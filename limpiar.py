"""Vacia el historial de pruebas antes de una demostracion.

    python limpiar.py                 dice que hay y que se llevaria, sin tocar nada
    python limpiar.py --hazlo         lo borra, dejando copia al lado

Existe porque el remate de la demo es abrir la pestana de Pedidos y ver el encargo
recien hecho. Veinte filas identicas de «torta de tres leches para Maria» encima
delatan que es un ensayo, y el pedido de verdad se pierde entre ellas.

**No toca lo que tiene transcripcion.** Una llamada con turnos guardados es el plan B
del guion —enseñar una conversacion ya mantenida si el navegador da guerra—, asi que
se queda aunque sea de prueba. Lo que se lleva son las fichas vacias que dejan las
tandas de `pruebas/`, que no sirven para enseñar nada.
"""

import argparse
import os
import shutil
import sqlite3
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get('VOZ_BD') or os.path.join(RAIZ, 'llamadas.db')


def candidatas(con):
    """Llamadas de prueba sin un solo turno registrado."""
    return [fila[0] for fila in con.execute(
        'SELECT id FROM llamada WHERE es_prueba = 1 AND id NOT IN '
        '(SELECT DISTINCT llamada_id FROM turno WHERE llamada_id IS NOT NULL)'
    )]


def main():
    parser = argparse.ArgumentParser(description='Limpia el historial de pruebas')
    parser.add_argument('--hazlo', action='store_true',
                        help='Borra de verdad. Sin esto solo informa.')
    args = parser.parse_args()

    if not os.path.exists(BASE):
        print(f'\n  No hay base de datos en {BASE}\n')
        return 1

    con = sqlite3.connect(BASE, timeout=10)
    ids = candidatas(con)
    total = con.execute('SELECT COUNT(*) FROM llamada').fetchone()[0]
    con_turnos = con.execute(
        'SELECT COUNT(DISTINCT llamada_id) FROM turno').fetchone()[0]

    if not ids:
        print(f'\n  Nada que limpiar: {total} llamada(s), ninguna de prueba vacia.\n')
        return 0

    marcas = ','.join('?' * len(ids))
    pedidos = con.execute(
        f'SELECT COUNT(*) FROM pedido WHERE llamada_id IN ({marcas})', ids).fetchone()[0]

    print(f'\n  Base            : {BASE}')
    print(f'  Llamadas        : {total}')
    print(f'  Con transcripcion: {con_turnos}  (intactas, son el plan B)')
    print(f'  Se llevaria     : {len(ids)} llamada(s) de prueba y {pedidos} pedido(s)')

    if not args.hazlo:
        print('\n  Esto es solo un aviso. Para hacerlo: python limpiar.py --hazlo\n')
        return 0

    copia = BASE + '.antes-de-limpiar'
    shutil.copy2(BASE, copia)

    con.execute(f'DELETE FROM pedido  WHERE llamada_id IN ({marcas})', ids)
    con.execute(f'DELETE FROM evento  WHERE llamada_id IN ({marcas})', ids)
    con.execute(f'DELETE FROM consumo WHERE llamada_id IN ({marcas})', ids)
    con.execute(f'DELETE FROM llamada WHERE id IN ({marcas})', ids)
    con.commit()

    print(f'\n  Hecho. Quedan {con.execute("SELECT COUNT(*) FROM llamada").fetchone()[0]} '
          f'llamada(s) y {con.execute("SELECT COUNT(*) FROM pedido").fetchone()[0]} pedido(s).')
    print(f'  Copia de antes  : {copia}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
