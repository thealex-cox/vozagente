"""Registro de llamadas en SQLite.

En skytech esto era Postgres y el puente de audio le mandaba los turnos **por HTTP**,
porque vivía en otro proceso sin ORM. Aquí el puente y la web son el mismo proceso,
así que se escribe directo: desaparecen el salto HTTP, su token y la clase de fallos
en que el audio va bien y la transcripción se pierde por un 403.

Tres reglas, y las tres vienen de estar en el camino del audio:

- **Nunca levanta hacia fuera.** Que el registro falle no puede cortar una llamada.
- **Una conexión por operación.** SQLite no comparte conexión entre hilos, y aquí
  escriben el bucle de eventos y los hilos del sintetizador.
- **La conversación se guarda turno a turno**, no al colgar. Una llamada que se corta
  o un reinicio dejarían la conversación entera sin registrar; lo que llegó, queda.
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA = os.environ.get('VOZ_BD') or os.path.join(RAIZ, 'llamadas.db')

_lock = threading.Lock()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS llamada (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creada_en TEXT NOT NULL,
    direccion TEXT NOT NULL,
    telefono TEXT NOT NULL,
    pais TEXT DEFAULT '',
    estado TEXT DEFAULT 'encolada',
    carrier TEXT DEFAULT '',
    carrier_call_id TEXT DEFAULT '',
    duracion_segundos INTEGER DEFAULT 0,
    costo REAL DEFAULT 0,
    resumen TEXT DEFAULT '',
    error TEXT DEFAULT '',
    es_prueba INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turno (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    llamada_id INTEGER NOT NULL,
    creado_en TEXT NOT NULL,
    sesion TEXT DEFAULT '',
    orden INTEGER DEFAULT 0,
    rol TEXT NOT NULL,
    texto TEXT DEFAULT '',
    latencia_ms INTEGER DEFAULT 0,
    interrumpido INTEGER DEFAULT 0,
    UNIQUE (llamada_id, sesion, orden)
);

CREATE TABLE IF NOT EXISTS evento (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    llamada_id INTEGER NOT NULL,
    creado_en TEXT NOT NULL,
    tipo TEXT NOT NULL,
    detalle TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS consumo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    llamada_id INTEGER NOT NULL,
    creado_en TEXT NOT NULL,
    proveedor TEXT NOT NULL,
    unidad TEXT NOT NULL,
    unidades REAL DEFAULT 0,
    detalle TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS no_llamar (
    telefono TEXT PRIMARY KEY,
    creado_en TEXT NOT NULL,
    motivo TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_llamada_sid ON llamada (carrier_call_id);
CREATE INDEX IF NOT EXISTS idx_turno_llamada ON turno (llamada_id);
CREATE INDEX IF NOT EXISTS idx_evento_llamada ON evento (llamada_id);
"""


def _ahora():
    return datetime.now().isoformat(timespec='seconds')


def conexion():
    """Una conexión nueva, ya inicializada.

    `check_same_thread=False` porque el sintetizador escribe desde un hilo del
    executor. El `_lock` de este módulo es lo que serializa las escrituras: sin él,
    dos turnos simultáneos dan «database is locked» a mitad de conversación.
    """
    con = sqlite3.connect(RUTA, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    # WAL: el lector del panel no bloquea al puente que está escribiendo turnos.
    con.execute('PRAGMA journal_mode=WAL')
    # El esquema se asegura en cada conexión, no una sola vez con una bandera de
    # módulo. Con la bandera, borrar o mover `llamadas.db` con el servicio en marcha
    # dejaba el proceso creyendo que las tablas existían: a partir de ahí toda
    # escritura fallaba con «no such table», y como el registro nunca levanta hacia
    # fuera, la conversación seguía y se perdía entera en silencio. `IF NOT EXISTS`
    # hace que esto sea barato y no toca nada si ya está.
    con.executescript(ESQUEMA)
    con.executescript(ESQUEMA_PEDIDOS)
    _asegurar_columnas(con)
    con.commit()
    return con


# Columnas anadidas despues de que la tabla ya existiera en alguna instalacion.
# `CREATE TABLE IF NOT EXISTS` no las anade a una tabla vieja, asi que sin esto una
# base creada antes seguiria sin la columna y toda escritura fallaria — y como el
# registro nunca levanta hacia fuera, fallaria en silencio.
COLUMNAS_ANADIDAS = (
    ('pedido', 'reemplazado_por', 'INTEGER'),
)


def _asegurar_columnas(con):
    for tabla, columna, tipo in COLUMNAS_ANADIDAS:
        existentes = {f[1] for f in con.execute(f'PRAGMA table_info({tabla})')}
        if existentes and columna not in existentes:
            con.execute(f'ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}')


def _escribir(sentencia, parametros=(), devolver_id=False):
    """Ejecuta y confirma. Devuelve el id insertado, o None si algo falló.

    Traga la excepción a propósito: esto se llama desde el camino del audio y una
    base bloqueada no puede tumbar una llamada en curso. Queda en el log.
    """
    try:
        with _lock:
            con = conexion()
            try:
                cursor = con.execute(sentencia, parametros)
                con.commit()
                return cursor.lastrowid if devolver_id else True
            finally:
                con.close()
    except Exception as ex:
        logger.warning(f'No se pudo escribir en el registro: {ex}')
        return None


def leer(sentencia, parametros=()):
    try:
        with _lock:
            con = conexion()
            try:
                return [dict(f) for f in con.execute(sentencia, parametros).fetchall()]
            finally:
                con.close()
    except Exception as ex:
        logger.warning(f'No se pudo leer el registro: {ex}')
        return []


def crear_llamada(direccion, telefono, pais='', carrier='', carrier_call_id='',
                  estado='encolada', es_prueba=False):
    return _escribir(
        'INSERT INTO llamada (creada_en, direccion, telefono, pais, estado, carrier, '
        'carrier_call_id, es_prueba) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (_ahora(), direccion, telefono, pais, estado, carrier, carrier_call_id,
         1 if es_prueba else 0),
        devolver_id=True,
    )


