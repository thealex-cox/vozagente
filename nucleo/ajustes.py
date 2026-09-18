"""Configuración del servicio: PostgreSQL manda, `config.json` es el espejo.

Los ajustes viven en la tabla `ajuste`, una fila por sección, y se editan desde el
panel o desde pgAdmin. `config.json` sigue existiendo y cumple **dos** papeles que la
base no puede cumplir:

- Guarda la sección `bd`, que es cómo se llega a la base. No puede estar dentro de
  ella misma.
- Es la copia de seguridad. Si Postgres no contesta, el servicio arranca con lo
  último que se guardó en el fichero y lo dice arriba del panel, en vez de quedarse
  sin atender llamadas. Un agente telefónico que depende de que una base esté viva
  para descolgar es un agente peor.

Al guardar se escriben los dos: primero la base, que es la fuente, y después el
espejo.

**El motor de voz lee su configuración de variables de entorno** (`CARTESIA_API_KEY`,
`VOZ_STREAM_TOKEN`, …), que es como venía de skytech. En vez de tocarlo para que lea
JSON —y perder la posibilidad de comparar los dos árboles fichero a fichero—, este
módulo vuelca lo que hace falta al entorno al arrancar. Es una traducción de diez
líneas y mantiene el motor intacto.

`config.json` lleva credenciales y **no se versiona**. `config.example.json` sí, y es
el que documenta la forma.
"""

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_CONFIG = os.environ.get('VOZ_CONFIG') or os.path.join(RAIZ, 'config.json')

_cache = None

# Verdadero cuando la última lectura no pudo hablar con la base y se tiró del
# fichero. Lo consulta `revisar()` para decirlo arriba del panel: si no, el servicio
# funcionaría con una copia vieja y nadie se enteraría hasta encontrar un ajuste que
# no cuadra.
_desde_respaldo = False

# La seccion `bd` NO se guarda en la base: es la que dice como llegar a ella. Vive
# solo en config.json y es lo unico que hace falta para arrancar.
SECCION_CONEXION = 'bd'

ESQUEMA_AJUSTES = """
CREATE TABLE IF NOT EXISTS ajuste (
    seccion TEXT PRIMARY KEY,
    datos TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
);
"""


def _conexion_bd(datos_bd):
    """Una conexión para leer o escribir ajustes, o None si no se puede.

    No usa `nucleo.almacen`: ese módulo importa éste, y cerrar el círculo dejaría el
    arranque dependiendo del orden de los imports. Son cuatro líneas de psycopg y
    evitan un ciclo que costaría mucho más explicar.
    """
    try:
        import psycopg
    except ImportError:
        return None
    host = (datos_bd.get('host') or 'localhost').strip()
    puerto = datos_bd.get('puerto') or 5432
    base = (datos_bd.get('base') or 'vozagente').strip()
    usuario = (datos_bd.get('usuario') or 'postgres').strip()
    clave = datos_bd.get('clave') or ''
    return psycopg.connect(
        f'host={host} port={puerto} dbname={base} user={usuario} password={clave}',
        connect_timeout=3)


# Cuando la base falla, el instante en que dejamos de reintentar. `cargar(recargar=
# True)` se ejecuta **en cada llamada entrante**, y con Postgres caido cada intento
# costaba los 5 s enteros del timeout: la llamada se quedaba muda esperando y el
# proveedor la daba por perdida. Tras un fallo se deja de preguntar un rato y se tira
# del espejo, que es instantaneo.
_bd_en_pausa_hasta = 0.0
SEGUNDOS_PAUSA_BD = 30


