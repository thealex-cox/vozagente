"""Demo del agente de voz por navegador, sin telefonía.

El cliente abre una página, habla por el micrófono y oye al agente. No hay carrier,
ni número, ni permisos geográficos de por medio: sirve para juzgar al agente —guion,
voz, si suena humano— antes de construir el puente de audio de la llamada real.

    uvicorn voz_agente.servidor_demo:app --host 0.0.0.0 --port 8600

`getUserMedia` sólo funciona sobre HTTPS o en localhost, así que para enseñárselo al
cliente hay que publicarlo detrás del proxy con TLS (ver README).
"""

import asyncio
import base64
import logging
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from nucleo import ajustes, negocio as negocio_cfg
from voz_agente import config
from voz_agente.agente import MODELO_CLAUDE, AgenteVoz
from nucleo import almacen
from nucleo.herramientas import (EjecutorHerramientas,
                                 nombres_activos as herramientas_activas)
from voz_agente.bitacora import Bitacora
from voz_agente.puente_twilio import router as router_twilio, token_esperado
from voz_agente.recursos import TURNO_LOCK, motor_stt, stt_compartido

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

CARPETA_ESTATICOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'estatico')

# El mismo proceso sirve la demo por navegador (/ws) y el puente telefónico
# (/twilio): comparten el modelo de Whisper, que tarda en cargar y ocupa ~140 MB.
app = FastAPI(title='Servicio de voz')
app.mount('/estatico', StaticFiles(directory=CARPETA_ESTATICOS), name='estatico')
app.include_router(router_twilio)


def construir_agente(idioma, contexto='', herramientas=None):
    """El agente de la demo por navegador.

    `prompt_negocio` va vacío y toda la identidad entra por `contexto`, igual que en
    el camino telefónico. Es lo que permite retocar el guion en `config.json` y que
    la conversación siguiente ya lo use, sin reiniciar el servicio.

    Lleva herramientas, al contrario que la demo de skytech: allí no tenía ficha en
    la base contra la que ejecutarlas, y aquí sí — cada conversación del navegador se
    registra como una llamada más. Es lo que permite enseñar el producto entero sin
    gastar telefonía: se le pide algo y el pedido aparece en el panel.
    """
    return AgenteVoz(
        stt=stt_compartido(),
        tts=config.construir_tts(idioma=idioma),
        prompt_negocio='',
        modelo=config.modelo_claude() or MODELO_CLAUDE,
        contexto=contexto,
        herramientas=herramientas,
    )


def negocio_cfg_actual():
    """El negocio tal como está ahora mismo en `config.json`.

    Se relee en vez de cachearse: el guion se retoca hasta el último minuto y una
    caché aquí obligaría a reiniciar el servicio para oír el cambio.
    """
    return negocio_cfg_datos(ajustes.cargar(recargar=True))


def negocio_cfg_datos(config_json):
    return ajustes.negocio(config_json)


@app.get('/')
async def pagina():
    return FileResponse(os.path.join(CARPETA_ESTATICOS, 'demo.html'))


@app.get('/salud')
async def salud():
    """Estado del servicio de un vistazo.

    Nació de que el fallo típico de esto no es una excepción sino una llamada que
    suena y sale muda, y averiguar por qué costaba una llamada por intento.
    """
    bloqueos, telefonia, avisos = ajustes.revisar()
    datos_negocio = negocio_cfg_actual()
    datos = {
        'negocio': negocio_cfg.nombre(datos_negocio),
        'idioma': negocio_cfg.idioma(datos_negocio),
        'api_key': config.hay_api_key(),
        'modelo': config.modelo_claude() or MODELO_CLAUDE,
        # Sin token el puente rechaza el audio y la llamada sale muda. Se publica si
        # está o no, nunca su valor.
        'stream_token': bool(token_esperado()),
        'bloqueos': bloqueos,
        'telefonia': telefonia,
        'avisos': avisos,
        # `listo` es «el agente puede hablar», no «puede
        # telefonear»: la demo por navegador no necesita telefonia.
        'listo': not bloqueos,
        'listo_telefono': not (bloqueos or telefonia),
    }
    datos.update(config.info_tts())
    # El reconocedor también: es la mitad de la calidad de la llamada y sin esto
    # sólo se sabía cuál estaba activo mirando los logs después de hablar, porque se
    # instancia de forma perezosa en el primer turno.
    datos['stt'] = motor_stt()
    return datos


