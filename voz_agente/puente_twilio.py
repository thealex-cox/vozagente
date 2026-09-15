"""Puente entre Twilio Media Streams y el agente de voz.

Twilio abre un WebSocket contra este endpoint cuando el TwiML devuelve
`<Connect><Stream>`, y a partir de ahí manda el audio del interlocutor en μ-law a
8 kHz, en tramas de 20 ms codificadas en base64. Se le contesta por el mismo socket
con el mismo formato.

Tres eventos del protocolo hacen el trabajo fino:

- `mark`: se envía detrás del último trozo de audio y Twilio lo devuelve cuando ha
  terminado de reproducirlo. Es la única forma de saber que el agente acabó de
  hablar, porque enviar el audio no es reproducirlo: Twilio lo almacena.
- `clear`: descarta lo que quede en ese búfer. Es lo que permite cortar al agente en
  seco cuando el cliente le habla encima.
- `customParameters` del evento `start`: el único sitio donde se puede colar un
  token. Twilio **no firma** las conexiones WebSocket como sí firma los webhooks
  HTTP, así que sin esto cualquiera que descubra la URL puede abrir conversaciones
  y gastar saldo de API.
"""

import asyncio
import base64
import json
import logging
import os
import queue
import shutil
import tempfile
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from voz_agente import audio, config
from voz_agente.agente import MODELO_CLAUDE, AgenteVoz
from voz_agente.bitacora import Bitacora
from voz_agente.herramientas import EjecutorHerramientas
from voz_agente.recursos import TURNO_LOCK, stt_compartido

logger = logging.getLogger(__name__)
router = APIRouter()

# Umbrales de detección de voz sobre el RMS de la trama (escala 0..32767). El audio
# telefónico trae ruido de línea constante, así que el silencio no es cero.
UMBRAL_VOZ = int(os.environ.get('VOZ_UMBRAL', '500'))
# Para interrumpir se exige bastante más energía: por el canal de vuelta se cuela
# eco de la propia voz del agente, y con el mismo umbral se cortaría a sí mismo.
UMBRAL_INTERRUPCION = int(os.environ.get('VOZ_UMBRAL_INTERRUPCION', '1800'))
# Informe periódico de los niveles que llegan, para calibrar los umbrales de arriba
# contra una llamada real en vez de contra tonos generados.
DEBUG_NIVEL = os.environ.get('VOZ_DEBUG_NIVEL', '') not in ('', '0')
TRAMAS_POR_INFORME = 100
# Colchón sobre la duración calculada del audio antes de dar por terminado el turno
# del agente. Cubre el retardo de red de la primera trama; pasarse acorta el silencio
# tras el que el interlocutor puede hablar, quedarse corto le corta la última sílaba.
MARGEN_FIN_AUDIO = float(os.environ.get('VOZ_MARGEN_FIN_AUDIO', '0.4'))
# Segundos de audio que se permiten ir por delante de la reproducción. Ver
# `enviar_tramas`: es lo que impide que SignalWire descarte el resto del turno.
ADELANTO_AUDIO = float(os.environ.get('VOZ_ADELANTO_AUDIO', '0.5'))

MS_TRAMA = audio.MILISEGUNDOS_TRAMA
# Silencio tras el que se da por terminado el turno del interlocutor. Ajustable sin
# tocar código porque es el número que hay que probar contra una línea de verdad:
# corto se le corta a media frase a quien duda —«quisiera, este...»— y largo alarga
# la espera de todas las respuestas. Los demás umbrales de aquí ya eran ajustables;
# éste se quedó fijo y es justo el que se quiere mover durante una prueba.
MS_SILENCIO_FIN = int(os.environ.get('VOZ_SILENCIO_FIN_MS', '800'))
# Voz mínima para dar un turno por bueno. Bajado de 300 ms tras perder una respuesta
# real: un «yes» o un «speaking» duran menos, y en una llamada de confirmación ésa es
# justo la respuesta que se espera. Lo que descarta esto es un golpe o un chasquido de
# línea, y para eso 180 ms siguen sobrando.
MS_MINIMO_VOZ = int(os.environ.get('VOZ_MINIMO_VOZ_MS', '180'))
MS_MINIMO_INTERRUPCION = 400
MS_MAXIMO_TURNO = 30000

# Tramas que se agrupan por envío: 10 x 20 ms = 200 ms de audio. Con un TTS en
# streaming el primer lote sale mucho antes de que la frase esté sintetizada, que es
# de donde viene la ganancia de latencia. Agrupar evita un mensaje por cada 20 ms.
TRAMAS_POR_LOTE = 10


def lotes_de_tramas(tts, texto, tamano=TRAMAS_POR_LOTE):
    """Del texto a lotes de tramas listas para Twilio, según van saliendo del TTS.

    Con Cartesia el audio ya viene en μ-law a 8 kHz y sólo hay que trocearlo. Con un
    proveedor sin streaming, `transmitir()` cae a sintetizar el fichero entero y
    devolverlo de una: funciona igual, pero sin ganancia.
    """
    lote = []
    for trama in audio.tramas_desde_flujo(tts.transmitir(texto)):
        lote.append(trama)
        if len(lote) >= tamano:
            yield lote
            lote = []
    if lote:
        yield lote

