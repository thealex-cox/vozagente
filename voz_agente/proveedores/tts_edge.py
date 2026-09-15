"""TTS con edge-tts. Gratis, sin API key y con voces neuronales de Ecuador.

Es la opción de desarrollo en Windows, donde Piper no instala (piper-phonemize no
publica wheels). Requiere internet: se apoya en el servicio de Microsoft Edge, así
que no es self-hosted y no conviene depender de él en producción.

Para producción sobre el servidor Linux: Piper (local) o Cartesia (cloud).
"""

import asyncio
import logging

from voz_agente.proveedores.base import TTSBase

logger = logging.getLogger(__name__)

VOZ_POR_DEFECTO = 'es-EC-LuisNeural'


class EdgeTTS(TTSBase):
    nombre = 'edge-tts'
    sample_rate = 24000
    extension = 'mp3'

    def __init__(self, voz=VOZ_POR_DEFECTO, velocidad='+0%'):
        self.voz = voz
        self.velocidad = velocidad

    async def _sintetizar_async(self, texto, ruta_salida):
        import edge_tts
        communicate = edge_tts.Communicate(texto, self.voz, rate=self.velocidad)
        await communicate.save(str(ruta_salida))
        return ruta_salida

    def sintetizar(self, texto, ruta_salida):
        """Salida en MP3. Para telefonía habrá que remuestrear a PCM 8 kHz."""
        return asyncio.run(self._sintetizar_async(texto, ruta_salida))

    def esta_disponible(self):
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False