def llamada(llamada_id):
    filas = leer('SELECT * FROM llamada WHERE id = ?', (llamada_id,))
    return filas[0] if filas else None


def llamada_por_sid(call_sid):
    """Para que un reintento del webhook no cree una segunda ficha de la misma
    llamada: el carrier reintenta, y sin esto cada intento dejaba una fila."""
    if not call_sid:
        return None
    filas = leer('SELECT * FROM llamada WHERE carrier_call_id = ? ORDER BY id DESC LIMIT 1',
                 (call_sid,))
    return filas[0] if filas else None


def actualizar_llamada(llamada_id, **campos):
    """Escribe **sólo** las columnas que se pasan.

    Nunca un UPDATE completo: al colgar, el webhook de estado del carrier y el
    último turno del puente llegan a la vez, y reescribir la fila entera devolvía la
    transcripción a como estaba un segundo antes — borrando justo lo único que la
    llamada produce. Fue un fallo real en skytech.
    """
    permitidas = {'estado', 'carrier', 'carrier_call_id', 'duracion_segundos',
                  'costo', 'resumen', 'error', 'pais', 'es_prueba'}
    campos = {k: v for k, v in campos.items() if k in permitidas}
    if not campos or not llamada_id:
        return None
    asignaciones = ', '.join(f'{k} = ?' for k in campos)
    return _escribir(f'UPDATE llamada SET {asignaciones} WHERE id = ?',
                     tuple(campos.values()) + (llamada_id,))


def anotar_evento(llamada_id, tipo, detalle=''):
    if not llamada_id:
        return None
    return _escribir(
        'INSERT INTO evento (llamada_id, creado_en, tipo, detalle) VALUES (?, ?, ?, ?)',
        (llamada_id, _ahora(), tipo, str(detalle)[:500]))


def anotar_turno(llamada_id, rol, texto, sesion='', orden=0, latencia_ms=0,
                 interrumpido=False):
    """Idempotente por (llamada, sesión, orden): si un turno se reintenta se
    actualiza en lugar de duplicarse."""
    if not llamada_id:
        return None
    return _escribir(
        'INSERT INTO turno (llamada_id, creado_en, sesion, orden, rol, texto, '
        'latencia_ms, interrumpido) VALUES (?, ?, ?, ?, ?, ?, ?, ?) '
        'ON CONFLICT (llamada_id, sesion, orden) DO UPDATE SET '
        'texto = excluded.texto, latencia_ms = excluded.latencia_ms, '
        'interrumpido = excluded.interrumpido',
        (llamada_id, _ahora(), sesion, orden, rol, texto[:5000], latencia_ms,
         1 if interrumpido else 0))


def marcar_interrumpido(llamada_id, sesion, orden):
    if not llamada_id:
        return None
    return _escribir(
        'UPDATE turno SET interrumpido = 1 WHERE llamada_id = ? AND sesion = ? AND orden = ?',
        (llamada_id, sesion, orden))


def turnos(llamada_id):
    return leer('SELECT * FROM turno WHERE llamada_id = ? ORDER BY sesion, orden',
                (llamada_id,))


def eventos(llamada_id):
    return leer('SELECT * FROM evento WHERE llamada_id = ? ORDER BY id', (llamada_id,))


def anotar_consumo(llamada_id, consumos):
    if not llamada_id or not consumos:
        return None
    for item in consumos:
        _escribir(
            'INSERT INTO consumo (llamada_id, creado_en, proveedor, unidad, unidades, '
            'detalle) VALUES (?, ?, ?, ?, ?, ?)',
            (llamada_id, _ahora(), item.get('proveedor', ''), item.get('unidad', ''),
             float(item.get('unidades', 0) or 0), str(item.get('detalle', ''))[:255]))
    return True


def consumos(llamada_id):
    return leer('SELECT * FROM consumo WHERE llamada_id = ? ORDER BY id', (llamada_id,))