@app.websocket('/ws')
async def conversacion(ws: WebSocket):
    await ws.accept()

    if not config.hay_api_key():
        await ws.send_json({
            'tipo': 'error',
            'mensaje': 'Falta la variable de entorno ANTHROPIC_API_KEY en el servidor.',
        })
        await ws.close()
        return

    # El negocio se relee de `config.json` en cada conexión, no se cachea: se
    # retoca el guion, se recarga la página y ya se oye el cambio. Es lo que se hace
    # veinte veces mientras se afina, y cachearlo obligaría a reiniciar cada vez.
    try:
        datos_negocio = negocio_cfg_actual()
    except Exception as ex:
        await ws.send_json({'tipo': 'error', 'mensaje': f'Configuracion invalida: {ex}'})
        await ws.close()
        return

    if not negocio_cfg.nombre(datos_negocio):
        # Sin nombre el agente no sabe quién es y contestaría como un asistente
        # genérico. En una demo delante de un cliente eso parece un producto a
        # medias, que es peor que un error claro.
        await ws.send_json({
            'tipo': 'error',
            'mensaje': 'El negocio no tiene nombre en config.json: revisa negocio.nombre.',
        })
        await ws.close()
        return

    # La demo no tiene dirección: quien la abre habla primero, como en una entrante.
    contexto = negocio_cfg.contexto(datos_negocio, 'entrante')
    saludo = negocio_cfg.saludo(datos_negocio, 'entrante')
    idioma = negocio_cfg.idioma(datos_negocio)
    if idioma not in config.VOCES:
        idioma = config.IDIOMA_POR_DEFECTO

    # La conversación por navegador se registra igual que una llamada: es material
    # de prueba tan válido como el telefónico y se revisa en el mismo sitio.
    llamada_id = almacen.crear_llamada(
        direccion='demo', telefono='navegador',
        estado='en curso', carrier='demo', es_prueba=True)
    bitacora = Bitacora(llamada_id, sesion=f'demo-{int(time.time())}')
    herramientas = EjecutorHerramientas(
        llamada_id, activas=herramientas_activas(datos_negocio))

    agente = construir_agente(idioma, contexto, herramientas)
    carpeta = tempfile.mkdtemp(prefix='voz_demo_')
    logger.info(
        f'Conexion nueva | negocio={negocio_cfg.nombre(datos_negocio)} '
        f'| idioma={idioma} | contexto={len(contexto)} chars '
        f'| llamada={llamada_id} | temp={carpeta}'
    )
    await ws.send_json({
        'tipo': 'enlace',
        'llamada': str(llamada_id or ''),
        'activo': False,
        # Para que quien abre la demo vea a quién va a oír antes de hablar.
        'negocio': negocio_cfg.nombre(datos_negocio),
        'saludo': saludo,
        'idioma': idioma,
    })

    try:
        while True:
            mensaje = await ws.receive()

            if mensaje.get('type') == 'websocket.disconnect':
                break

            if mensaje.get('text'):
                import json
                datos = json.loads(mensaje['text'])
                if datos.get('tipo') == 'config':
                    nuevo = datos.get('idioma', idioma)
                    if nuevo != idioma:
                        idioma = nuevo
                        # Se reconstruye el agente: cambia la voz y se descarta el
                        # historial, que estaba en el otro idioma. Las herramientas
                        # se conservan: no dependen del idioma y su cliente HTTP ya
                        # tiene la conexión abierta. El negocio también, o cambiar de
                        # idioma a media demo devolvería al agente de Skytech.
                        agente = construir_agente(idioma, contexto, herramientas)
                        logger.info(f'Idioma cambiado a {idioma}')
                    await ws.send_json({'tipo': 'listo', 'idioma': idioma})
                continue

            audio = mensaje.get('bytes')
            if not audio:
                continue

            async with TURNO_LOCK:
                await procesar_turno(ws, agente, audio, carpeta, idioma, bitacora,
                                     vocabulario=negocio_cfg.vocabulario(datos_negocio))

    except WebSocketDisconnect:
        logger.info('Conexion cerrada por el navegador')
    except Exception as ex:
        logger.exception(f'Error en la conversacion: {ex}')
        try:
            await ws.send_json({'tipo': 'error', 'mensaje': str(ex)})
        except Exception:
            pass
    finally:
        await bitacora.cerrar()
        herramientas.cerrar()
        almacen.actualizar_llamada(llamada_id, estado='finalizada')
        shutil.rmtree(carpeta, ignore_errors=True)