# Respaldo por si el saludo no llega en el TwiML. **No nombra a ningun negocio**:
# el nombre lo compone `nucleo/negocio.py` y viaja como <Parameter>, y una frase
# fija con una marca dentro es exactamente como se acaba saludando con el nombre
# equivocado. El aviso de que es automatico se mantiene aunque se recorte todo lo
# demas — es lo unico que no puede faltar en la primera frase.
#
# Cortos a proposito: por telefono, cada segundo de saludo es un segundo en que quien
# ha descolgado no sabe si le habla alguien.
SALUDOS = {
    'es': 'Hola, le atiende un asistente automatico. ¿En que puedo ayudarle?',
    'en': 'Hello, you are speaking with an automated assistant. How can I help you?',
}


def saludo_de(idioma, direccion, personalizado=''):
    """La primera frase. Normalmente llega ya redactada en el TwiML.

    La compone `nucleo/negocio.py`, que es quien sabe como se llama el negocio y si
    la llamada entra o sale. Esto es solo la red de seguridad.
    """
    if personalizado.strip():
        return personalizado.strip()
    return SALUDOS.get(idioma, SALUDOS['es'])


def token_esperado():
    return os.environ.get('VOZ_STREAM_TOKEN', '')


class EstadoLlamada:
    """Todo lo que hay que recordar mientras dura una llamada."""

    def __init__(self, stream_sid, call_sid, llamada_id, idioma, contexto='',
                 telefono=''):
        self.stream_sid = stream_sid
        self.call_sid = call_sid
        self.llamada_id = llamada_id
        self.idioma = idioma
        self.carpeta = tempfile.mkdtemp(prefix='voz_tel_')
        # El telefono se le pasa al ejecutor y NO viaja como parametro de la
        # herramienta: al otro lado hay un desconocido dictandole cosas al agente, y
        # un numero por parametro dejaria que cualquiera apunte un pedido a nombre
        # de otro. Lo sabe el puente, asi que lo pone el puente.
        self.herramientas = EjecutorHerramientas(llamada_id, telefono=telefono)
        # `prompt_negocio` va vacío a propósito: aquí la identidad del negocio no es
        # un fichero que se carga al arrancar, sino el `contexto` que llega en cada
        # llamada. Así se retoca el guion y la llamada siguiente ya lo usa, sin
        # reiniciar el servicio. Lo compone `nucleo/negocio.py`.
        self.agente = AgenteVoz(
            stt=stt_compartido(),
            tts=config.construir_tts(idioma=idioma),
            prompt_negocio='',
            modelo=config.modelo_claude() or MODELO_CLAUDE,
            contexto=contexto,
            herramientas=self.herramientas,
        )
        self.buffer = bytearray()
        # Lo que esta llamada lleva consumido de los proveedores de pago. Se cuenta
        # aquí y no en Django porque aquí es donde ocurre: el audio que se manda a
        # transcribir y los caracteres que se sintetizan no dejan rastro del otro
        # lado, y estimarlos desde la transcripción es exactamente lo que esto evita.
        self.segundos_stt = 0.0
        self.caracteres_tts = 0
        # Muestreo de niveles para calibrar el VAD. Los umbrales se fijaron con tonos
        # sintéticos, y una línea telefónica no se parece: sin poder ver qué niveles
        # llegan de verdad, un umbral mal puesto se manifiesta como un agente que
        # simplemente no contesta, que es indistinguible de que no llegue el audio.
        self.niveles = []
        self.tramas_recibidas = 0
        # Cuánto audio se lleva enviado en el turno actual y cuándo empezó a salir.
        # Con eso se sabe cuándo habrá terminado de sonar sin que el carrier lo diga.
        self.tramas_turno = 0
        self.inicio_envio = None
        # Tramas que salieron más tarde de lo que tocaba, y cuánto. Es la medida de
        # si el TTS aguanta el tiempo real: si no, el audio sale a goteo.
        self.tramas_tarde = 0
        self.retraso_max = 0.0
        self.envio_total = 0.0
        self.envio_max = 0.0
        self.envios_lentos = 0
        self.espera_cola_total = 0.0
        self.espera_cola_max = 0.0
        # Vocabulario esperado de esta llamada, para el reconocedor. Lo rellena
        # `arrancar()` con lo que manda Django.
        self.vocabulario = ''
        self.capturando = False
        self.ms_voz = 0
        self.ms_total = 0
        self.ms_silencio = 0
        self.ms_interrupcion = 0
        self.hablando_agente = False
        self.bitacora = Bitacora(llamada_id, sesion=stream_sid)
        # Frases del turno que el agente está diciendo ahora mismo. Se juntan y se
        # registran de una vez al cerrarlo: ver `cerrar_turno_agente`.
        self.frases_agente = []
        # Se incrementa en cada interrupción. La tarea que está enviando audio
        # compara contra ella antes de cada trama: si no coincide, lo suyo ya no
        # vale y deja de enviar. Es la forma de "cancelar" un trabajo que en parte
        # corre en un hilo y no se puede matar.
        self.generacion = 0
        # Distinto de `generacion`: ésta sólo cambia al interrumpir, y el agente
        # encadena turnos sin que nadie le interrumpa —el cierre son dos: la frase de
        # despedida y el «que tenga buen día»—. Sin identidad propia de turno, la
        # tarea de `liberar_por_tiempo` del turno anterior se despierta mientras el
        # siguiente todavía suena, lo da por terminado y cuelga a media frase.
        self.turno = 0
        # Lo ponemos nosotros al colgar. Sirve para no registrar como fallo del puente
        # el error que suelta `receive_text` sobre un WebSocket que acabamos de cerrar.
        self.colgado = False
        self.tarea = None
        # Tarea que manda silencio mientras nadie habla. Ver `mantener_vivo`.
        self.latido = None
        # Abre la conexión con el modelo mientras suena el saludo. Ver `arrancar`.
        self.precalentado = None
        # Momento de la última trama enviada, sea voz o relleno. Es lo que decide si
        # hace falta rellenar, y no puede ser una bandera: ver `mantener_vivo`.
        self.ultimo_envio = time.time()

    def limpiar(self):
        self.herramientas.cerrar()
        shutil.rmtree(self.carpeta, ignore_errors=True)

    def turno_en_curso(self):
        return self.tarea is not None and not self.tarea.done()

    def nuevo_turno(self):
        """Abre un turno de habla y devuelve su número, para que las tareas que
        quedan corriendo sepan si lo que vigilaban sigue siendo lo actual."""
        self.turno += 1
        self.tramas_turno = 0
        self.inicio_envio = None
        self.tramas_tarde = 0
        self.retraso_max = 0.0
        self.envio_total = 0.0
        self.envio_max = 0.0
        self.envios_lentos = 0
        self.espera_cola_total = 0.0
        self.espera_cola_max = 0.0
        return self.turno


