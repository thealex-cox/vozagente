"""Recursos que comparten la demo por navegador y el puente telefónico.

Los dos frentes corren en el mismo proceso a propósito: el modelo de Whisper pesa
~140 MB y tarda en cargar, así que tener una instancia por cada uno duplicaría la
memoria y el arranque.
"""

import asyncio
import logging
import os

from voz_agente.proveedores.stt_whisper import WhisperLocalSTT

logger = logging.getLogger(__name__)

# Whisper en CPU no puede con dos transcripciones a la vez, y además el idioma se
# fija en el propio reconocedor: sin este cerrojo, una llamada en inglés y otra en
# español se pisarían el atributo mutuamente.
TURNO_LOCK = asyncio.Lock()

_stt = None


def motor_stt():
    """Deepgram si hay clave, Whisper local si no.

    Se elige solo, igual que el TTS: no son motores de la misma liga sobre audio
    telefónico —Whisper está entrenado con audio limpio y a 8 kHz confunde nombres
    propios— así que pedir una variable extra para preferir el bueno sólo serviría
    para olvidarse de ponerla. `VOZ_MOTOR_STT` fuerza uno concreto.
    """
    explicito = os.environ.get('VOZ_MOTOR_STT', '').strip().lower()
    if explicito:
        return explicito
    return 'deepgram' if os.environ.get('DEEPGRAM_API_KEY') else 'whisper'


def stt_compartido():
    global _stt
    if _stt is None:
        if motor_stt() == 'deepgram':
            from voz_agente.proveedores.stt_deepgram import DeepgramSTT
            _stt = DeepgramSTT()
            if not _stt.esta_disponible():
                logger.warning('Se pidio Deepgram pero falta DEEPGRAM_API_KEY; se usa Whisper')
                _stt = WhisperLocalSTT(modelo=os.environ.get('VOZ_MODELO_STT', 'base'))
        else:
            _stt = WhisperLocalSTT(modelo=os.environ.get('VOZ_MODELO_STT', 'base'))
        logger.info(f'Reconocimiento de voz: {_stt.nombre}')
    return _stt