def _leer_de_bd(datos_bd):
    """Las secciones guardadas en la base. `None` si no se pudo hablar con ella.

    `None` y `{}` no significan lo mismo, y por eso se distinguen: una base vacía es
    una instalación nueva y se sigue adelante con el fichero; una base inalcanzable
    es un fallo que hay que decir en voz alta.
    """
    global _bd_en_pausa_hasta
    if time.monotonic() < _bd_en_pausa_hasta:
        return None

    try:
        con = _conexion_bd(datos_bd)
        if con is None:
            return None
        try:
            with con.cursor() as cur:
                cur.execute(ESQUEMA_AJUSTES)
                con.commit()
                cur.execute('SELECT seccion, datos FROM ajuste')
                filas = cur.fetchall()
        finally:
            con.close()
    except Exception as ex:
        _bd_en_pausa_hasta = time.monotonic() + SEGUNDOS_PAUSA_BD
        logger.warning('No se pudieron leer los ajustes de la base (no se reintenta '
                       'en %ss): %s', SEGUNDOS_PAUSA_BD,
                       str(ex).encode('ascii', 'replace').decode())
        return None

    _bd_en_pausa_hasta = 0.0

    fuera = {}
    for seccion_nombre, crudo in filas:
        try:
            fuera[seccion_nombre] = json.loads(crudo)
        except ValueError:
            logger.warning(f'La seccion «{seccion_nombre}» de la base no es JSON valido')
    return fuera


def _escribir_en_bd(datos_bd, config):
    """Vuelca las secciones a la base. Devuelve False si no se pudo."""
    from datetime import datetime
    ahora = datetime.now().isoformat(timespec='seconds')
    try:
        con = _conexion_bd(datos_bd)
        if con is None:
            return False
        try:
            with con.cursor() as cur:
                cur.execute(ESQUEMA_AJUSTES)
                for nombre, valores in config.items():
                    if nombre == SECCION_CONEXION:
                        continue
                    cur.execute(
                        'INSERT INTO ajuste (seccion, datos, actualizado_en) '
                        'VALUES (%s, %s, %s) ON CONFLICT (seccion) DO UPDATE SET '
                        'datos = excluded.datos, '
                        'actualizado_en = excluded.actualizado_en',
                        (nombre, json.dumps(valores, ensure_ascii=False), ahora))
            con.commit()
        finally:
            con.close()
        return True
    except Exception as ex:
        logger.warning('No se pudieron guardar los ajustes en la base: %s',
                       str(ex).encode('ascii', 'replace').decode())
        return False


def desde_respaldo():
    """Si la última carga tuvo que tirar del fichero porque la base no contestó."""
    return _desde_respaldo


def asegurar_base(config=None):
    """Crea la base si no existe. Devuelve (hecho, mensaje).

    Sin esto, una instalación nueva arranca, conversa, toma un encargo y **no guarda
    nada**: el registro nunca levanta hacia fuera, así que el fallo sale como unos
    avisos en el log y una pestaña de Pedidos vacía que parece normal. Se descubre
    cuando alguien pregunta por el encargo que el cliente dio por hecho.

    `CREATE DATABASE` no puede ir dentro de una transacción, de ahí el `autocommit`.
    """
    datos = (config if config is not None else cargar()).get(SECCION_CONEXION) or {}
    base = (datos.get('base') or 'vozagente').strip()
    try:
        import psycopg
    except ImportError:
        return False, 'Falta el driver: pip install "psycopg[binary]"'

    comun = (f"host={datos.get('host') or 'localhost'} "
             f"port={datos.get('puerto') or 5432} "
             f"user={datos.get('usuario') or 'postgres'} "
             f"password={datos.get('clave') or ''}")
    try:
        with psycopg.connect(f'{comun} dbname=postgres', connect_timeout=5,
                             autocommit=True) as con:
            existe = con.execute('SELECT 1 FROM pg_database WHERE datname = %s',
                                 (base,)).fetchone()
            global _bd_en_pausa_hasta
            if existe:
                return True, ''
            con.execute(f'CREATE DATABASE "{base}"')
            # La base acaba de nacer, asi que el intento fallido de hace un momento
            # ya no dice nada: sin esto, la pausa de reintento haria que el arranque
            # creara la base y en la linea siguiente avisara de que no puede leerla.
            _bd_en_pausa_hasta = 0.0
            return True, f'Base «{base}» creada en PostgreSQL.'
    except Exception as ex:
        limpio = str(ex).encode('ascii', 'replace').decode().split('\n')[0]
        return False, f'No se pudo preparar la base «{base}»: {limpio}'


