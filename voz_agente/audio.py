"""Conversión de audio entre la telefonía y el agente.

La telefonía y el agente no hablan el mismo formato y no hay término medio: Twilio
manda y espera μ-law a 8 kHz en tramas de 20 ms, mientras que Whisper quiere un
fichero y edge-tts devuelve MP3 a 24 kHz. Todo lo que traduce entre esos dos mundos
vive aquí, sin dependencias de red ni de framework, para poder probarlo suelto.

`audioop` es stdlib y hace el trabajo pesado en C. Ojo: **desapareció en Python
3.13**; este proceso corre en 3.10 y si algún día se sube de versión hay que
sustituirlo (audioop-lts o numpy a mano).
"""

import audioop
import io
import wave

TASA_TELEFONIA = 8000
ANCHO_MUESTRA = 2
MILISEGUNDOS_TRAMA = 20
# 20 ms a 8 kHz = 160 muestras. En μ-law es 1 byte por muestra; en PCM16 son 2.
MUESTRAS_TRAMA = TASA_TELEFONIA * MILISEGUNDOS_TRAMA // 1000
BYTES_TRAMA_MULAW = MUESTRAS_TRAMA
BYTES_TRAMA_PCM = MUESTRAS_TRAMA * ANCHO_MUESTRA


def mulaw_a_pcm(datos):
    """μ-law 8 kHz -> PCM lineal 16 bits, misma frecuencia."""
    return audioop.ulaw2lin(datos, ANCHO_MUESTRA)


def pcm_a_mulaw(pcm):
    """PCM lineal 16 bits -> μ-law. La entrada ya debe estar a 8 kHz."""
    return audioop.lin2ulaw(pcm, ANCHO_MUESTRA)


def remuestrear(pcm, tasa_origen, tasa_destino, estado=None):
    """Devuelve (pcm, estado). El estado encadena llamadas sucesivas.

    Hay que conservarlo entre tramas: `ratecv` guarda ahí el resto del filtro, y
    reiniciarlo en cada trama mete un chasquido audible en cada frontera.
    """
    if tasa_origen == tasa_destino:
        return pcm, estado
    return audioop.ratecv(pcm, ANCHO_MUESTRA, 1, tasa_origen, tasa_destino, estado)


def nivel_rms(pcm):
    """Energía de la trama, 0..32767. Es la base de la detección de voz."""
    if not pcm:
        return 0
    return audioop.rms(pcm, ANCHO_MUESTRA)


def trocear(datos, tamano):
    """Parte en trozos de tamaño fijo y descarta la cola incompleta.

    Twilio espera tramas completas: una trama corta al final se oye como un clic.
    """
    return [datos[i:i + tamano] for i in range(0, len(datos) - tamano + 1, tamano)]


SILENCIO_MULAW = b'\xff'


def tramas_desde_flujo(trozos, tamano=BYTES_TRAMA_MULAW, relleno=SILENCIO_MULAW):
    """Convierte un flujo de audio de tamaño arbitrario en tramas completas.

    Un TTS en streaming devuelve los trozos que le convienen, no múltiplos de 20 ms.
    Aquí se acumula el resto entre trozos y sólo se emiten tramas completas; la cola
    final se rellena con silencio en lugar de mandarse corta, porque una trama a
    medias se oye como un clic.
    """
    resto = b''
    for trozo in trozos:
        if not trozo:
            continue
        datos = resto + trozo
        corte = len(datos) - (len(datos) % tamano)
        for i in range(0, corte, tamano):
            yield datos[i:i + tamano]
        resto = datos[corte:]
    if resto:
        yield resto + relleno * (tamano - len(resto))


def escribir_wav(pcm, ruta, tasa=TASA_TELEFONIA):
    """Vuelca PCM crudo a un WAV mono, que es lo que sabe leer el STT."""
    with wave.open(str(ruta), 'wb') as archivo:
        archivo.setnchannels(1)
        archivo.setsampwidth(ANCHO_MUESTRA)
        archivo.setframerate(tasa)
        archivo.writeframes(pcm)
    return ruta


def wav_en_memoria(pcm, tasa=TASA_TELEFONIA):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as archivo:
        archivo.setnchannels(1)
        archivo.setsampwidth(ANCHO_MUESTRA)
        archivo.setframerate(tasa)
        archivo.writeframes(pcm)
    return buffer.getvalue()


def archivo_a_pcm(ruta, tasa_destino=TASA_TELEFONIA):
    """Decodifica cualquier audio a PCM16 mono en la frecuencia pedida.

    Se usa PyAV en vez de invocar a ffmpeg: ya viene con faster-whisper, así que no
    añade dependencias ni obliga a tener un binario en el PATH del servidor.
    """
    import av

    contenedor = av.open(str(ruta))
    try:
        remuestreador = av.AudioResampler(format='s16', layout='mono', rate=tasa_destino)
        trozos = []

        def acumular(salida):
            # PyAV >= 9 devuelve una lista de tramas; las versiones viejas, una sola.
            if salida is None:
                return
            for trama in (salida if isinstance(salida, list) else [salida]):
                if trama is not None:
                    trozos.append(trama.to_ndarray().tobytes())

        for trama in contenedor.decode(audio=0):
            acumular(remuestreador.resample(trama))
        acumular(remuestreador.resample(None))
        return b''.join(trozos)
    finally:
        contenedor.close()


def archivo_a_tramas_mulaw(ruta):
    """De un fichero del TTS a la lista de tramas que espera Twilio."""
    pcm = archivo_a_pcm(ruta, TASA_TELEFONIA)
    return trocear(pcm_a_mulaw(pcm), BYTES_TRAMA_MULAW)


def duracion_segundos(pcm, tasa=TASA_TELEFONIA):
    return len(pcm) / (ANCHO_MUESTRA * tasa)
