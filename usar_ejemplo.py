"""Carga un negocio de ejemplo sobre esta instalacion.

    python usar_ejemplo.py                    lista los que hay
    python usar_ejemplo.py panaderia          dice que cambiaria, sin tocar nada
    python usar_ejemplo.py panaderia --hazlo  lo aplica

Existe porque el producto y el negocio son cosas distintas y conviene que se note.
El codigo no sabe de panaderias: sabe atender un telefono, entender lo que le dicen
y apuntar lo que le piden. Quien es, que vende y que debe decir entra entero por
configuracion, y estos ficheros son ejemplos de eso.

**Solo toca lo que describe al negocio**: nombre, idioma, vocabulario, descripcion,
guiones y la franja horaria. No toca claves, ni telefonia, ni las URL, ni la conexion
a la base — eso es de la instalacion, no del negocio, y sobrescribirlo al probar otro
ejemplo dejaria el servicio sin poder llamar.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nucleo import ajustes

RAIZ = os.path.dirname(os.path.abspath(__file__))
CARPETA = os.path.join(RAIZ, 'ejemplos')

# Lo unico que un ejemplo puede cambiar. Todo lo demas describe la instalacion —con
# que credenciales, por que numero, contra que base— y no viaja con el negocio.
SECCIONES = ('negocio', 'llamadas')


def disponibles():
    if not os.path.isdir(CARPETA):
        return []
    return sorted(f[:-5] for f in os.listdir(CARPETA) if f.endswith('.json'))


def leer(nombre):
    ruta = os.path.join(CARPETA, f'{nombre}.json')
    if not os.path.exists(ruta):
        return None
    with open(ruta, encoding='utf-8') as archivo:
        return json.load(archivo)


def listar():
    nombres = disponibles()
    if not nombres:
        print(f'\n  No hay ejemplos en {CARPETA}\n')
        return 1
    print('\n  Negocios de ejemplo:\n')
    for nombre in nombres:
        datos = leer(nombre) or {}
        titulo = (datos.get('negocio') or {}).get('nombre', '')
        print(f'  {nombre:<14} {titulo}')
        detalle = datos.get('_descripcion', '')
        if detalle:
            print(f'  {"":<14} {detalle}')
        print()
    print('  Para usar uno:  python usar_ejemplo.py <nombre> --hazlo\n')
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Carga un negocio de ejemplo sobre esta instalacion')
    parser.add_argument('nombre', nargs='?', help='Cual. Sin esto, los lista.')
    parser.add_argument('--hazlo', action='store_true',
                        help='Aplica de verdad. Sin esto solo informa.')
    args = parser.parse_args()

    if not args.nombre:
        return listar()

    datos = leer(args.nombre)
    if datos is None:
        print(f'\n  No existe el ejemplo «{args.nombre}».')
        print(f'  Hay: {", ".join(disponibles()) or "ninguno"}\n')
        return 1

    cambios = {s: datos[s] for s in SECCIONES if s in datos}
    if not cambios:
        print(f'\n  El ejemplo «{args.nombre}» no describe ningun negocio.\n')
        return 1

    actual = ajustes.cargar(recargar=True)
    nombre_actual = ajustes.negocio(actual).get('nombre') or '(sin nombre)'
    nombre_nuevo = (cambios.get('negocio') or {}).get('nombre') or '(sin nombre)'

    print(f'\n  Ahora    : {nombre_actual}')
    print(f'  Pasaria a: {nombre_nuevo}')
    print(f'  Secciones: {", ".join(cambios)}')
    print('  No se tocan: claves, telefonia, URL ni la conexion a la base.')

    if not args.hazlo:
        print(f'\n  Esto es solo un aviso. Para hacerlo: '
              f'python usar_ejemplo.py {args.nombre} --hazlo\n')
        return 0

    ajustes.guardar(cambios)
    print(f'\n  Hecho. El agente ya es «{nombre_nuevo}».')
    print('  Los guiones se releen en cada llamada: no hace falta reiniciar.')
    print('  El idioma si, que la voz se elige al arrancar la llamada.\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