@router.websocket('/twilio')
async def media_stream(ws: WebSocket):
    await ws.accept()
    estado = None

    try:
        while True:
            datos = json.loads(await ws.receive_text())
            evento = datos.get('event')

            if DEBUG_NIVEL and evento != 'media':
                logger.info('evento del carrier: %s %s', evento,
                            json.dumps({k: v for k, v in datos.items()
                                        if k not in ('media', 'event')})[:120])

            if evento == 'start':
                estado = await arrancar(ws, datos)
                if estado is None:
                    return
            elif evento == 'media' and estado is not None:
                await entra_audio(ws, estado, datos)
            elif evento == 'mark' and estado is not None:
                marca_reproducida(estado, datos)
                # Cuando el carrier sí devuelve la marca, éste es el instante exacto
                # en que la despedida acabó de sonar. `liberar_por_tiempo` cuelga
                # igual si no llega, pero unos cientos de ms más tarde.
                await colgar_si_termino(ws, estado)
                if estado.colgado:
                    break
            elif evento == 'stop':
                break

            if estado is not None and estado.colgado:
                break

    except WebSocketDisconnect:
        logger.info('Twilio cerro el stream')
    except Exception as ex:
        # Colgamos nosotros: el error es de leer sobre un socket ya cerrado, y
        # registrarlo como fallo del puente manda a buscar una avería que no existe.
        if estado is not None and estado.colgado:
            logger.info('Stream cerrado tras colgar la llamada')
        else:
            logger.exception(f'Fallo el puente de audio: {ex}')
    finally:
        if estado is not None:
            if estado.latido is not None:
                estado.latido.cancel()
            # Si la llamada se cae antes de que la conexión termine de abrirse, el
            # resultado ya no le importa a nadie; sin recogerlo, asyncio lo denuncia
            # al recolectar la tarea y ensucia el log con un fallo que no lo es.
            if estado.precalentado is not None:
                estado.precalentado.cancel()
            if estado.turno_en_curso():
                estado.tarea.cancel()
            # Lo que el agente estaba diciendo cuando se cortó también se registra:
            # colgar a mitad de una frase es justo el dato que explica una queja.
            cerrar_turno_agente(estado)
            reportar_consumo(estado)
            await estado.bitacora.cerrar()
            logger.info(f'Llamada {estado.call_sid} finalizada')
            estado.limpiar()


# Proveedores que facturan. Whisper y piper corren en esta misma máquina y no
# cobran nada, así que anotarles consumo sólo llenaría el panel de ceros.
PROVEEDORES_DE_PAGO = ('deepgram', 'cartesia')


def reportar_consumo(estado):
    """Deja en la bitácora lo que la llamada gastó con cada proveedor.

    Se llama al colgar, cuando los tres contadores ya son definitivos. Lo que sale
    de aquí son unidades medidas —segundos de audio, caracteres, tokens—, no
    estimaciones. Aqui se guardan las unidades tal cual y sin precio: este producto
    todavia no tiene tabla de tarifas, asi que ponerle un importe seria inventarselo.
    Cuando la haya, el sitio es `nucleo/almacen.py`, no esto.
    """
    consumos = []
    try:
        nombre_stt = getattr(getattr(estado.agente, 'stt', None), 'nombre', '')
        if nombre_stt in PROVEEDORES_DE_PAGO and estado.segundos_stt > 0:
            consumos.append({
                'proveedor': nombre_stt,
                'unidad': 'minuto',
                'unidades': round(estado.segundos_stt / 60, 4),
                'detalle': 'Audio sent to be transcribed',
            })

        nombre_tts = getattr(getattr(estado.agente, 'tts', None), 'nombre', '')
        if nombre_tts in PROVEEDORES_DE_PAGO and estado.caracteres_tts > 0:
            consumos.append({
                'proveedor': nombre_tts,
                'unidad': 'caracter',
                'unidades': estado.caracteres_tts,
                'detalle': 'Characters synthesised, greetings included',
            })

        uso = getattr(estado.agente, 'uso', None) or {}
        # La escritura de caché se suma a la entrada en lugar de llevar unidad
        # propia: se factura algo más cara, pero inventar una unidad que la tabla de
        # tarifas no contempla dejaría esos tokens sin precio ninguno.
        entrada = int(uso.get('entrada', 0)) + int(uso.get('cache_escritura', 0))
        for unidades, unidad, detalle in (
            (entrada, 'token_entrada', 'Prompt tokens, cache writes included'),
            (int(uso.get('salida', 0)), 'token_salida', 'Tokens the model generated'),
            (int(uso.get('cache_lectura', 0)), 'token_cache', 'Prompt tokens served from cache'),
        ):
            if unidades > 0:
                consumos.append({
                    'proveedor': 'anthropic', 'unidad': unidad,
                    'unidades': unidades, 'detalle': detalle,
                })

        estado.bitacora.anotar_consumo(consumos)
    except Exception as ex:
        # Contabilidad: que falle no puede impedir que la llamada se cierre bien.
        logger.warning(f'[{estado.call_sid}] no se pudo reportar el consumo: {ex}')


