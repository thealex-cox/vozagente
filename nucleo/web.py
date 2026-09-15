"""Los webhooks que el proveedor consulta durante una llamada, y el panel.

Esto es lo que en skytech daba Django. Aquí son cuatro rutas de FastAPI en el mismo
proceso que el agente, lo que quita de en medio el salto HTTP entre los dos, su token
y toda una clase de fallos en que el audio va bien y el registro se pierde.

**Son públicas por obligación**: las llama el proveedor desde fuera, sin sesión y sin
cookie. Lo único que separa una petición legítima de una inventada es el token de la
query (`?t=…`), por el motivo explicado en `nucleo/carrier.py`: SignalWire firma con
un secreto que la cuenta no expone, así que exigir firma dejaba muda toda llamada
suya. Ninguna vista hace nada antes de comprobarlo.
"""

import hmac
import logging
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from nucleo import ajustes, almacen, carrier as carrier_mod, negocio as negocio_cfg, telefono

logger = logging.getLogger(__name__)
router = APIRouter()


def _atributo(valor):
    """Escapa un valor para meterlo entre comillas en un atributo XML."""
    return escape(str(valor), {'"': '&quot;', "'": '&apos;'})


def _twiml(cuerpo):
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{cuerpo}</Response>',
        media_type='text/xml; charset=utf-8')


def _autorizado(request):
    """Comprueba el secreto que viaja en la URL del webhook.

    Sin token configurado se rechaza todo. Es deliberado: un servicio que atiende
    llamadas sin autenticar es un servicio que cualquiera puede hacer sonar a tu
    costa, y arrancar sin token ya sale como bloqueo en `/salud`.
    """
    esperado = ajustes.token()
    if not esperado:
        logger.error('No hay web.token configurado: se rechaza el webhook')
        return False
    return hmac.compare_digest(request.query_params.get('t', ''), esperado)


def _cuerpo_stream(llamada_id, datos_negocio, direccion, telefono=''):
    """El TwiML que entrega la llamada al agente, o '' si no hay puente.

    Los `<Parameter>` son el único canal para autenticar el WebSocket del audio: ni
    Twilio ni SignalWire lo firman como sí firman los webhooks HTTP. Llegan al puente
    dentro de `customParameters` del evento `start`.
    """
    url_stream = ajustes.url_stream()
    token = ajustes.token()
    if not url_stream or not token:
        return ''

    idioma = negocio_cfg.idioma(datos_negocio)
    parametros = (
        f'<Parameter name="token" value="{_atributo(token)}"/>'
        f'<Parameter name="llamada" value="{llamada_id}"/>'
        f'<Parameter name="idioma" value="{_atributo(idioma)}"/>'
        f'<Parameter name="direccion" value="{_atributo(direccion)}"/>'
        f'<Parameter name="contexto" '
        f'value="{_atributo(negocio_cfg.contexto(datos_negocio, direccion))}"/>'
    )
    # El numero de quien esta al telefono, para que un pedido quede atado a el. Va
    # como parametro y no como argumento de la herramienta: al otro lado hay un
    # desconocido dictandole cosas al agente.
    if telefono:
        parametros += f'<Parameter name="telefono" value="{_atributo(telefono)}"/>'
    # Para el reconocedor, no para el modelo: le adelanta los nombres propios que va
    # a oír. Sin esto el nombre del negocio es justo lo que peor transcribe.
    vocabulario = negocio_cfg.vocabulario(datos_negocio)
    if vocabulario:
        parametros += f'<Parameter name="vocabulario" value="{_atributo(vocabulario)}"/>'
    # La primera frase la dice el puente sin pasar por el modelo, para que salga al
    # instante: el silencio inicial es donde cuelgan.
    saludo = negocio_cfg.saludo(datos_negocio, direccion)
    if saludo:
        parametros += f'<Parameter name="saludo" value="{_atributo(saludo)}"/>'

    # <Connect> mantiene la llamada abierta mientras dure el WebSocket, al contrario
    # que <Start>, que seguiría con el resto del TwiML en paralelo.
    return f'<Connect><Stream url="{_atributo(url_stream)}">{parametros}</Stream></Connect>'


def _say_de_reserva(datos_negocio):
    idioma = negocio_cfg.idioma(datos_negocio)
    voz = 'en-US' if idioma == 'en' else 'es-MX'
    mensaje = negocio_cfg.mensaje_sin_agente(datos_negocio)
    return f'<Say language="{_atributo(voz)}">{escape(mensaje)}</Say><Hangup/>'


@router.post('/twiml/{llamada_id}')
async def twiml_saliente(llamada_id: int, request: Request):
    """Lo que se dice al descolgar una llamada que hicimos nosotros."""
    if not _autorizado(request):
        logger.warning(f'TwiML rechazado para la llamada {llamada_id}: token invalido')
        return Response(content='Invalid token', status_code=403)

    llamada = almacen.llamada(llamada_id)
    if llamada is None:
        return _twiml('<Say>This call is no longer available.</Say><Hangup/>')

    almacen.actualizar_llamada(llamada_id, estado='en curso')
    almacen.anotar_evento(llamada_id, 'answered', 'El destinatario descolgo')

    datos_negocio = ajustes.negocio(ajustes.cargar(recargar=True))
    cuerpo = _cuerpo_stream(llamada_id, datos_negocio, 'saliente',
                            telefono=llamada.get('telefono', ''))
    if not cuerpo:
        cuerpo = _say_de_reserva(datos_negocio)

    # El aviso de que hay una máquina al otro lado va en la primera frase por los dos
    # caminos —en el saludo del agente y en el <Say> de reserva—. Queda registrado
    # porque es lo que se enseña si alguien reclama.
    almacen.anotar_evento(llamada_id, 'disclosure', 'Aviso de asistente automatico')
    return _twiml(cuerpo)


