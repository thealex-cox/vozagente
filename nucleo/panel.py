"""El panel: configurar el negocio, ver las llamadas y marcar.

**Este panel NO puede quedar expuesto al mundo.** No tiene contraseña, enseña las
claves de los proveedores y desde él se hacen llamadas que cuestan dinero. Y el
riesgo no es teórico: para recibir llamadas hay que publicar este mismo servicio con
un túnel (`cloudflared tunnel --url http://localhost:8600`), y un túnel expone el
**puerto entero** — webhooks y panel por igual.

Por eso cada ruta de aquí pasa por `_solo_local`, que **mira la cabecera `Host`**, no
la IP de origen. La IP no sirve: cloudflared se conecta desde la propia máquina, así
que a través del túnel la petición llega igualmente de 127.0.0.1. Lo que sí cambia es
el `Host` — `localhost:8600` cuando se abre en el navegador de esta máquina, y
`loquesea.trycloudflare.com` cuando viene por el túnel.
"""

import hmac
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from nucleo import acceso, ajustes, almacen, carrier as carrier_mod, telefono

logger = logging.getLogger(__name__)
router = APIRouter()

CARPETA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'estatico')

def _solo_local(request):
    """Devuelve None si se puede seguir, o la respuesta de rechazo.

    Desde esta máquina se entra siempre; desde fuera hace falta la clave de acceso.
    Es la misma regla que aplica el middleware de `nucleo/acceso.py`, repetida aquí a
    conciencia: si alguien monta este router en otra aplicación sin el middleware, lo
    que queda expuesto son las claves de los proveedores y el botón de marcar, y ese
    no es un fallo que convenga descubrir después.
    """
    if acceso.host_local(request.headers.get('host')):
        return None

    clave = ajustes.clave_acceso()
    recibida = (request.query_params.get('k')
                or request.cookies.get(acceso.COOKIE) or '')
    if clave and recibida and hmac.compare_digest(recibida, clave):
        return None

    logger.warning(
        f'Panel rechazado: llego con Host={request.headers.get("host")!r} '
        f'{"sin clave" if not recibida else "con clave incorrecta"}.')
    return JSONResponse(
        {'error': True,
         'mensaje': 'Para abrir el panel desde fuera hace falta la clave de acceso. '
                    'Se pone en el propio panel, en Claves y telefonia.'},
        status_code=403)


@router.get('/panel')
async def panel(request: Request):
    rechazo = _solo_local(request)
    if rechazo is not None:
        return rechazo
    return FileResponse(os.path.join(CARPETA, 'panel.html'))


@router.get('/api/config')
async def leer_config(request: Request):
    """La configuración tal cual, claves incluidas.

    Se sirven en claro a propósito: el panel tiene que poder enseñarlas y copiarlas,
    y viven en un `config.json` de texto plano en esta misma máquina, así que no hay
    secreto que proteger aquí que no esté ya al alcance de quien abre la carpeta. Lo
    que protege esto es `_solo_local`, no el ocultarlas.
    """
    rechazo = _solo_local(request)
    if rechazo is not None:
        return rechazo

    config = ajustes.cargar(recargar=True)
    bloqueos, telefonia, avisos = ajustes.revisar(config)
    return JSONResponse({
        'error': False,
        'config': config,
        'bloqueos': bloqueos,
        'telefonia': telefonia,
        'avisos': avisos,
        'ruta': ajustes.RUTA_CONFIG,
        'enlaces': ajustes.enlaces_compartibles(config),
    })


@router.post('/api/config')
async def escribir_config(request: Request):
    rechazo = _solo_local(request)
    if rechazo is not None:
        return rechazo

    try:
        cambios = await request.json()
    except Exception as ex:
        return JSONResponse({'error': True, 'mensaje': f'JSON invalido: {ex}'},
                            status_code=400)
    if not isinstance(cambios, dict):
        return JSONResponse({'error': True, 'mensaje': 'Se esperaba un objeto'},
                            status_code=400)

    try:
        config = ajustes.guardar(cambios)
    except Exception as ex:
        logger.exception('No se pudo guardar la configuracion')
        return JSONResponse({'error': True, 'mensaje': f'No se pudo guardar: {ex}'},
                            status_code=500)

    bloqueos, telefonia, avisos = ajustes.revisar(config)
    # Se avisa de lo que necesita reinicio para no dejar al usuario creyendo que un
    # cambio ya esta activo cuando no lo esta. El guion y las claves entran solos;
    # el idioma no, porque la voz se elige al construir el agente.
    return JSONResponse({'error': False, 'bloqueos': bloqueos,
                         'telefonia': telefonia, 'avisos': avisos})