def cerrar_turno_agente(estado, latencia_ms=0, interrumpido=False):
    """Cierra la intervención del agente y la deja en la bitácora.

    Las frases se acumulan y se registran juntas: trocear la respuesta es un truco
    del audio para empezar a hablar antes de tenerla entera, pero en la transcripción
    una respuesta es una respuesta. Vaciar el búfer aquí hace que llamar dos veces —
    lo que pasa cuando una interrupción y el fin de turno se cruzan — no duplique.
    """
    if not estado.frases_agente:
        return
    texto = ' '.join(estado.frases_agente)
    estado.frases_agente = []
    estado.bitacora.anotar(
        'agente', texto, latencia_ms=latencia_ms, interrumpido=interrumpido,
    )


async def arrancar(ws, datos):
    """Valida el token y prepara la llamada. Devuelve None si hay que rechazarla."""
    inicio = datos.get('start', {})
    parametros = inicio.get('customParameters', {}) or {}
    # Twilio ha cambiado el uso de mayúsculas en estas claves entre versiones.
    recibido = parametros.get('token') or parametros.get('Token') or ''
    esperado = token_esperado()

    if not esperado:
        logger.error('VOZ_STREAM_TOKEN no esta configurado: se rechaza el stream')
        await ws.close(code=1008)
        return None
    if recibido != esperado:
        logger.warning(f"Stream rechazado para {inicio.get('callSid')}: token invalido")
        await ws.close(code=1008)
        return None

    idioma = parametros.get('idioma') or config.IDIOMA_POR_DEFECTO
    contexto = parametros.get('contexto') or ''
    direccion = parametros.get('direccion') or 'saliente'
    saludo = parametros.get('saludo') or ''
    # Éste no va al modelo sino al reconocedor de voz: le adelanta el nombre de quien
    # contesta y el servicio cotizado. Ver `vocabulario_llamada` en Django.
    vocabulario = parametros.get('vocabulario') or ''
    estado = EstadoLlamada(
        stream_sid=datos.get('streamSid') or inicio.get('streamSid', ''),
        call_sid=inicio.get('callSid', ''),
        llamada_id=parametros.get('llamada', ''),
        idioma=idioma if idioma in config.VOCES else config.IDIOMA_POR_DEFECTO,
        contexto=contexto,
        telefono=parametros.get('telefono') or '',
    )
    estado.vocabulario = vocabulario
    logger.info(
        f'Llamada {estado.call_sid} conectada | llamada_id={estado.llamada_id} '
        f'| idioma={estado.idioma} | direccion={direccion} '
        f'| contexto={"si" if contexto else "no"} '
        f'| vocabulario={"si" if vocabulario else "no"}'
    )

    # El agente habla primero: en una llamada saliente el silencio inicial hace que
    # el interlocutor cuelgue. El saludo lleva incorporado el aviso de que es una IA,
    # que en el camino de streaming no lo da nadie más — el <Say> del TwiML sólo se
    # reproduce cuando NO hay puente.
    estado.tarea = asyncio.create_task(
        hablar(ws, estado,
               saludo_de(estado.idioma, direccion, saludo), registrar=True)
    )
    # Mientras suena el saludo se abre la conexión con el modelo. Es tiempo regalado:
    # el cliente está escuchando, no esperando respuesta. Sin esto, lo que sólo pasa
    # una vez —importar la librería, DNS, TLS— se pagaba en el primer turno, con el
    # cliente ya callado: 13,3 s medidos en una llamada real, contra 1,0 s del turno
    # siguiente. Va en un hilo porque el cliente del modelo es síncrono y aquí
    # bloquear el bucle es dejar de mover el audio.
    estado.precalentado = asyncio.ensure_future(
        asyncio.get_event_loop().run_in_executor(None, estado.agente.precalentar))
    estado.latido = asyncio.create_task(mantener_vivo(ws, estado))
    return estado


