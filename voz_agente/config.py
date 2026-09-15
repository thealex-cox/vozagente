"""Configuración del motor de voz: qué proveedores usar y con qué voz.

Este fichero viene del motor de skytech y se ha simplificado en lo único en que ese
producto y este difieren de raíz: **allí una instalación servía a tres asistentes
distintos** —Skytech-Geo, InfraOpera y un negocio invitado— y había que elegir cuál
con `VOZ_PERSONA`, cargando su prompt de un `.md` en disco.

Aquí una instalación **es** un negocio. Su identidad entera —quién es, qué vende, qué
guion sigue— la compone `nucleo/negocio.py` desde `config.json` y viaja como contexto
de cada llamada, así que retocar el guion surte efecto en la llamada siguiente sin
reiniciar el servicio. No hay prompt en disco que cargar ni persona que elegir, y por
eso `persona()`, `cargar_prompt_negocio()` e `info_prompt()` ya no existen.

Lo demás —selección de TTS, voces, modelo— se mantiene igual que en el original para
que los dos árboles se puedan comparar fichero a fichero.
"""

import os

BASE_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Voces de edge-tts por idioma. es-EC es la voz ecuatoriana; el castellano
# peninsular suena ajeno en una llamada comercial en América.
VOCES = {
    'es': 'es-EC-LuisNeural',
    'en': 'en-US-ChristopherNeural',
}
IDIOMA_POR_DEFECTO = os.environ.get('VOZ_IDIOMA', 'es')


MOTORES_TTS = ('cartesia', 'edge', 'piper')


def motor_tts():
    """Cartesia en cuanto haya credenciales; edge-tts mientras tanto.

    Se auto-selecciona en vez de exigir una variable más porque son motores de
    ligas distintas: edge-tts tarda ~1.9 s en la primera frase y en una llamada eso
    se oye como una línea muerta. Si hay clave de Cartesia, no hay motivo para
    seguir con el de desarrollo. `VOZ_MOTOR_TTS` fuerza uno concreto.
    """
    explicito = os.environ.get('VOZ_MOTOR_TTS', '').strip().lower()
    if explicito in MOTORES_TTS:
        return explicito
    return 'cartesia' if os.environ.get('CARTESIA_API_KEY') else 'edge'


def construir_tts(motor='', idioma=IDIOMA_POR_DEFECTO, ruta_voz=''):
    """Piper no instala en Windows (piper-phonemize no publica wheels), así que
    edge-tts es el motor de desarrollo. En Linux funcionan los dos."""
    elegido = (motor or motor_tts()).lower()
    if elegido == 'cartesia':
        from voz_agente.proveedores.tts_cartesia import CartesiaTTS
        return CartesiaTTS(idioma=idioma)
    if elegido == 'piper':
        from voz_agente.proveedores.tts_piper import PiperTTS
        return PiperTTS(ruta_voz=ruta_voz or ruta_voz_por_defecto())
    from voz_agente.proveedores.tts_edge import EdgeTTS
    # 'es' y no IDIOMA_POR_DEFECTO como respaldo: esa variable sale de VOZ_IDIOMA sin
    # validar, así que un valor sin voz asignada —VOZ_IDIOMA=pt— hacía que el propio
    # respaldo reventara con KeyError. Y esto se construye al arrancar la llamada, así
    # que en vez de degradar a otra voz tumbaba la llamada entera al descolgar.
    return EdgeTTS(voz=VOCES.get(idioma) or VOCES.get(IDIOMA_POR_DEFECTO) or VOCES['es'])


def info_tts(idioma=IDIOMA_POR_DEFECTO):
    """Estado del TTS, para /salud. No publica la clave, sólo si está."""
    elegido = motor_tts()
    datos = {'tts': elegido, 'tts_streaming': elegido == 'cartesia'}
    if elegido == 'cartesia':
        from voz_agente.proveedores.tts_cartesia import CartesiaTTS
        motor_obj = CartesiaTTS(idioma=idioma)
        datos['tts_listo'] = motor_obj.esta_disponible()
        if not motor_obj.esta_disponible():
            datos['tts_falta'] = 'Falta CARTESIA_API_KEY o el UUID de voz del idioma.'
    else:
        datos['tts_listo'] = True
    return datos


def ruta_voz_por_defecto():
    return os.path.join(BASE_PROYECTO, 'voz_agente', 'voces', 'es_ES-davefx-medium.onnx')


def modelo_claude():
    """Permite contrastar modelos sin tocar el código.

    El valor por defecto lo fija `agente.py`: Haiku, elegido por latencia. En una
    llamada el tiempo hasta el primer token es lo que percibe el cliente como
    silencio, y ahí Haiku gana a modelos más capaces.
    """
    return os.environ.get('VOZ_MODELO_CLAUDE', '')


def hay_api_key():
    return bool(os.environ.get('ANTHROPIC_API_KEY'))