async def procesar_turno(ws, agente, audio, carpeta, idioma, bitacora=None,
                         vocabulario=''):
    """Un turno completo: audio del cliente -> STT -> Claude -> TTS -> audio.

    El trabajo va a un hilo porque `AgenteVoz` es síncrono, y las frases se empujan
    al navegador por una cola conforme se sintetizan. Esperar a la respuesta entera
    antes de mandar nada tiraría por tierra el troceado en frases, que es justo lo
    que recorta el silencio que percibe quien escucha.
    """
    entrada = os.path.join(carpeta, f'entrada_{int(time.time() * 1000)}.webm')
    with open(entrada, 'wb') as archivo:
        archivo.write(audio)

    loop = asyncio.get_running_loop()
    cola = asyncio.Queue()
    inicio = time.time()

    # El reconocedor se comparte entre conexiones, asi que su idioma y su
    # vocabulario se fijan en cada turno: si no, una demo le dejaria puesto a la
    # siguiente el nombre de otro negocio. Sin el, el nombre propio que mas se va a
    # decir en toda la conversacion es justo el que peor se transcribe.
    stt = stt_compartido()
    stt.idioma = idioma
    stt.contexto = vocabulario

    def emitir(item):
        loop.call_soon_threadsafe(cola.put_nowait, item)

    def trabajo():
        indice = [0]
        primer_audio = []

        def al_completar_frase(frase):
            if not frase.strip():
                return
            destino = os.path.join(carpeta, f'r_{int(time.time() * 1000)}_{indice[0]}.{agente.tts.extension}')
            indice[0] += 1
            try:
                agente.hablar(frase, destino)
            except Exception as ex:
                emitir(('error', f'Fallo al sintetizar: {ex}', None))
                return
            if not primer_audio:
                primer_audio.append(time.time() - inicio)
            with open(destino, 'rb') as archivo:
                emitir(('audio', frase, base64.b64encode(archivo.read()).decode('ascii')))
            os.remove(destino)

        try:
            texto_usuario = agente.escuchar(entrada)
            emitir(('usuario', texto_usuario, time.time() - inicio))
            if not texto_usuario.strip():
                emitir(('fin', {'vacio': True}, None))
                return
            emitir(('estado', 'pensando', None))
            respuesta = agente.pensar(texto_usuario, al_completar_frase=al_completar_frase)
            # Se emite en vez de anotarse aquí porque la bitácora encola con
            # `asyncio.ensure_future`, y esto corre en un hilo del executor donde no
            # hay bucle de eventos. El consumidor sí está en el bucle.
            emitir(('respuesta', respuesta, primer_audio[0] if primer_audio else None))
            emitir(('fin', {
                'primer_audio': primer_audio[0] if primer_audio else None,
                'ttft': getattr(agente, 'ultimo_ttft', None),
                'total': time.time() - inicio,
            }, None))
        except Exception as ex:
            logger.exception(f'Fallo el turno: {ex}')
            emitir(('error', str(ex), None))
            emitir(('fin', {}, None))
        finally:
            try:
                os.remove(entrada)
            except OSError:
                pass

    tarea = loop.run_in_executor(None, trabajo)

    while True:
        clase, dato, extra = await cola.get()
        if clase == 'usuario':
            if bitacora is not None:
                bitacora.anotar('cliente', dato)
            await ws.send_json({'tipo': 'usuario', 'texto': dato, 'stt': round(extra, 2)})
        elif clase == 'respuesta':
            if bitacora is not None:
                bitacora.anotar('agente', dato, latencia_ms=int((extra or 0) * 1000))
        elif clase == 'estado':
            await ws.send_json({'tipo': 'estado', 'valor': dato})
        elif clase == 'audio':
            # El formato sale del motor, no fijo: sólo edge-tts devuelve MP3. Cartesia
            # y Piper devuelven WAV, y Cartesia es el que se elige solo en cuanto hay
            # clave —o sea, la configuración de producción—, así que anunciarlo como
            # MP3 le entregaba al navegador un WAV mal etiquetado.
            extension = getattr(agente.tts, 'extension', 'mp3')
            await ws.send_json({
                'tipo': 'audio', 'texto': dato,
                'mime': 'audio/mpeg' if extension == 'mp3' else f'audio/{extension}',
                'audio_b64': extra,
            })
        elif clase == 'error':
            await ws.send_json({'tipo': 'error', 'mensaje': dato})
        elif clase == 'fin':
            await ws.send_json({'tipo': 'fin', 'metricas': dato or {}})
            break

    await tarea