async def entra_audio(ws, estado, datos):
    """Una trama de 20 ms del interlocutor."""
    carga = datos.get('media', {}).get('payload')
    if not carga:
        return

    pcm = audio.mulaw_a_pcm(base64.b64decode(carga))
    nivel = audio.nivel_rms(pcm)

    if DEBUG_NIVEL:
        estado.tramas_recibidas += 1
        estado.niveles.append(nivel)
        if len(estado.niveles) >= TRAMAS_POR_INFORME:
            picos = sorted(estado.niveles)
            logger.info(
                '[%s] niveles de %d tramas (%.1fs): min=%d mediana=%d p90=%d max=%d '
                '| umbral voz=%d, superadas=%d',
                estado.stream_sid[:8], len(picos), len(picos) * MS_TRAMA / 1000.0,
                picos[0], picos[len(picos) // 2], picos[int(len(picos) * 0.9)], picos[-1],
                UMBRAL_VOZ, sum(1 for n in picos if n > UMBRAL_VOZ),
            )
            estado.niveles.clear()

    if estado.hablando_agente:
        # Mientras el agente habla sólo se busca una interrupción. No se acumula
        # nada: el búfer se llenaría con el eco del propio agente.
        if nivel > UMBRAL_INTERRUPCION:
            estado.ms_interrupcion += MS_TRAMA
            if estado.ms_interrupcion >= MS_MINIMO_INTERRUPCION:
                await interrumpir(ws, estado)
        else:
            estado.ms_interrupcion = 0
        return

    if estado.turno_en_curso():
        return

    if nivel > UMBRAL_VOZ:
        if not estado.capturando:
            estado.capturando = True
            estado.buffer.clear()
            estado.ms_voz = 0
            estado.ms_total = 0
        estado.ms_voz += MS_TRAMA
        estado.ms_silencio = 0
    elif estado.capturando:
        estado.ms_silencio += MS_TRAMA

    if not estado.capturando:
        return

    estado.buffer.extend(pcm)
    estado.ms_total += MS_TRAMA

    fin_por_silencio = estado.ms_silencio >= MS_SILENCIO_FIN
    fin_por_limite = estado.ms_total >= MS_MAXIMO_TURNO
    if not (fin_por_silencio or fin_por_limite):
        return

    pcm_turno = bytes(estado.buffer)
    ms_voz_turno = estado.ms_voz
    voz_suficiente = ms_voz_turno >= MS_MINIMO_VOZ
    estado.capturando = False
    estado.buffer.clear()
    estado.ms_silencio = 0

    if not voz_suficiente:
        # Un golpe, una tos o un chasquido de línea. Transcribirlo gasta CPU para
        # devolver ruido, y el agente acabaría contestando a nada.
        #
        # Se registra porque desde fuera esto es indistinguible de que el agente no
        # oiga: el interlocutor habla, no pasa nada, y no queda rastro de por qué.
        logger.info('[%s] turno descartado: %d ms de voz, hacen falta %d',
                    estado.call_sid, ms_voz_turno, MS_MINIMO_VOZ)
        return

    estado.tarea = asyncio.create_task(responder(ws, estado, pcm_turno))


async def responder(ws, estado, pcm):
    """Turno completo: audio del interlocutor -> STT -> Claude -> TTS -> audio."""
    generacion = estado.generacion
    ruta = os.path.join(estado.carpeta, f'entrada_{int(time.time() * 1000)}.wav')
    audio.escribir_wav(pcm, ruta)
    # El reconocedor cobra por audio enviado, no por llamada: se cuenta lo que
    # realmente se le manda, que es sólo lo que el cliente habló.
    estado.segundos_stt += len(pcm) / (audio.TASA_TELEFONIA * audio.ANCHO_MUESTRA)
    loop = asyncio.get_running_loop()
    inicio = time.time()

    try:
        # El cerrojo cubre sólo la transcripción, que es lo que satura la CPU y lo
        # que comparte el atributo `idioma`. Mantenerlo durante el resto del turno
        # dejaría a las demás llamadas esperando a que ésta terminara de hablar.
        async with TURNO_LOCK:
            stt = stt_compartido()
            stt.idioma = estado.idioma
            # El reconocedor se comparte entre llamadas, así que su vocabulario se
            # fija aquí dentro igual que el idioma: si no, una llamada le pondría a
            # otra el nombre del cliente equivocado.
            stt.contexto = estado.vocabulario
            texto = await loop.run_in_executor(None, estado.agente.escuchar, ruta)
    finally:
        try:
            os.remove(ruta)
        except OSError:
            pass

    if not texto.strip() or generacion != estado.generacion:
        return

    logger.info(f'[{estado.call_sid}] cliente: {texto}')
    estado.bitacora.anotar('cliente', texto)

    cola = asyncio.Queue()

    def emitir(item):
        loop.call_soon_threadsafe(cola.put_nowait, item)

    # Dos etapas en hilos distintos, y ésta es la razón: sintetizar dentro del
    # callback de Claude bloqueaba la lectura del stream, así que mientras Cartesia
    # trabajaba no llegaba texto nuevo y viceversa. Medido en llamada real: el envío
    # se quedaba sin audio 3 s por turno, con huecos de hasta 1.9 s, y eso es
    # silencio en mitad de una frase. Ningún componente era lento por separado —
    # Cartesia entrega a 5x tiempo real— lo que fallaba era ponerlos en serie.
    cola_texto = queue.Queue()

    def sintetizador():
        try:
            while True:
                frase = cola_texto.get()
                if frase is None:
                    break
                if generacion != estado.generacion:
                    continue
                # Sin nada pronunciable no se llama al TTS: el modelo emite emojis
                # sueltos como frase —el prompt de Sky está escrito para chat y los
                # permite— y Cartesia rechaza la petición entera con «invalid input».
                # Visto en llamada real: un 🎙️ dejó muda esa intervención.
                if not any(caracter.isalnum() for caracter in frase):
                    logger.info('[%s] frase sin nada que pronunciar, se omite: %r',
                                estado.call_sid, frase[:20])
                    continue
                estado.caracteres_tts += len(frase)
                try:
                    for lote in lotes_de_tramas(estado.agente.tts, frase):
                        if generacion != estado.generacion:
                            break
                        emitir(('tramas', None, lote))
                except Exception as ex:
                    logger.error(f'[{estado.call_sid}] fallo el TTS: {ex}')
        finally:
            # El turno acaba cuando se acaba el AUDIO, no cuando Claude deja de
            # escribir: por eso el 'fin' lo emite esta etapa y no la otra.
            emitir(('fin', None, None))

    def trabajo():
        def al_completar_frase(frase):
            if not frase.strip() or generacion != estado.generacion:
                return
            emitir(('frase', frase, None))
            # Sólo encolar: que este callback no tarde es lo que permite que Claude
            # siga generando mientras suena lo anterior.
            cola_texto.put(frase)

        try:
            estado.agente.pensar(texto, al_completar_frase=al_completar_frase)
        except Exception as ex:
            logger.exception(f'[{estado.call_sid}] fallo Claude: {ex}')
        finally:
            # Centinela: sin él el hilo sintetizador se queda esperando para siempre.
            cola_texto.put(None)

    loop.run_in_executor(None, trabajo)
    loop.run_in_executor(None, sintetizador)

    estado.hablando_agente = True
    turno = estado.nuevo_turno()
    primer_audio = None
    while True:
        # Cuánto se espera aquí es la medida directa de si el productor va justo:
        # si el consumidor se queda parado esperando audio mientras el agente está
        # hablando, ese tiempo es silencio en la línea.
        espera_cola = time.time()
        clase, frase, tramas = await cola.get()
        parado = time.time() - espera_cola
        # Sólo cuenta si ya había empezado a salir audio: esperar antes de la primera
        # trama del turno no es un hueco en la voz, es la latencia de respuesta, y
        # mezclarlas hacía parecer que había cortes donde sólo había una pausa.
        if estado.inicio_envio is not None and parado > 0.05:
            estado.espera_cola_total += parado
            estado.espera_cola_max = max(estado.espera_cola_max, parado)
        if generacion != estado.generacion:
            return
        if clase == 'fin':
            break
        if clase == 'frase':
            logger.info(f'[{estado.call_sid}] agente: {frase}')
            estado.frases_agente.append(frase)
            continue
        if primer_audio is None:
            primer_audio = time.time() - inicio
        await enviar_tramas(ws, estado, tramas, generacion)

    if generacion == estado.generacion:
        cerrar_turno_agente(estado, latencia_ms=int((primer_audio or 0) * 1000))
        await marcar_fin(ws, estado, generacion, turno)
    if primer_audio is not None:
        logger.info(f'[{estado.call_sid}] primer audio {primer_audio:.2f}s')


async def hablar(ws, estado, texto, registrar=False):
    """Sintetiza y envía una frase fija, sin pasar por Claude (saludos, avisos).

    Va por el mismo camino en streaming que las respuestas: el saludo es lo primero
    que oye el interlocutor y esperar a tenerlo entero antes de empezar es
    exactamente el silencio inicial que hace colgar.
    """
    generacion = estado.generacion
    loop = asyncio.get_running_loop()
    cola = asyncio.Queue()

    estado.caracteres_tts += len(texto)

    def trabajo():
        try:
            for lote in lotes_de_tramas(estado.agente.tts, texto):
                if generacion != estado.generacion:
                    break
                loop.call_soon_threadsafe(cola.put_nowait, lote)
        except Exception as ex:
            logger.error(f'[{estado.call_sid}] no se pudo sintetizar "{texto[:40]}": {ex}')
        finally:
            loop.call_soon_threadsafe(cola.put_nowait, None)

    if registrar:
        logger.info(f'[{estado.call_sid}] agente: {texto}')
        estado.frases_agente.append(texto)
    estado.hablando_agente = True
    turno = estado.nuevo_turno()
    inicio = time.time()
    primer_audio = None
    loop.run_in_executor(None, trabajo)

    while True:
        lote = await cola.get()
        if lote is None:
            break
        if generacion != estado.generacion:
            return
        if primer_audio is None:
            primer_audio = time.time() - inicio
        await enviar_tramas(ws, estado, lote, generacion)

    if generacion == estado.generacion:
        cerrar_turno_agente(estado, latencia_ms=int((primer_audio or 0) * 1000))
        await marcar_fin(ws, estado, generacion, turno)


async def enviar_tramas(ws, estado, tramas, generacion):
    """Envía el audio al ritmo al que se reproduce, manteniendo un pequeño adelanto.

    Twilio almacena todo lo que se le manda y lo reproduce a ritmo real, así que con
    él se podía volcar el turno entero de golpe. **SignalWire no**: se queda con lo
    que le cabe y descarta el resto, y el síntoma es un saludo que se corta a mitad de
    palabra —verificado en llamada real, se oían unos 3 s de los 9,7 enviados—.

    El adelanto es el compromiso entre las dos formas de fallar: sin nada de colchón
    un tirón de red deja un hueco audible, y con demasiado volvemos a perder audio y a
    tardar en obedecer una interrupción, porque lo ya entregado sigue sonando.
    """
    for trama in tramas:
        if generacion != estado.generacion:
            return
        if estado.inicio_envio is None:
            estado.inicio_envio = time.time()

        # Instante en que sonará esta trama, menos el adelanto que se le permite.
        objetivo = (estado.inicio_envio
                    + estado.tramas_turno * MS_TRAMA / 1000.0
                    - ADELANTO_AUDIO)
        espera = objetivo - time.time()
        if espera > 0:
            await asyncio.sleep(espera)
            if generacion != estado.generacion:
                return
        elif estado.tramas_turno * MS_TRAMA / 1000.0 > ADELANTO_AUDIO:
            # Vamos tarde: la trama tenía que haber salido y todavía no la teníamos,
            # así que el audio no se está generando al ritmo al que se consume y el
            # búfer del carrier se queda seco. Eso se oye como voz entrecortada.
            #
            # La condición excluye las primeras tramas del turno: el colchón permite
            # mandar `ADELANTO_AUDIO` de audio de golpe, así que su objetivo es
            # anterior al inicio del envío y salen "tarde" por definición. Sin este
            # filtro, todo turno arrancaba con 25 falsos positivos.
            estado.tramas_tarde += 1
            estado.retraso_max = max(estado.retraso_max, -espera)

        estado.tramas_turno += 1
        # Se cronometra el envío porque son 50 mensajes por segundo y, si cada uno
        # tarda más de los 20 ms que dura la trama, el retraso se acumula y la voz
        # se parte. Es la única forma de separar «el audio no se generó a tiempo»
        # de «el audio no se pudo entregar a tiempo».
        antes = time.time()
        await ws.send_text(json.dumps({
            'event': 'media',
            'streamSid': estado.stream_sid,
            'media': {'payload': base64.b64encode(trama).decode('ascii')},
        }))
        tardo = time.time() - antes
        estado.ultimo_envio = time.time()
        estado.envio_total += tardo
        estado.envio_max = max(estado.envio_max, tardo)
        if tardo > 0.02:
            estado.envios_lentos += 1


async def mantener_vivo(ws, estado):
    """Manda silencio mientras el agente no habla, para que el carrier no cuelgue.

    SignalWire da el stream por terminado si dejamos de enviarle audio: medido en
    llamadas reales, cierra entre 5 y 6 segundos después de la última trama. Las
    llamadas de confirmación no lo destapaban porque el agente cuelga él mismo en
    cuanto registra el resultado; las de prueba, que se quedan esperando a que el
    interlocutor hable, se cortaban solas a mitad de conversación.

    Una llamada telefónica nunca está en silencio de verdad —hay ruido de línea—, así
    que enviar silencio explícito es lo que un teléfono hace de todas formas.
    """
    trama = audio.SILENCIO_MULAW * audio.BYTES_TRAMA_MULAW
    carga = base64.b64encode(trama).decode('ascii')
    try:
        while True:
            await asyncio.sleep(0.25)
            if estado.colgado:
                continue
            # La condición es el tiempo desde la última trama de voz, no una bandera
            # de «está hablando»: esa bandera se apaga mientras todavía queda audio
            # sonando, y entonces el relleno se intercalaba con la voz y la
            # destrozaba. Con un segundo de margen, si hay voz saliendo esto no llega
            # a dispararse nunca.
            if time.time() - estado.ultimo_envio < 1.0:
                continue
            await ws.send_text(json.dumps({
                'event': 'media',
                'streamSid': estado.stream_sid,
                'media': {'payload': carga},
            }))
            estado.ultimo_envio = time.time()
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.info('[%s] fin del silencio de mantenimiento: %s', estado.call_sid, ex)


async def marcar_fin(ws, estado, generacion, turno):
    if estado.tramas_turno:
        medio = estado.envio_total / estado.tramas_turno
        logger.info('[%s] envio: %d tramas, medio %.1f ms, peor %.0f ms, '
                    '%d por encima de 20 ms (%.0f%%)',
                    estado.call_sid, estado.tramas_turno, medio * 1000,
                    estado.envio_max * 1000, estado.envios_lentos,
                    100.0 * estado.envios_lentos / estado.tramas_turno)
    if estado.tramas_tarde:
        # Se registra siempre, no sólo en modo depuración: es la diferencia entre
        # «la voz se oye mal» y saber por qué, y sólo se puede medir aquí.
        logger.info('[%s] audio a goteo: %d de %d tramas salieron tarde '
                    '(hasta %.2fs de retraso)',
                    estado.call_sid, estado.tramas_tarde, estado.tramas_turno,
                    estado.retraso_max)
    if estado.espera_cola_total:
        logger.info('[%s] el envio se quedo sin audio %.2fs en total, '
                    'el peor hueco %.2fs: el TTS no va por delante',
                    estado.call_sid, estado.espera_cola_total, estado.espera_cola_max)
    await ws.send_text(json.dumps({
        'event': 'mark',
        'streamSid': estado.stream_sid,
        # El turno va en el nombre porque la marca puede llegar tarde: sin él, la del
        # turno anterior daría por terminado el que esté sonando.
        'mark': {'name': f'fin-{generacion}-{turno}'},
    }))
    asyncio.create_task(liberar_por_tiempo(ws, estado, generacion, turno))


async def liberar_por_tiempo(ws, estado, generacion, turno):
    """Devuelve la palabra al interlocutor cuando el audio ya ha tenido tiempo de sonar.

    Hace falta porque **SignalWire no devuelve el evento `mark`** —verificado en
    llamadas reales: sólo manda `connected`, `start` y `stop`—, y sin él
    `hablando_agente` se queda en True para siempre y el puente no vuelve a escuchar
    nunca. El síntoma es un agente que saluda y enmudece.

    La cuenta es fiable porque el audio va a ritmo fijo: cada trama son 20 ms, así que
    el final de la reproducción es el instante en que salió la primera más la duración
    total. El margen cubre el retardo de red del primer paquete.

    No sustituye a `mark`: si el carrier lo manda —Twilio sí—, libera antes y esto no
    llega a hacer nada.
    """
    if turno != estado.turno or estado.inicio_envio is None:
        return
    duracion = estado.tramas_turno * MS_TRAMA / 1000.0
    ahora = time.time()
    # Si el TTS fue más lento que el tiempo real —edge-tts lo es—, las tramas salieron
    # a goteo y el búfer del carrier se vació por el camino: entonces la reproducción
    # acaba con el último envío, no a `inicio + duración`. Se toma el mayor de los dos
    # para no devolver la palabra mientras el agente todavía se oye.
    fin_estimado = max(estado.inicio_envio + duracion, ahora)
    restante = fin_estimado + MARGEN_FIN_AUDIO - ahora
    if restante > 0:
        await asyncio.sleep(restante)

    # Se vuelve a comprobar el turno: mientras dormíamos ha podido empezar otro, y
    # entonces lo que acaba de sonar no es lo que esta tarea vigilaba.
    if generacion != estado.generacion or turno != estado.turno:
        return

    if estado.hablando_agente:
        logger.info('[%s] fin de audio por tiempo (%.1fs); el carrier no devolvio la marca',
                    estado.call_sid, duracion)
        estado.hablando_agente = False
        estado.ms_interrupcion = 0
        estado.tramas_turno = 0
        estado.inicio_envio = None

    await colgar_si_termino(ws, estado)


async def colgar_si_termino(ws, estado):
    """Cierra la llamada cuando ya no queda nada que hacer en ella.

    Sin esto la línea se queda abierta después de la despedida y el agente entra en un
    intercambio de cortesías —«gracias» / «de nada, adiós»— que no acaba nunca, porque
    ninguno de los dos lados cuelga. Visto en una llamada real.

    Se espera a que el audio haya terminado de sonar: cortar antes se lleva por delante
    la despedida, y colgarle a alguien a media frase es peor que la llamada de más.

    Cerrar el WebSocket basta para colgar: el `<Connect>` del TwiML dura lo que dure el
    stream, y no hay nada detrás.
    """
    if not estado.herramientas.conversacion_terminada or estado.hablando_agente:
        return

    # Enviado no es reproducido: el audio sale con hasta `ADELANTO_AUDIO` de ventaja
    # sobre lo que se oye, y cerrar el WebSocket descarta lo que el carrier tenga en
    # el búfer. Sin esta espera se pierde el final de la despedida —medido: la frase
    # de cierre se cortaba tras ~1,3 s de los ~4 que dura—.
    await asyncio.sleep(ADELANTO_AUDIO + MARGEN_FIN_AUDIO)

    # Otro turno pudo arrancar durante la espera; entonces la conversación no estaba
    # terminada y colgar ahora sería el mismo fallo que esto viene a corregir.
    if estado.hablando_agente:
        return

    logger.info('[%s] colgando: la llamada ya registro su resultado', estado.call_sid)
    estado.colgado = True
    try:
        await ws.close()
    except Exception:
        # Si ya se cerró por el otro lado no hay nada que hacer, y desde luego no
        # tumbar el cierre ordenado del turno por ello.
        pass


def marca_reproducida(estado, datos):
    """Twilio devuelve la marca cuando terminó de reproducir hasta ese punto.

    SignalWire no la devuelve nunca; para ese caso está `liberar_por_tiempo`.
    """
    nombre = datos.get('mark', {}).get('name', '')
    if nombre == f'fin-{estado.generacion}-{estado.turno}':
        estado.hablando_agente = False
        estado.ms_interrupcion = 0
        estado.tramas_turno = 0
        estado.inicio_envio = None


async def interrumpir(ws, estado):
    """El interlocutor habló encima: se tira el audio pendiente y se le cede la voz."""
    logger.info(f'[{estado.call_sid}] interrumpido por el interlocutor')
    # Primero se invalida la generación y sólo después se cierra el turno: así el
    # resto del turno ya no entra al búfer y lo que se registra es exactamente lo
    # que el agente alcanzó a decir.
    estado.generacion += 1
    cerrar_turno_agente(estado, interrumpido=True)
    # Y si el turno ya se había reportado —el agente terminó de mandar el audio pero
    # Twilio seguía reproduciéndolo—, se corrige la anotación que ya salió.
    estado.bitacora.marcar_interrumpido()
    estado.hablando_agente = False
    estado.ms_interrupcion = 0
    estado.capturando = False
    estado.buffer.clear()

    # Se cancela el turno además de invalidarlo. Sin esto la corrutina se queda
    # dormida en `await cola.get()` hasta que el hilo de Claude termine —lo que puede
    # tardar segundos, más si hay una herramienta de por medio—, y mientras tanto
    # `turno_en_curso()` sigue siendo cierto y `entra_audio` descarta todo lo que
    # llega. O sea que justo lo que dice el interlocutor al interrumpir, que es lo
    # que más importa, era lo único que no se escuchaba.
    #
    # El hilo no se puede matar y sigue hasta acabar, pero eso ya estaba resuelto:
    # compara la generación antes de emitir nada, así que su salida se descarta.
    # Se suelta la referencia además de cancelar: `cancel()` sólo pide la cancelación
    # y `done()` sigue siendo falso hasta que el bucle la procesa, así que sin esto
    # quedaría una ventana de unas cuantas tramas en la que se seguiría descartando
    # audio. La tarea recibe su CancelledError igual.
    if estado.turno_en_curso():
        estado.tarea.cancel()
    estado.tarea = None

    await ws.send_text(json.dumps({'event': 'clear', 'streamSid': estado.stream_sid}))
