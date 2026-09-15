"""TTS local con Piper. Apache 2.0, corre en CPU y no cobra por carácter.

Suena por debajo de Cartesia o ElevenLabs, pero permite validar el flujo completo
sin contratar nada. Al pasar a producción se cambia la clase, no el agente.
"""

import logging
import wave

from voz_agente.proveedores.base import TTSBase

logger = logging.getLogger(__name__)


class PiperTTS(TTSBase):
    nombre = 'piper-local'
    sample_rate = 22050

    def __init__(self, ruta_voz):
        """ruta_voz apunta al .onnx de la voz (ver README para descargarla)."""
        self.ruta_voz = str(ruta_voz)
        self._voz = None

    def _cargar(self):
        if self._voz is None:
            from piper.voice import PiperVoice
            self._voz = PiperVoice.load(self.ruta_voz)
            self.sample_rate = self._voz.config.sample_rate
        return self._voz

    def sintetizar(self, texto, ruta_salida):
        voz = self._cargar()
        with wave.open(str(ruta_salida), 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            voz.synthesize(texto, wav)
        return ruta_salida

    def esta_disponible(self):
        import os
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return os.path.exists(self.ruta_voz)
