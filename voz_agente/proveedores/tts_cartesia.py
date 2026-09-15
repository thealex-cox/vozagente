"""TTS con Cartesia Sonic. Es el motor de producción para llamadas.

Resuelve el único problema que impedía usar el agente por teléfono: edge-tts
sintetiza la frase entera antes de devolver el primer byte —1.94 s medidos para una
sola frase—, mientras que Cartesia va entregando el audio conforme lo genera.

Y hay una segunda ganancia que no es de latencia sino de arquitectura: Cartesia
emite directamente `pcm_mulaw` a 8000 Hz, que es exactamente lo que come Twilio. Por
el camino telefónico desaparecen la decodificación de MP3, el remuestreo y la
conversión a μ-law; los bytes que llegan de Cartesia se reenvían tal cual.

El contenedor `raw` es obligatorio en streaming: los endpoints SSE y WebSocket no
saben devolver WAV ni MP3. Para el fichero suelto (demo por navegador, banco de
pruebas) se usa el endpoint de bytes, que sí acepta WAV.
"""

import base64
import json
import logging
import os

import httpx

from voz_agente.proveedores.base import TTSBase

logger = logging.getLogger(__name__)

URL_BASE = os.environ.get('CARTESIA_URL', 'https://api.cartesia.ai')
# La cabecera Cartesia-Version es obligatoria y fija el contrato: si se omite, la
# API rechaza la peticion.
VERSION_API = os.environ.get('CARTESIA_VERSION', '2026-03-01')
MODELO_POR_DEFECTO = os.environ.get('CARTESIA_MODELO', 'sonic-3.5')
TIMEOUT = 30

# Las voces de Cartesia son UUID de su catalogo, no nombres. No hay valor por
# defecto sensato que inventar: se configuran por entorno.
VARIABLE_VOZ = {'es': 'CARTESIA_VOZ_ES', 'en': 'CARTESIA_VOZ_EN'}


class CartesiaError(Exception):
    """Fallo del proveedor. Quien la capture debe degradar a otro TTS."""


class CartesiaTTS(TTSBase):
    nombre = 'cartesia'
    extension = 'wav'
    sample_rate = 24000
    soporta_streaming = True

    def __init__(self, voz='', idioma='es', modelo='', api_key=''):
        self.idioma = idioma
        self.voz = voz or os.environ.get(VARIABLE_VOZ.get(idioma, ''), '')
        self.modelo = modelo or MODELO_POR_DEFECTO
        self.api_key = api_key or os.environ.get('CARTESIA_API_KEY', '')

    def esta_disponible(self):
        return bool(self.api_key and self.voz)

    def _cabeceras(self):
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Cartesia-Version': VERSION_API,
            'Content-Type': 'application/json',
        }

    def _cuerpo(self, texto, contenedor, encoding, tasa):
        return {
            'model_id': self.modelo,
            'transcript': texto,
            'voice': {'mode': 'id', 'id': self.voz},
            'language': self.idioma,
            'output_format': {
                'container': contenedor,
                'encoding': encoding,
                'sample_rate': tasa,
            },
        }

    def _comprobar(self):
        if not self.api_key:
            raise CartesiaError('Falta CARTESIA_API_KEY')
        if not self.voz:
            raise CartesiaError(
                f"Falta la voz para '{self.idioma}': configura "
                f"{VARIABLE_VOZ.get(self.idioma, 'CARTESIA_VOZ_*')} con un UUID del catalogo"
            )

    def sintetizar(self, texto, ruta_salida):
        """Fichero completo. Para la demo por navegador y el banco de pruebas."""
        self._comprobar()
        try:
            respuesta = httpx.post(
                f'{URL_BASE}/tts/bytes',
                headers=self._cabeceras(),
                json=self._cuerpo(texto, 'wav', 'pcm_s16le', self.sample_rate),
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as ex:
            raise CartesiaError(f'No se pudo llegar a Cartesia: {ex}')

        if respuesta.status_code >= 400:
            raise CartesiaError(f'Cartesia devolvio {respuesta.status_code}: {respuesta.text[:300]}')

        with open(str(ruta_salida), 'wb') as archivo:
            archivo.write(respuesta.content)
        return ruta_salida

    def transmitir(self, texto, encoding='pcm_mulaw', sample_rate=8000):
        """Audio en crudo conforme se genera. Es el camino de la llamada."""
        self._comprobar()
        cuerpo = self._cuerpo(texto, 'raw', encoding, sample_rate)

        try:
            with httpx.stream(
                'POST', f'{URL_BASE}/tts/sse',
                headers=self._cabeceras(), json=cuerpo, timeout=TIMEOUT,
            ) as respuesta:
                if respuesta.status_code >= 400:
                    respuesta.read()
                    raise CartesiaError(
                        f'Cartesia devolvio {respuesta.status_code}: {respuesta.text[:300]}'
                    )

                for linea in respuesta.iter_lines():
                    if not linea or not linea.startswith('data:'):
                        continue
                    evento = json.loads(linea[5:].strip())
                    tipo = evento.get('type')

                    if tipo == 'chunk':
                        yield base64.b64decode(evento['data'])
                    elif tipo == 'error':
                        raise CartesiaError(
                            evento.get('message') or evento.get('title') or 'error desconocido'
                        )
                    elif tipo == 'done' or evento.get('done'):
                        return
                    # 'timestamps' y 'phoneme_timestamps' se ignoran: no se piden.
        except httpx.HTTPError as ex:
            raise CartesiaError(f'Se corto el streaming de Cartesia: {ex}')
