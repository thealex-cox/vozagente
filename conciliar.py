"""Pregunta al proveedor como acabaron de verdad las llamadas y lo escribe.

    python conciliar.py                 dice que corregiria, sin tocar nada
    python conciliar.py --hazlo         lo escribe, dejando copia al lado

**El proveedor es la fuente de la verdad, y para las entrantes es la unica.** El aviso
de estado del carrier viaja en la peticion que crea la llamada, asi que solo lo tienen
las salientes; una entrante se cierra sola cuando el puente termina bien
(`bitacora.cerrar`). Cuando no termina bien —el servicio se cae, se reinicia con una
llamada en curso, se cierra la terminal— la ficha se queda «en curso» con duracion cero
para siempre, y eso es justo lo que se proyecta en la pestana de Llamadas.

El **coste** ademas no lo rellenaba nadie en las entrantes: se sabe al facturar y solo
lo tiene el proveedor. Sin el, la pregunta «cuanto cuesta esto» se contesta de memoria.

Dos reglas, y las dos por lo mismo —no borrar informacion que ya costo obtener—:

- **El estado solo se escribe si sigue «en curso».** El aviso del proveedor distingue
  «ocupado» de «sin respuesta»; pisarlo con un «finalizada» generico borra el motivo.
- **No se toca lo que el proveedor no conoce.** Sin `carrier_call_id` no hay a quien
  preguntar: son las sesiones de la demo por navegador, que no son llamadas.
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg

from nucleo import ajustes, almacen, carrier as carrier_mod

RAIZ = os.path.dirname(os.path.abspath(__file__))


def pendientes(con):
    """Llamadas que el proveedor conoce y de las que nos falta algo por saber."""
    return con.execute(
        "SELECT id, creada_en, direccion, telefono, estado, duracion_segundos, costo, "
        "carrier_call_id FROM llamada "
        "WHERE carrier_call_id IS NOT NULL AND carrier_call_id != '' "
        "AND (estado = 'en curso' OR costo IS NULL OR costo = 0) "
        "ORDER BY id"
    ).fetchall()


def correccion(fila, datos):
    """Que columnas cambian para esta llamada. Vacio = el registro ya estaba bien."""
    _id, _creada, _dir, _tel, estado, duracion, costo, _cid = fila
    campos = {}

    nuevo_estado = carrier_mod.ESTADOS.get(str(datos.get('status') or ''))
    if nuevo_estado and estado == 'en curso' and nuevo_estado != estado:
        campos['estado'] = nuevo_estado

    cruda = str(datos.get('duration') or '')
    if cruda.isdigit() and int(cruda) != (duracion or 0):
        campos['duracion_segundos'] = int(cruda)

    precio = datos.get('price')
    if precio not in (None, ''):
        try:
            # El proveedor lo da como gasto, que es negativo en su contabilidad.
            valor = round(abs(float(precio)), 4)
        except (TypeError, ValueError):
            valor = None
        if valor is not None and valor != round(float(costo or 0), 4):
            campos['costo'] = valor

    return campos


def main():
    parser = argparse.ArgumentParser(
        description='Concilia el registro de llamadas con lo que dice el proveedor')
    parser.add_argument('--hazlo', action='store_true',
                        help='Escribe de verdad. Sin esto solo informa.')
    args = parser.parse_args()

    config = ajustes.cargar()
    carrier = carrier_mod.construir(config.get('carrier'))
    if carrier is None:
        print('\n  No hay carrier configurado: no hay a quien preguntar.\n')
        return 1

    try:
        con = psycopg.connect(ajustes.dsn(config), connect_timeout=10)
    except Exception as ex:
        print(f'\n  No se puede abrir la base: {almacen.texto_seguro(ex)}\n')
        return 1
    filas = pendientes(con)
    if not filas:
        print('\n  Nada que conciliar: ninguna llamada a medio cerrar.\n')
        return 0

    print(f"\n  Base     : {ajustes.bd(config).get('base') or 'vozagente'} (PostgreSQL)")
    print(f'  Proveedor: {carrier.nombre}')
    print(f'  Revisando: {len(filas)} llamada(s)\n')

    cambios = []
    for fila in filas:
        llamada_id = fila[0]
        datos = carrier.consultar_llamada(fila[7])
        if not datos:
            print(f'  {llamada_id:>4}  el proveedor no responde por esta llamada')
            continue
        campos = correccion(fila, datos)
        if not campos:
            print(f'  {llamada_id:>4}  ya estaba bien')
            continue
        detalle = ', '.join(f'{k}: {fila[i]} -> {v}' for k, v, i in (
            ('estado', campos.get('estado'), 4),
            ('duracion', campos.get('duracion_segundos'), 5),
            ('costo', campos.get('costo'), 6),
        ) if v is not None)
        print(f'  {llamada_id:>4}  {fila[2]:<9} {detalle}')
        cambios.append((llamada_id, campos))

    if not cambios:
        print('\n  Todo cuadra con el proveedor. No hay nada que escribir.\n')
        return 0

    if not args.hazlo:
        print(f'\n  Esto es solo un aviso: {len(cambios)} llamada(s) por corregir.')
        print('  Para hacerlo: python conciliar.py --hazlo\n')
        return 0

    copia = almacen.volcar_a_fichero(
        os.path.join(RAIZ, 'llamadas.antes-de-conciliar.sql'))
    if not copia:
        print('  [!] No se pudo hacer la copia de seguridad: se sigue igualmente.')

    for llamada_id, campos in cambios:
        asignaciones = ', '.join(f'{k} = %s' for k in campos)
        con.execute(f'UPDATE llamada SET {asignaciones} WHERE id = %s',
                    tuple(campos.values()) + (llamada_id,))
        con.execute(
            'INSERT INTO evento (llamada_id, creado_en, tipo, detalle) '
            'VALUES (%s, %s, %s, %s)',
            (llamada_id, datetime.now().isoformat(timespec='seconds'),
             'conciliado', 'Corregido con lo que dice el proveedor'))
    con.commit()

    print(f'\n  Hecho: {len(cambios)} llamada(s) corregida(s).')
    if copia:
        print(f'  Copia de antes: {copia}\n')
    else:
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
