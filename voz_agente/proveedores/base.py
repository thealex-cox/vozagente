"""Interfaces de STT y TTS.

Cada capa es intercambiable a propósito: en una llamada real conviene el proveedor
cloud (más rápido), pero para desarrollar y medir sirve el local (gratis y sin
cuentas). El resto del agente no debe enterarse de cuál está activo.
"""

from abc import ABC, abstractmethod


class STTBase(ABC):
    nombre = ''

    @abstractmethod
    def transcribir(self, ruta_audio):
        """Devuelve el texto reconocido de un archivo de audio."""

    def esta_disponible(self):
        return True


class TTSBase(ABC):
    nombre = ''
    # Frecuencia de muestreo de salida. La telefonía trabaja a 8 kHz, así que
    # cualquier cosa por encima habrá que remuestrearla al enchufar el carrier.
    sample_rate = 22050
    extension = 'wav'
    # True sólo si el proveedor devuelve audio conforme lo genera. Es la diferencia
    # entre que el primer sonido salga en ~90 ms o después de sintetizar la frase
    # entera, que con edge-tts son casi dos segundos.
    soporta_streaming = False

    @abstractmethod
    def sintetizar(self, texto, ruta_salida):
        """Escribe el audio del texto en ruta_salida. Devuelve la ruta."""

    def transmitir(self, texto, encoding='pcm_mulaw', sample_rate=8000):
        """Genera el audio por trozos, en crudo y sin cabeceras.

        La implementación por defecto sintetiza el fichero completo y lo devuelve
        de una vez: correcta, pero sin ninguna ganancia de latencia. Los proveedores
        que sepan hacerlo de verdad la sobrescriben.
        """
        import os
        import tempfile

        from voz_agente import audio

        destino = os.path.join(
            tempfile.gettempdir(), f'tts_{os.getpid()}_{id(texto)}.{self.extension}'
        )
        try:
            self.sintetizar(texto, destino)
            pcm = audio.archivo_a_pcm(destino, sample_rate)
            yield audio.pcm_a_mulaw(pcm) if encoding == 'pcm_mulaw' else pcm
        finally:
            try:
                os.remove(destino)
            except OSError:
                pass

    def esta_disponible(self):
        return True


def limpiar_para_voz(texto):
    """Quita del texto todo lo que no se puede pronunciar.

    El prompt le pide al modelo que no use markdown, pero obedece sólo casi siempre:
    en las pruebas seguían colándose negritas. Esto lo garantiza por código, que es
    la única forma de que un asterisco no acabe leído en voz alta al cliente.
    """
    import re
    texto = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', texto)      # [texto](url) -> texto
    texto = re.sub(r'https?://\S+', '', texto)                   # URLs sueltas
    texto = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', texto)       # **negrita**
    texto = re.sub(r'_{2,}([^_]+)_{2,}', r'\1', texto)
    texto = re.sub(r'`{1,3}([^`]*)`{1,3}', r'\1', texto)         # `código`
    texto = re.sub(r'^#{1,6}\s*', '', texto, flags=re.MULTILINE)  # encabezados
    texto = re.sub(r'^\s*[-*+]\s+', '', texto, flags=re.MULTILINE)  # viñetas
    texto = re.sub(r'\s{2,}', ' ', texto)
    return texto.strip()


def dividir_en_frases(texto, minimo=25):
    """Parte el texto en frases pronunciables.

    Es lo que permite empezar a hablar antes de que el modelo termine de escribir:
    en cuanto hay una frase completa se sintetiza, en lugar de esperar la respuesta
    entera. Es la diferencia entre responder en menos de un segundo o en cuatro.
    """
    frases, actual = [], ''
    for caracter in texto:
        actual += caracter
        if caracter in '.?!\n' and len(actual.strip()) >= minimo:
            frases.append(actual.strip())
            actual = ''
    if actual.strip():
        frases.append(actual.strip())
    return frases