@router.post('/entrante')
async def entrante(request: Request):
    """Atiende una llamada que entra al número del negocio.

    A diferencia de la saliente, aquí la llamada **no existe todavía**: se crea al
    vuelo con lo que manda el proveedor. Es idempotente por su identificador, así que
    un reintento del webhook no deja dos fichas de la misma llamada.
    """
    if not _autorizado(request):
        logger.warning('Llamada entrante rechazada: token invalido')
        return Response(content='Invalid token', status_code=403)

    formulario = await request.form()
    origen = telefono.normalizar(formulario.get('From', ''))
    call_sid = str(formulario.get('CallSid', ''))[:128]

    existente = almacen.llamada_por_sid(call_sid)
    if existente is not None:
        llamada_id = existente['id']
    else:
        llamada_id = almacen.crear_llamada(
            direccion='entrante', telefono=origen or 'desconocido',
            pais=telefono.pais(origen), estado='en curso',
            carrier='signalwire', carrier_call_id=call_sid)
        almacen.anotar_evento(llamada_id, 'answered',
                              f'Llamada entrante de {origen or "desconocido"}')
        # Que un número de la lista de no-llamar nos llame es lo que puede reabrir la
        # puerta, pero no se desbloquea solo: quien llama para quejarse quedaría
        # desbloqueado y volveríamos a llamarle. Se anota y lo decide una persona.
        if origen and almacen.esta_bloqueado(origen):
            almacen.anotar_evento(
                llamada_id, 'answered',
                'Este numero esta en la lista de no-llamar y nos ha llamado el')

    datos_negocio = ajustes.negocio(ajustes.cargar(recargar=True))
    cuerpo = _cuerpo_stream(llamada_id, datos_negocio, 'entrante',
                            telefono=origen)
    if not cuerpo:
        cuerpo = _say_de_reserva(datos_negocio)
    else:
        almacen.anotar_evento(llamada_id, 'disclosure', 'Aviso de asistente automatico')
    return _twiml(cuerpo)


@router.post('/estado/{llamada_id}')
async def estado(llamada_id: int, request: Request):
    """El proveedor avisa cada vez que la llamada cambia de estado."""
    if not _autorizado(request):
        return Response(content='Invalid token', status_code=403)

    formulario = await request.form()
    estado_carrier = str(formulario.get('CallStatus', ''))
    nuestro = carrier_mod.ESTADOS.get(estado_carrier)
    if nuestro is None:
        logger.info(f'Estado no contemplado en la llamada {llamada_id}: {estado_carrier!r}')
        return JSONResponse({'error': False})

    # **Sólo las columnas que esta vista toca.** Al colgar, esto y el último turno
    # del puente llegan a la vez, y un guardado completo devolvería la transcripción
    # a como estaba un segundo antes — borrando lo único que la llamada produce. Fue
    # un fallo real en skytech.
    campos = {'estado': nuestro}
    duracion = str(formulario.get('CallDuration', ''))
    if duracion.isdigit():
        campos['duracion_segundos'] = int(duracion)
    almacen.actualizar_llamada(llamada_id, **campos)
    almacen.anotar_evento(llamada_id, estado_carrier, 'Aviso del proveedor')
    return JSONResponse({'error': False})


@router.get('/llamadas')
async def listado():
    """Bitácora en JSON. Sin autenticar y por eso **sólo debe escucharse en local**:
    lleva teléfonos y transcripciones. Lo que se publica al mundo son las tres rutas
    de arriba y `/twilio`, no esto — ver el README."""
    filas = almacen.listar_llamadas(50)
    for fila in filas:
        fila['turnos'] = len(almacen.turnos(fila['id']))
    return JSONResponse({'llamadas': filas})


@router.get('/llamadas/{llamada_id}', response_class=HTMLResponse)
async def detalle(llamada_id: int):
    """La conversación de una llamada, legible de un vistazo.

    En HTML y no en JSON porque lo que se hace con esto es leerlo después de una
    llamada de prueba, y un JSON con saltos de línea escapados no se lee.
    """
    llamada = almacen.llamada(llamada_id)
    if llamada is None:
        return HTMLResponse('<p>No existe esa llamada.</p>', status_code=404)

    filas = []
    for turno in almacen.turnos(llamada_id):
        quien = 'Agente' if turno['rol'] == 'agente' else 'Cliente'
        corte = ' <em>(interrumpido)</em>' if turno['interrumpido'] else ''
        filas.append(f'<p><strong>{quien}:</strong> {escape(turno["texto"] or "")}{corte}</p>')

    eventos = ''.join(
        f'<li>{escape(e["creado_en"])} — {escape(e["tipo"])}: {escape(e["detalle"] or "")}</li>'
        for e in almacen.eventos(llamada_id))

    return HTMLResponse(f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Llamada {llamada_id}</title>
<style>
body {{ font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 46rem; padding: 0 1rem; }}
p {{ margin: .4rem 0; }} strong {{ color: #444; }}
ul {{ color: #666; font-size: .85em; }}
.cab {{ color: #666; border-bottom: 1px solid #ddd; padding-bottom: .5rem; }}
</style></head><body>
<h1>Llamada {llamada_id}</h1>
<p class="cab">{escape(llamada['direccion'])} · {escape(llamada['telefono'])} ·
{escape(llamada['estado'])} · {llamada['duracion_segundos']} s</p>
{''.join(filas) or '<p><em>Sin conversacion registrada.</em></p>'}
<h2>Eventos</h2><ul>{eventos}</ul>
</body></html>""")
