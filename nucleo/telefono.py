"""Números de teléfono y a qué hora se puede marcar.

Portado de `voz/services.py` de skytech, y las dos reglas que trae vienen de fallos
reales que costaron llamadas:

- **No se le inventa el país a un número.** Buscar prefijos al principio marcaba
  números estadounidenses a Sudamérica: el código de área 510 (California) empieza
  por '51', que es Perú; el 562 por '56', Chile. Lo que separa sin ambigüedad es el
  cero inicial, que la marcación nacional ecuatoriana lleva y un número del plan
  norteamericano no puede tener.
- **La ventana horaria es la del destinatario, no la nuestra.** En EE. UU. la TCPA
  son 8:00-21:00 hora suya, y saltársela cuesta entre 500 y 1.500 USD por llamada.
"""

import re
from datetime import datetime, timedelta

# Horas legales por país, en hora local del destinatario. Es un techo: la
# configuración del negocio sólo puede estrechar esta ventana, nunca ampliarla.
VENTANAS_POR_PAIS = {
    # EE. UU. y Canadá: TCPA.
    '1': {'zona': 'America/New_York', 'inicio': 8, 'fin': 21, 'dias': (0, 1, 2, 3, 4, 5, 6)},
    # Ecuador: ARCOTEL exige autorización previa para llamadas comerciales.
    '593': {'zona': 'America/Guayaquil', 'inicio': 8, 'fin': 20, 'dias': (0, 1, 2, 3, 4, 5)},
    '57': {'zona': 'America/Bogota', 'inicio': 8, 'fin': 20, 'dias': (0, 1, 2, 3, 4, 5)},
    '51': {'zona': 'America/Lima', 'inicio': 8, 'fin': 20, 'dias': (0, 1, 2, 3, 4, 5)},
    '52': {'zona': 'America/Mexico_City', 'inicio': 8, 'fin': 20, 'dias': (0, 1, 2, 3, 4, 5)},
    '34': {'zona': 'Europe/Madrid', 'inicio': 9, 'fin': 21, 'dias': (0, 1, 2, 3, 4, 5)},
}

VENTANA_POR_DEFECTO = {'zona': None, 'inicio': 9, 'fin': 18, 'dias': (0, 1, 2, 3, 4)}

PREFIJOS_CONOCIDOS = ('593', '351', '55', '57', '56', '54', '52', '51', '34', '1')

# El plan de numeración de Ecuador: móvil de nueve dígitos que empieza por 9, fijo de
# ocho que empieza por el código de provincia (2-7).
#
# **Es el único país cuya longitud se comprueba**, y a propósito. Un `len >= 9` suelto
# no distingue un fijo bueno de ocho dígitos (022345678) de un móvil al que le falta
# uno (099912345): los dos tienen nueve con el cero delante, así que el truncado
# pasaba, se marcaba, y moría en el proveedor después de haberse dado por válido.
# Inventarse la longitud de los demás países haría lo contrario —rechazar números
# buenos—, y eso es peor: al malo lo para el proveedor, al bueno no lo salva nadie.
PLAN_ECUADOR = re.compile(r'^(9\d{8}|[2-7]\d{7})$')


def _plan_valido(e164):
    """Si el número es ecuatoriano, que encaje con su plan. El resto pasa."""
    if not e164.startswith('+593'):
        return True
    return bool(PLAN_ECUADOR.match(e164[4:]))


def normalizar(telefono, pais_defecto='593'):
    """Devuelve el número en E.164 (+593999123456) o '' si no es utilizable.

    `pais_defecto` sólo se aplica a la marcación nacional con cero inicial. Lo que no
    encaja en ningún formato reconocible **se descarta en vez de suponerle un país**:
    una llamada que no sale se queda anotada y el dato se corrige, pero una mal
    marcada le suena a un desconocido y se factura igual.
    """
    if not telefono:
        return ''
    limpio = re.sub(r'[^\d+]', '', str(telefono))
    if not limpio:
        return ''

    candidato = _a_e164(limpio, pais_defecto)
    return candidato if _plan_valido(candidato) else ''


