"""STT local con faster-whisper. Sin API key y sin costo por minuto.

En CPU el modelo 'base' ronda tiempo real para audio telefónico; 'small' es más
preciso pero se va a ~2x. Para producción conviene medir contra Deepgram, que
cobra USD 0.0043/min y responde en streaming.
"""

import logging
import os
import time

from voz_agente.proveedores.base import STTBase

logger = logging.getLogger(__name__)


class WhisperLocalSTT(STTBase):
    nombre = 'whisper-local'

    def __init__(self, modelo='base', idioma='es', compute_type='int8', beam_size=None):
        self.modelo_nombre = modelo
        self.idioma = idioma
        self.compute_type = compute_type
        # Cuántas alternativas compara antes de decidir cada palabra. Con 1 coge la
        # primera que se le ocurre, que es lo más rápido y lo que producía «habla con
        # electric digamos» en lugar de «habla con Alex Chica» — y con eso el agente
        # dio por hecho que no era el cliente y cerró la llamada.
        self.beam_size = int(beam_size or os.environ.get('VOZ_BEAM_SIZE', '5'))
        # Vocabulario esperado de la llamada en curso: nombre de quien contesta,
        # servicio cotizado, la marca. Whisper lo usa como contexto previo y deja de
        # inventarse los nombres propios, que es donde falla en audio telefónico.
        # Lo fija el puente en cada llamada, igual que `idioma`, bajo el mismo cerrojo.
        self.contexto = ''
        self._modelo = None

    def _cargar(self):
        """Carga perezosa: el modelo tarda en inicializar y pesa en memoria, así
        que no debe ocurrir al importar el módulo."""
        if self._modelo is None:
            from faster_whisper import WhisperModel
            inicio = time.time()
            self._modelo = WhisperModel(
                self.modelo_nombre, device='cpu', compute_type=self.compute_type,
            )
            logger.info(f'Whisper {self.modelo_nombre} cargado en {time.time() - inicio:.1f}s')
        return self._modelo

    def transcribir(self, ruta_audio):
        modelo = self._cargar()
        segmentos, _ = modelo.transcribe(
            str(ruta_audio), language=self.idioma, beam_size=self.beam_size,
            vad_filter=True, initial_prompt=self._como_frase(),
        )
        return ' '.join(s.text.strip() for s in segmentos).strip()

    def _como_frase(self):
        """El vocabulario llega como lista de términos, pero Whisper lo trata como el
        texto que precede al audio: una enumeración suelta orienta peor que algo que
        alguien podría haber dicho.

        La frase **no nombra ninguna empresa**. Decía «Llamada de Skytech sobre una
        cotizacion», y desde que el mismo agente atiende a otros negocios eso es
        cebar al reconocedor con la marca equivocada: en la llamada de una panadería
        empujaba a transcribir «Skytech» sobre cualquier palabra que se le pareciera.
        Los términos de `contexto` ya traen el nombre que toca en cada llamada.
        """
        if not self.contexto:
            return None
        return f'Llamada telefonica. Se habla de: {self.contexto}.'

    def esta_disponible(self):
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False