def volcar_a_bd(config=None):
    """Sube a la base lo que hay en `config.json`. Devuelve False si no se pudo.

    Es la migración inicial, y se puede repetir sin miedo: reescribe cada sección con
    lo que tenga el fichero. Quien la llame dos veces obtiene el mismo resultado.
    """
    datos = config if config is not None else cargar(recargar=True)
    return _escribir_en_bd(datos.get(SECCION_CONEXION) or {}, datos)


class ConfigError(Exception):
    """La configuración no permite arrancar. Se levanta al inicio y a propósito: es
    preferible no arrancar a arrancar mudo y descubrirlo con alguien al teléfono."""


def cargar(ruta=None, recargar=False):
    """Lee `config.json`. Se cachea salvo que se pida recargarlo.

    La recarga existe para el guion: se retoca el texto, se vuelve a leer y la
    llamada siguiente ya lo usa, sin reiniciar el servicio. Es lo que se hace veinte
    veces mientras se afina una demo.
    """
    global _cache
    if _cache is not None and not recargar and ruta is None:
        return _cache

    destino = ruta or RUTA_CONFIG
    if not os.path.exists(destino):
        raise ConfigError(
            f'No existe {destino}. Copia config.example.json a config.json y '
            f'rellenalo.')

    try:
        with open(destino, encoding='utf-8') as archivo:
            datos = json.load(archivo)
    except ValueError as ex:
        # El fallo típico es una coma de más al editar el guion a mano. Decir la
        # línea ahorra la búsqueda a ojo en un fichero con texto largo dentro.
        raise ConfigError(f'{destino} no es JSON valido: {ex}')

    # Lo que hay en la base manda sobre lo que hay en el fichero. El fichero deja de
    # ser la fuente y pasa a ser dos cosas: de donde sale `bd` —lo unico que hace
    # falta para llegar a la base— y el espejo que se usa si la base no contesta.
    global _desde_respaldo
    guardadas = _leer_de_bd(datos.get(SECCION_CONEXION) or {})
    _desde_respaldo = guardadas is None
    if guardadas:
        for nombre, valores in guardadas.items():
            if nombre == SECCION_CONEXION:
                continue
            if isinstance(valores, dict) and isinstance(datos.get(nombre), dict):
                # Mezcla y no sustitucion: una clave que el panel todavia no conoce
                # y solo esta en el fichero no puede desaparecer por no estar en la
                # base.
                fusion = dict(datos[nombre])
                fusion.update(valores)
                datos[nombre] = fusion
            else:
                datos[nombre] = valores

    if ruta is None:
        _cache = datos
    return datos


def seccion(nombre, config=None):
    datos = config if config is not None else cargar()
    valor = datos.get(nombre) or {}
    return valor if isinstance(valor, dict) else {}


def negocio(config=None):
    return seccion('negocio', config)


def carrier(config=None):
    return seccion('carrier', config)


def ia(config=None):
    return seccion('ia', config)


def web(config=None):
    return seccion('web', config)


def llamadas(config=None):
    return seccion('llamadas', config)


def bd(config=None):
    return seccion('bd', config)


def dsn(config=None):
    """La cadena de conexion a PostgreSQL.

    `VOZ_BD` manda sobre `config.json`, y es lo que usan las pruebas para trabajar
    sobre una base desechable sin tocar la de verdad. Antes apuntaba a un fichero
    SQLite; ahora es un DSN, que es el mismo papel con otro motor.
    """
    del_entorno = (os.environ.get('VOZ_BD') or '').strip()
    if del_entorno:
        return del_entorno
    datos = bd(config)
    host = (datos.get('host') or 'localhost').strip()
    puerto = datos.get('puerto') or 5432
    base = (datos.get('base') or 'vozagente').strip()
    usuario = (datos.get('usuario') or 'postgres').strip()
    clave = datos.get('clave') or ''
    return (f'host={host} port={puerto} dbname={base} '
            f'user={usuario} password={clave}')