def _a_e164(limpio, pais_defecto):
    """La forma internacional según cómo esté escrito, sin juzgar si existe."""
    if limpio.startswith('+'):
        return limpio if len(limpio) >= 9 else ''

    if limpio.startswith('00'):
        # Prefijo de salida internacional: lo que sigue ya trae código de país.
        resto = limpio[2:]
        return f'+{resto}' if len(resto) >= 9 else ''

    if limpio.startswith('0'):
        # Tronco nacional: 0999123456 -> +593999123456 con pais_defecto='593'.
        return f'+{pais_defecto}{limpio[1:]}' if len(limpio) >= 9 else ''

    # Plan norteamericano: diez dígitos, u once que ya empiezan por su código.
    if len(limpio) == 10 and pais_defecto == '1':
        return f'+1{limpio}'
    if len(limpio) == 11 and limpio.startswith('1'):
        return f'+{limpio}'

    # Más largo: aquí sí es seguro mirar prefijos, porque la longitud ya descarta
    # que sea una marcación nacional.
    if len(limpio) >= 11 and limpio.startswith(PREFIJOS_CONOCIDOS):
        return f'+{limpio}'

    # Nueve dígitos empezando por 9 es un móvil ecuatoriano al que le falta el cero.
    if len(limpio) == 9 and limpio.startswith('9') and pais_defecto == '593':
        return f'+593{limpio}'

    return ''


def pais(telefono):
    numero = (telefono or '').lstrip('+')
    for prefijo in sorted(PREFIJOS_CONOCIDOS, key=len, reverse=True):
        if numero.startswith(prefijo):
            return prefijo
    return ''


def formatear(numero):
    """+13055551234 -> +1 (305) 555-1234. Lo demás se devuelve tal cual."""
    limpio = re.sub(r'[^\d+]', '', str(numero or ''))
    if limpio.startswith('+1') and len(limpio) == 12:
        return f'+1 ({limpio[2:5]}) {limpio[5:8]}-{limpio[8:]}'
    return limpio


def _hora_local(codigo, ahora=None):
    """La hora del destinatario. Sin `zoneinfo` utilizable se usa la del servidor.

    Degradar en vez de levantar es deliberado: una zona horaria que no resuelve no
    puede impedir atender llamadas, y la ventana por defecto es más estrecha que
    cualquier techo legal, así que equivocarse aquí peca de prudente.
    """
    momento = ahora or datetime.now()
    ventana = VENTANAS_POR_PAIS.get(codigo, VENTANA_POR_DEFECTO)
    zona = ventana.get('zona')
    if not zona:
        return momento
    try:
        from zoneinfo import ZoneInfo
        from datetime import timezone
        return datetime.now(timezone.utc).astimezone(ZoneInfo(zona)).replace(tzinfo=None)
    except Exception:
        return momento


def ventana_efectiva(codigo, config_llamadas=None):
    """Cruza el techo legal del país con la preferencia del negocio y se queda con
    la más estrecha. La configuración **nunca puede ampliar** lo que la ley permite:
    ese es el único motivo por el que esto no es un simple `get`."""
    legal = dict(VENTANAS_POR_PAIS.get(codigo, VENTANA_POR_DEFECTO))
    preferida = config_llamadas or {}

    inicio = preferida.get('hora_inicio')
    fin = preferida.get('hora_fin')
    dias = preferida.get('dias')

    if isinstance(inicio, int):
        legal['inicio'] = max(legal['inicio'], inicio)
    if isinstance(fin, int):
        legal['fin'] = min(legal['fin'], fin)
    if isinstance(dias, (list, tuple)) and dias:
        legal['dias'] = tuple(d for d in legal['dias'] if d in dias)
    return legal


def puede_llamar(telefono, config_llamadas=None, ahora=None):
    """(True, '') si se puede marcar ahora mismo; (False, motivo) si no."""
    codigo = pais(telefono)
    ventana = ventana_efectiva(codigo, config_llamadas)
    momento = _hora_local(codigo, ahora)

    if momento.weekday() not in ventana['dias']:
        return False, (f'Hoy no se llama a este destino '
                       f'(son las {momento:%H:%M} del {momento:%A} alli).')
    if not ventana['inicio'] <= momento.hour < ventana['fin']:
        return False, (f'Fuera de la franja {ventana["inicio"]}:00-{ventana["fin"]}:00 '
                       f'del destinatario (alli son las {momento:%H:%M}).')
    return True, ''


def proximo_momento_valido(telefono, config_llamadas=None, desde=None):
    """Cuándo se podrá llamar. Busca a saltos de 15 min hasta 8 días.

    Devuelve None si no encuentra hueco, y quien llama decide qué hacer: apuntar la
    hora pedida y que el despacho la frene, que es visible y se diagnostica, en vez
    de descartar la llamada, que no deja rastro.
    """
    momento = desde or datetime.now()
    for _ in range(8 * 24 * 4):
        permitido, _motivo = puede_llamar(telefono, config_llamadas, momento)
        if permitido:
            return momento
        momento += timedelta(minutes=15)
    return None