@router.get('/api/llamadas')
async def listar(request: Request):
    rechazo = _solo_local(request)
    if rechazo is not None:
        return rechazo

    filas = almacen.listar_llamadas(100)
    for fila in filas:
        fila['turnos'] = len(almacen.turnos(fila['id']))
    return JSONResponse({'error': False, 'llamadas': filas})


@router.get('/api/llamadas/{llamada_id}')
async def detalle(llamada_id: int, request: Request):
    rechazo = _solo_local(request)
    if rechazo is not None:
        return rechazo

    llamada = almacen.llamada(llamada_id)
    if llamada is None:
        return JSONResponse({'error': True, 'mensaje': 'No existe esa llamada'},
                            status_code=404)
    return JSONResponse({
        'error': False,
        'llamada': llamada,
        'turnos': almacen.turnos(llamada_id),
        'eventos': almacen.eventos(llamada_id),
    })


@router.post('/api/llamar')
async def llamar(request: Request):
    """Marca al número que se escriba. Cuesta dinero, así que comprueba antes.

    No mira la franja horaria —igual que la consola de pruebas de skytech, y por lo
    mismo: pulsar este botón es una decisión humana explícita sobre un número escrito
    a mano—, pero **sí respeta la lista de no-llamar**, que no admite excepciones.
    """
    rechazo = _solo_local(request)
    if rechazo is not None:
        return rechazo

    try:
        cuerpo = await request.json()
    except Exception:
        cuerpo = {}

    config = ajustes.cargar(recargar=True)
    pais_defecto = str(ajustes.llamadas(config).get('pais_defecto') or '593')
    numero = telefono.normalizar(str(cuerpo.get('telefono', '')), pais_defecto)
    if not numero:
        return JSONResponse(
            {'error': True, 'mensaje': 'Ese numero no se puede marcar. Escribelo con '
                                       'el prefijo del pais, por ejemplo +593999123456.'},
            status_code=400)

    if almacen.esta_bloqueado(numero):
        return JSONResponse(
            {'error': True, 'mensaje': f'{numero} esta en la lista de no-llamar.'},
            status_code=400)

    carrier = carrier_mod.construir(ajustes.carrier(config))
    if carrier is None:
        return JSONResponse(
            {'error': True, 'mensaje': 'No hay proveedor configurado: revisa la '
                                       'seccion de telefonia.'},
            status_code=400)

    base = ajustes.url_publica(config)
    if not base:
        return JSONResponse(
            {'error': True, 'mensaje': 'Falta la URL publica: el proveedor no sabria '
                                       'a donde pedir las instrucciones y la llamada '
                                       'saldria muda.'},
            status_code=400)

    llamada_id = almacen.crear_llamada(
        direccion='saliente', telefono=numero, pais=telefono.pais(numero),
        carrier=carrier.nombre, estado='marcando', es_prueba=True)

    permitido, motivo = telefono.puede_llamar(numero, ajustes.llamadas(config))
    if not permitido:
        # Queda anotado aunque se marque igual: el dia que alguien reclame por una
        # llamada a deshora hay que poder ver que salio de aqui y con que motivo.
        almacen.anotar_evento(llamada_id, 'error',
                              f'Franja horaria ignorada desde el panel: {motivo}')

    token = ajustes.token(config)
    try:
        resultado = carrier.iniciar_llamada(
            numero,
            url_webhook=f'{base}/twiml/{llamada_id}?t={token}',
            url_estado=f'{base}/estado/{llamada_id}?t={token}',
        )
    except carrier_mod.CarrierError as ex:
        almacen.actualizar_llamada(llamada_id, estado='fallida', error=str(ex)[:500])
        almacen.anotar_evento(llamada_id, 'error', str(ex))
        # El motivo del proveedor se pasa tal cual: «To number is not routeable» dice
        # exactamente que pasa, y reescribirlo borra la unica pista util.
        return JSONResponse({'error': True, 'mensaje': f'El proveedor rechazo la '
                                                       f'llamada: {ex}'},
                            status_code=400)

    almacen.actualizar_llamada(llamada_id, carrier_call_id=resultado.call_id)
    almacen.anotar_evento(llamada_id, 'dialed', f'Marcada por {carrier.nombre}')
    return JSONResponse({'error': False, 'id': llamada_id,
                         'aviso': '' if permitido else motivo})


