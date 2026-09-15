"""STT en la nube con Deepgram, para el audio telefónico.

Whisper local funciona, pero está entrenado con audio de calidad y la telefonía va a
8 kHz con compresión: en llamadas reales confundía nombres propios hasta el punto de
que el agente concluía que hablaba con otra persona y cerraba la llamada. Darle el
vocabulario esperado lo arregló en buena parte; esto es el siguiente escalón, con un
modelo entrenado precisamente para llamadas.

Ésta es la versión **sin streaming**: recibe el turno ya grabado, igual que Whisper,
así que encaja donde estaba sin tocar el puente. Mejora la precisión, no la latencia
— para eso hace falta su conexión permanente, que sí cambia cómo se detectan los
turnos y es un trabajo aparte.

    export DEEPGRAM_API_KEY=...
    export VOZ_MOTOR_STT=deepgram
"""

import logging
import os

import requests

from voz_agente.proveedores.base import STTBase

logger = logging.getLogger(__name__)

URL = 'https://api.deepgram.com/v1/listen'
TIEMPO_ESPERA = 30
# Medido contra la API real sobre una muestra de 20 s a 8 kHz: nova-3 transcribió los
# 169 caracteres completos y nova-2 sólo 92 —se comió la primera mitad—. La variante
# 'phonecall', que sería la lógica aquí, **no existe para español**: la API responde
# 400 y sugiere justamente nova-3.
MODELO_POR_DEFECTO = 'nova-3'


class DeepgramSTT(STTBase):
    nombre = 'deepgram'

    def __init__(self, clave='', modelo='', idioma='es'):
        self.clave = clave or os.environ.get('DEEPGRAM_API_KEY', '')
        self.modelo = modelo or os.environ.get('DEEPGRAM_MODELO', MODELO_POR_DEFECTO)
        self.idioma = idioma
        # Mismo atributo que el reconocedor local: el puente le pone aquí el nombre
        # de quien contesta y el servicio cotizado, y los dos lo aprovechan.
        self.contexto = ''

    def esta_disponible(self):
        return bool(self.clave)

    def transcribir(self, ruta_audio):
        if not self.clave:
            logger.error('Falta DEEPGRAM_API_KEY: no se puede transcribir')
            return ''

        parametros = {
            'model': self.modelo,
            'language': self.idioma,
            # Puntuación y mayúsculas. Sin esto llega todo en minúsculas y seguido,
            # que se lee mal en la transcripción del panel.
            'smart_format': 'true',
        }
        # `keyterm` da preferencia a términos concretos. Verificado contra la API: sin
        # él, «Gracias por llamar a Skytech» se transcribe como «Gracias por llamar
        # a.» — se come la marca. Con él, aparece.
        terminos = [t.strip() for t in self.contexto.split(',') if t.strip()]
        if terminos:
            parametros['keyterm'] = terminos[:20]

        try:
            with open(ruta_audio, 'rb') as audio:
                respuesta = requests.post(
                    URL,
                    params=parametros,
                    data=audio,
                    headers={
                        'Authorization': f'Token {self.clave}',
                        'Content-Type': 'audio/wav',
                    },
                    timeout=TIEMPO_ESPERA,
                )
        except requests.RequestException as ex:
            # Nunca levanta: hay un cliente al teléfono y quedarse sin transcripción
            # de un turno es recuperable; que se caiga la llamada, no.
            logger.warning(f'Deepgram no respondio: {ex}')
            return ''

        if respuesta.status_code >= 400:
            logger.error(
                f'Deepgram rechazo el audio ({respuesta.status_code}): '
                f'{respuesta.text[:200]}'
            )
            return ''

        try:
            canales = respuesta.json()['results']['channels']
            return (canales[0]['alternatives'][0]['transcript'] or '').strip()
        except (ValueError, KeyError, IndexError) as ex:
            logger.error(f'Respuesta de Deepgram sin transcripcion utilizable: {ex}')
            return ''
