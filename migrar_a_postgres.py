"""Pasa el registro de llamadas de SQLite a PostgreSQL. Se ejecuta una sola vez.

    python migrar_a_postgres.py                dice que copiaria, sin tocar nada
    python migrar_a_postgres.py --hazlo        lo copia de verdad

El fichero `llamadas.db` **no se toca ni se borra**: es la copia de seguridad de la
migracion, y mientras exista siempre se puede volver atras dejando el `almacen.py`
anterior. Lo que hace este script es crear la base en Postgres si no esta, montar el
esquema y volcar las filas.

**Se conservan los identificadores.** El panel, los eventos y los pedidos se apuntan
unos a otros por id, asi que renumerar al copiar dejaria un pedido colgando de una
llamada que no es. Como se insertan ids a mano, al final hay que empujar las secuencias
por encima del maximo: sin eso, el primer INSERT de verdad chocaria con una fila ya
existente.

**Es idempotente por tabla**: si la tabla de destino ya tiene filas, esa tabla se salta
en vez de duplicarla. Para rehacer la migracion entera hay que vaciar antes el destino.
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg

from nucleo import ajustes, almacen

RAIZ = os.path.dirname(os.path.abspath(__file__))
ORIGEN = os.path.join(RAIZ, 'llamadas.db')

# Orden de copia: primero aquello a lo que los demas apuntan. `llamada` antes que
# `turno` y `pedido`, o quedarian huerfanos a mitad si algo falla.
TABLAS = (
    ('llamada', 'id'),
    ('turno', 'id'),
    ('evento', 'id'),
    ('consumo', 'id'),
    ('pedido', 'id'),
    ('no_llamar', None),
)


def crear_base_si_falta(datos):
    """CREATE DATABASE, que no puede ir dentro de una transaccion."""
    base = (datos.get('base') or 'vozagente').strip()
    comun = (f"host={datos.get('host') or 'localhost'} "
             f"port={datos.get('puerto') or 5432} "
             f"user={datos.get('usuario') or 'postgres'} "
             f"password={datos.get('clave') or ''}")
    with psycopg.connect(f'{comun} dbname=postgres', connect_timeout=10,
                         autocommit=True) as con:
        existe = con.execute('SELECT 1 FROM pg_database WHERE datname = %s',
                             (base,)).fetchone()
        if existe:
            return False
        con.execute(f'CREATE DATABASE "{base}"')
        return True


def columnas_comunes(origen, destino, tabla):
    """Solo se copia lo que existe en los dos lados.

    Si el esquema de destino gana una columna, esto no se entera y no se rompe; si la
    pierde, tampoco intenta escribirla.
    """
    del_origen = [f[1] for f in origen.execute(f'PRAGMA table_info({tabla})')]
    # La conexion del almacen entrega diccionarios, no tuplas: es lo que espera el
    # resto del programa, que lee las filas por nombre de columna.
    del_destino = [f['column_name'] for f in destino.execute(
        'SELECT column_name FROM information_schema.columns WHERE table_name = %s',
        (tabla,)).fetchall()]
    return [c for c in del_origen if c in del_destino]


def main():
    parser = argparse.ArgumentParser(
        description='Migra el registro de llamadas de SQLite a PostgreSQL')
    parser.add_argument('--hazlo', action='store_true',
                        help='Copia de verdad. Sin esto solo informa.')
    args = parser.parse_args()

    if not os.path.exists(ORIGEN):
        print(f'\n  No hay nada que migrar: no existe {ORIGEN}\n')
        return 1

    config = ajustes.cargar()
    datos = ajustes.bd(config)

    # La base se crea tambien en el simulacro. Es la unica forma de mirar el destino
    # para decir que se copiaria y que ya esta; y crear una base vacia no destruye
    # nada, que es lo que el simulacro promete.
    try:
        if crear_base_si_falta(datos):
            print(f"\n  Base «{datos.get('base')}» creada en PostgreSQL.")
    except Exception as ex:
        print(f'\n  No se puede hablar con PostgreSQL: {almacen.texto_seguro(ex)}\n')
        print('  Revisa la seccion «bd» de config.json.\n')
        return 1

    origen = sqlite3.connect(ORIGEN)
    origen.row_factory = sqlite3.Row
    try:
        destino = almacen.conexion()
    except Exception as ex:
        print(f'\n  No se puede abrir PostgreSQL: {almacen.texto_seguro(ex)}\n')
        print('  Revisa la seccion «bd» de config.json.\n')
        return 1

    print(f"\n  Origen : {ORIGEN}")
    print(f"  Destino: {datos.get('base') or 'vozagente'} en "
          f"{datos.get('host') or 'localhost'}:{datos.get('puerto') or 5432}\n")

    total = 0
    for tabla, clave in TABLAS:
        try:
            filas = origen.execute(f'SELECT * FROM {tabla}').fetchall()
        except sqlite3.OperationalError:
            print(f'  {tabla:<10} no existe en el origen, se salta')
            continue

        ya_hay = destino.execute(f'SELECT COUNT(*) AS n FROM {tabla}').fetchone()['n']
        if ya_hay:
            print(f'  {tabla:<10} {len(filas):>4} en origen — el destino ya tiene '
                  f'{ya_hay}, se salta')
            continue
        if not filas:
            print(f'  {tabla:<10}    0 — nada que copiar')
            continue

        if not args.hazlo:
            print(f'  {tabla:<10} {len(filas):>4} fila(s) se copiarian')
            total += len(filas)
            continue

        columnas = columnas_comunes(origen, destino, tabla)
        lista = ', '.join(columnas)
        marcas = ', '.join(['%s'] * len(columnas))
        with destino.cursor() as cur:
            for fila in filas:
                cur.execute(f'INSERT INTO {tabla} ({lista}) VALUES ({marcas})',
                            tuple(fila[c] for c in columnas))
        # La secuencia se queda en 1 porque los ids vinieron puestos. Empujarla al
        # maximo es lo que evita que el primer INSERT de verdad choque con una fila
        # que ya esta.
        if clave:
            destino.execute(
                f"SELECT setval(pg_get_serial_sequence('{tabla}', '{clave}'), "
                f"COALESCE((SELECT MAX({clave}) FROM {tabla}), 1))")
        destino.commit()
        print(f'  {tabla:<10} {len(filas):>4} fila(s) copiadas')
        total += len(filas)

    origen.close()
    destino.close()

    # Los ajustes no vienen del `llamadas.db` sino de `config.json`, asi que van
    # aparte. Se vuelcan siempre que no esten ya: son la misma migracion —dejar de
    # depender del fichero— aunque la fuente sea otra.
    if args.hazlo:
        if ajustes.volcar_a_bd(config):
            print('  ajustes    volcados a la base desde config.json')
        else:
            print('  [!] los ajustes no se pudieron volcar a la base')
    else:
        print('  ajustes    se volcarian desde config.json')

    if not args.hazlo:
        print(f'\n  Esto es solo un aviso: {total} fila(s) en total.')
        print('  Para hacerlo: python migrar_a_postgres.py --hazlo\n')
        return 0

    print(f'\n  Hecho: {total} fila(s) migradas.')
    print(f'  {ORIGEN} se queda donde esta, como copia de seguridad.\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