def url_publica(config=None):
    """Por dónde alcanza el carrier a este servicio. Sin barra final.

    No se deduce de la petición: detrás de un túnel o un proxy, el host que llega no
    es el que el carrier usó, y la URL del webhook tiene que ser exactamente la que
    él conoce.
    """
    return (web(config).get('url_publica') or '').strip().rstrip('/')


def url_stream(config=None):
    """`wss://…/twilio`, por donde el carrier entrega el audio. Vacía = sin puente:
    la llamada se atiende con un mensaje fijo en vez de con el agente."""
    return (web(config).get('url_stream') or '').strip()


def token(config=None):
    """El secreto compartido. Autentica el WebSocket del audio —que el carrier no
    firma— y viaja en la query de los webhooks."""
    return (web(config).get('token') or '').strip()


def aplicar_entorno(config=None):
    """Vuelca al entorno lo que el motor de voz espera encontrar allí.

    Se llama una vez al arrancar. Sólo escribe lo que tiene valor: una clave vacía
    en el JSON no puede pisar una variable puesta a mano en la terminal, que es
    justo lo que se hace para probar un proveedor distinto sin tocar el fichero.
    """
    datos = config if config is not None else cargar()
    claves = ia(datos)

    equivalencias = {
        'ANTHROPIC_API_KEY': claves.get('anthropic_api_key'),
        'VOZ_MODELO_CLAUDE': claves.get('modelo'),
        'DEEPGRAM_API_KEY': claves.get('deepgram_api_key'),
        'CARTESIA_API_KEY': claves.get('cartesia_api_key'),
        'CARTESIA_VOZ_ES': claves.get('cartesia_voz_es'),
        'CARTESIA_VOZ_EN': claves.get('cartesia_voz_en'),
        'VOZ_STREAM_TOKEN': token(datos),
        'VOZ_IDIOMA': negocio(datos).get('idioma'),
    }
    for variable, valor in equivalencias.items():
        if valor:
            os.environ[variable] = str(valor)

    return equivalencias


def revisar(config=None):
    """Qué funciona y qué no. Devuelve (bloqueos, telefonia, avisos).

    Tres listas y no una, porque hay dos formas distintas de estar roto y confundirlas
    cuesta tiempo el peor dia:

    - **bloqueos**: el agente no puede ni hablar. Nada funciona, ni por navegador.
    - **telefonia**: la demo por navegador va perfecta, pero por teléfono no entraría
      ni saldría una llamada. Es el estado normal mientras no hay túnel levantado, y
      pintarlo en rojo hace buscar un problema que no existe.
    - **avisos**: funciona, pero peor de lo que podría.

    Nació de que el fallo típico de esto no es una excepción sino una llamada que
    suena y sale muda, y averiguar por qué cuesta una llamada por intento.
    """
    datos = config if config is not None else cargar()
    bloqueos, telefonia, avisos = [], [], []

    # Un aviso y no un bloqueo: con el espejo del fichero el agente atiende igual, y
    # dejar la demo sin llamadas porque Postgres no arranca seria cambiar un
    # problema pequeno por uno grande. Pero hay que decirlo, o se estaria trabajando
    # sobre una copia vieja sin saberlo.
    if desde_respaldo():
        avisos.append('No se pudo leer la base: se esta usando la copia de '
                      'config.json. Lo que guardes ahora puede no cuadrar con lo '
                      'que hay en Postgres.')

    if not negocio(datos).get('nombre'):
        bloqueos.append('El negocio no tiene nombre: el agente no sabe quien es.')
    if not ia(datos).get('anthropic_api_key'):
        bloqueos.append('Falta ia.anthropic_api_key: el agente no puede pensar.')

    if not url_publica(datos):
        telefonia.append('Falta la URL publica: el proveedor no sabria a donde pedir '
                         'las instrucciones y la llamada saldria muda.')
    if not token(datos):
        telefonia.append('Falta el secreto compartido: el puente rechazaria el audio.')
    if not url_stream(datos):
        telefonia.append('Falta la URL del audio: las llamadas se atenderian con un '
                         'mensaje fijo y se colgarian, sin agente.')

    datos_carrier = carrier(datos)
    if not datos_carrier.get('api_token'):
        telefonia.append('Sin el token del proveedor no se pueden hacer llamadas '
                         'salientes (las entrantes no lo necesitan).')
    elif not datos_carrier.get('numero_origen'):
        telefonia.append('Sin numero de origen el proveedor rechaza la llamada '
                         'saliente.')

    if not ia(datos).get('cartesia_api_key'):
        avisos.append('Sin clave de Cartesia la voz va por edge-tts, que tarda '
                      '~1,9 s en la primera frase y por telefono se nota.')
    if not ia(datos).get('deepgram_api_key'):
        avisos.append('Sin clave de Deepgram se transcribe con Whisper local, que '
                      'sobre audio telefonico entiende bastante peor.')

    return bloqueos, telefonia, avisos