def transcripcion(llamada_id):
    """La conversación como texto corrido, para leerla de un vistazo."""
    etiquetas = {'cliente': 'Cliente', 'agente': 'Agente'}
    return '\n'.join(
        f"{etiquetas.get(t['rol'], t['rol'])}: {t['texto']}"
        for t in turnos(llamada_id) if (t['texto'] or '').strip())


def listar_llamadas(limite=50):
    return leer('SELECT * FROM llamada ORDER BY id DESC LIMIT ?', (limite,))


def bloquear(telefono, motivo=''):
    """Lista de no-llamar. Es la única lista que no admite excepciones: quien pide no
    recibir llamadas no las pidió sólo de un tipo."""
    return _escribir(
        'INSERT INTO no_llamar (telefono, creado_en, motivo) VALUES (?, ?, ?) '
        'ON CONFLICT (telefono) DO UPDATE SET motivo = excluded.motivo',
        (telefono, _ahora(), motivo[:255]))


def esta_bloqueado(telefono):
    if not telefono:
        return False
    return bool(leer('SELECT 1 FROM no_llamar WHERE telefono = ?', (telefono,)))


def bloqueados():
    return leer('SELECT * FROM no_llamar ORDER BY creado_en DESC')


# ---- pedidos -------------------------------------------------------------
#
# Lo unico que el agente puede escribir. Deliberadamente generico: `resumen` y
# `detalles` en texto libre en vez de campos por sector, porque el mismo servicio
# atiende a una panaderia, a una clinica y a un taller, y un esquema con «sabor» y
# «porciones» obligaria a tocar la base para cada negocio nuevo.

ESQUEMA_PEDIDOS = """
CREATE TABLE IF NOT EXISTS pedido (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creado_en TEXT NOT NULL,
    llamada_id INTEGER,
    telefono TEXT DEFAULT '',
    nombre TEXT DEFAULT '',
    resumen TEXT NOT NULL,
    cuando_texto TEXT DEFAULT '',
    detalles TEXT DEFAULT '',
    atendido INTEGER DEFAULT 0,
    atendido_en TEXT DEFAULT '',
    reemplazado_por INTEGER
);

CREATE INDEX IF NOT EXISTS idx_pedido_llamada ON pedido (llamada_id);
CREATE INDEX IF NOT EXISTS idx_pedido_atendido ON pedido (atendido);
"""


def crear_pedido(llamada_id, resumen, nombre='', cuando_texto='', detalles='',
                 telefono=''):
    """Guarda lo que el cliente ha pedido. Devuelve el id, o None si no se pudo.

    Devolver None y no levantar es lo que permite que el agente diga la verdad: si
    esto falla, la herramienta se lo dice y el agente ofrece que un compañero lo
    confirme, en vez de asegurar que quedó anotado cuando no.
    """
    if not (resumen or '').strip():
        return None
    return _escribir(
        'INSERT INTO pedido (creado_en, llamada_id, telefono, nombre, resumen, '
        'cuando_texto, detalles) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (_ahora(), llamada_id, telefono[:20], nombre[:150], resumen[:2000],
         cuando_texto[:150], detalles[:2000]),
        devolver_id=True)


def listar_pedidos(limite=100, solo_pendientes=False):
    if solo_pendientes:
        return leer('SELECT * FROM pedido WHERE atendido = 0 ORDER BY id DESC LIMIT ?',
                    (limite,))
    return leer('SELECT * FROM pedido ORDER BY id DESC LIMIT ?', (limite,))


def pedidos_de_llamada(llamada_id):
    return leer('SELECT * FROM pedido WHERE llamada_id = ? ORDER BY id', (llamada_id,))


def marcar_pedido(pedido_id, atendido=True):
    """Atendido o no. Con vuelta atras a proposito: el agente esta escuchando por
    telefono y va a apuntar algun falso positivo, asi que hay que poder deshacerlo
    sin borrar la fila — la fila es la evidencia de lo que se dijo en la llamada."""
    return _escribir(
        'UPDATE pedido SET atendido = ?, atendido_en = ? WHERE id = ?',
        (1 if atendido else 0, _ahora() if atendido else '', pedido_id))


def pedidos_pendientes():
    filas = leer('SELECT COUNT(*) AS total FROM pedido '
                 'WHERE atendido = 0 AND reemplazado_por IS NULL')
    return filas[0]['total'] if filas else 0


def reemplazar_pedido(viejo_id, nuevo_id):
    """Marca un pedido como sustituido por otro.

    No se borra: la fila es la evidencia de lo que el cliente dijo en la llamada, y
    un encargo corregido cuenta la historia entera —lo que pidio primero y con que se
    quedo—. Lo que hace el panel es dejar de contarlo como pendiente.
    """
    if not viejo_id or not nuevo_id or viejo_id == nuevo_id:
        return None
    return _escribir(
        'UPDATE pedido SET reemplazado_por = ?, atendido = 1, atendido_en = ? '
        'WHERE id = ? AND reemplazado_por IS NULL',
        (nuevo_id, _ahora(), viejo_id))