@router.post('/api/no-llamar')
async def no_llamar(request: Request):
    """Añade un número a la lista de no-llamar. Es la única lista sin excepciones."""
    rechazo = _solo_local(request)
    if rechazo is not None:
        return rechazo

    try:
        cuerpo = await request.json()
    except Exception:
        cuerpo = {}

    config = ajustes.cargar()
    pais_defecto = str(ajustes.llamadas(config).get('pais_defecto') or '593')
    numero = telefono.normalizar(str(cuerpo.get('telefono', '')), pais_defecto)
    if not numero:
        return JSONResponse({'error': True, 'mensaje': 'Ese numero no es utilizable.'},
                            status_code=400)

    almacen.bloquear(numero, str(cuerpo.get('motivo', ''))[:255])
    return JSONResponse({'error': False, 'telefono': numero})


@router.get('/api/no-llamar')
async def listar_no_llamar(request: Request):
    rechazo = _solo_local(request)
    if rechazo is not None:
        return rechazo
    return JSONResponse({'error': False, 'numeros': almacen.bloqueados()})


@router.get('/api/pedidos')
async def listar_pedidos(request: Request):
    rechazo = _solo_local(request)
    if rechazo is not None:
        return rechazo
    return JSONResponse({'error': False,
                         'pedidos': almacen.listar_pedidos(200),
                         'pendientes': almacen.pedidos_pendientes()})


@router.get('/api/vivo')
async def vivo(request: Request):
    """Lo que el panel refresca solo: pedidos y la llamada que esta sonando.

    Una sola ruta y no tres, porque el navegador la pide cada pocos segundos: tres
    sondeos serian tres conexiones a la base por vuelta, y esos milisegundos salen
    del turno de la llamada que se esta atendiendo.
    """
    rechazo = _solo_local(request)
    if rechazo is not None:
        return rechazo
    return JSONResponse({'error': False, **almacen.panorama_vivo()})


@router.get('/api/pedidos/{pedido_id}')
async def detalle_pedido(pedido_id: int, request: Request):
    """Un pedido con la conversacion de la que salio.

    La transcripcion va con el pedido porque la pregunta que se hace quien lo mira
    es «¿esto es lo que el cliente dijo de verdad?», y la respuesta es la llamada.
    """
    rechazo = _solo_local(request)
    if rechazo is not None:
        return rechazo

    datos = almacen.pedido(pedido_id)
    if datos is None:
        return JSONResponse({'error': True, 'mensaje': 'No existe ese pedido'},
                            status_code=404)
    llamada_id = datos.get('llamada_id')
    return JSONResponse({
        'error': False,
        'pedido': datos,
        'llamada': almacen.llamada(llamada_id) if llamada_id else None,
        'turnos': almacen.turnos(llamada_id) if llamada_id else [],
    })


@router.post('/api/pedidos/{pedido_id}')
async def atender_pedido(pedido_id: int, request: Request):
    """Marca un pedido como atendido, o lo devuelve a pendiente.

    Con vuelta atras a proposito: el agente escucha por telefono y va a apuntar algun
    falso positivo, asi que hay que poder deshacerlo sin borrar la fila — la fila es
    la evidencia de lo que se dijo en la llamada.
    """
    rechazo = _solo_local(request)
    if rechazo is not None:
        return rechazo

    try:
        cuerpo = await request.json()
    except Exception:
        cuerpo = {}
    almacen.marcar_pedido(pedido_id, bool(cuerpo.get('atendido', True)))
    return JSONResponse({'error': False, 'pendientes': almacen.pedidos_pendientes()})