def guardar(cambios):
    """Escribe `config.json` mezclando `cambios` sobre lo que ya hay.

    Mezcla en vez de sustituir, y por secciones: el panel sólo manda lo que enseña,
    y un `config.json` editado a mano puede tener claves que el panel no conoce
    todavía. Con una sustitución completa, guardar desde el panel las borraría sin
    avisar.

    La escritura es atómica —fichero temporal y `os.replace`—: si el proceso muere a
    mitad, `config.json` queda como estaba en vez de truncado. Un fichero de
    configuración a medias deja el servicio sin arrancar, y es justo el momento en
    que menos se puede depurar.
    """
    actual = cargar(recargar=True)
    fusionado = {k: dict(v) if isinstance(v, dict) else v for k, v in actual.items()}

    for seccion, valores in (cambios or {}).items():
        if isinstance(valores, dict):
            fusionado.setdefault(seccion, {})
            if not isinstance(fusionado[seccion], dict):
                fusionado[seccion] = {}
            fusionado[seccion].update(valores)
        else:
            fusionado[seccion] = valores

    # Primero la base, que es la fuente. Si falla se sigue igualmente y se escribe el
    # fichero: perder un cambio recien hecho por no poder hablar con Postgres seria
    # peor que quedarse con las dos copias descuadradas un rato, y la siguiente
    # carga lo dice arriba del panel.
    en_bd = _escribir_en_bd(fusionado.get(SECCION_CONEXION) or {}, fusionado)
    if not en_bd:
        logger.warning('Los ajustes se guardaron solo en el fichero: la base no '
                       'contesto')

    # El espejo. Es lo que permite que el agente siga atendiendo llamadas si la base
    # no arranca: sin el, quedarse sin Postgres seria quedarse sin servicio.
    temporal = f'{RUTA_CONFIG}.tmp'
    with open(temporal, 'w', encoding='utf-8', newline='\n') as archivo:
        json.dump(fusionado, archivo, indent=2, ensure_ascii=False)
        archivo.write('\n')
    os.replace(temporal, RUTA_CONFIG)

    global _cache
    _cache = fusionado
    # Las claves nuevas tienen que llegar al motor, que las lee del entorno. Sin
    # esto se guardaría una clave de Cartesia y las llamadas seguirían con la vieja
    # —o sin ninguna— hasta reiniciar, que es exactamente el tipo de fallo que
    # parece del proveedor y no lo es.
    aplicar_entorno(fusionado)
    return fusionado


def clave_acceso(config=None):
    """La frase que deja entrar desde fuera. Vacia = solo desde esta maquina."""
    return (web(config).get('clave_acceso') or '').strip()


def enlaces_compartibles(config=None):
    """Los enlaces que se le pueden pasar a alguien, con la clave ya puesta.

    Salen de `url_publica`, que es la del tunel. Si no hay tunel no hay nada que
    compartir, y decirlo es mas util que devolver un enlace a localhost que en el
    ordenador de otro no abre nada.
    """
    datos = config if config is not None else cargar()
    base = url_publica(datos)
    clave = clave_acceso(datos)
    if not base:
        return {}
    sufijo = f'?k={clave}' if clave else ''
    return {
        'demo': f'{base}/{sufijo}',
        'panel': f'{base}/panel{sufijo}',
        'con_clave': bool(clave),
    }
