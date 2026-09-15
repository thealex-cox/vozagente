"""Hace una llamada saliente desde la terminal.

    python llamar.py +593999123456
    python llamar.py +593999123456 --ahora     # se salta la ventana horaria
    python llamar.py --revisar                 # comprueba la cadena sin gastar nada

El servicio tiene que estar corriendo (`python servidor.py`) y su URL pública tiene
que llegar hasta él: el proveedor no marca hasta saber qué decir, y lo pregunta por
esa URL cuando el destinatario descuelga.

**Esto marca de verdad y cuesta dinero.** Pide confirmación antes, con el número y el
identificador de origen delante, porque el fallo caro de esto no es técnico: es
llamar al número equivocado.
"""

import argparse
import sys

from nucleo import ajustes, almacen, carrier as carrier_mod, telefono


def revisar(config, carrier):
    """Enseña la cadena entera antes de gastar una llamada.

    Nació de que el fallo típico no es una excepción sino una llamada que se encola y
    nunca suena, y averiguar por qué cuesta una llamada por intento.
    """
    print()
    bloqueos, telefonia, avisos = ajustes.revisar(config)
    print(f'  Negocio        : {ajustes.negocio(config).get("nombre") or "(sin nombre)"}')
    print(f'  URL publica    : {ajustes.url_publica() or "(sin configurar)"}')
    print(f'  Puente de audio: {ajustes.url_stream() or "(sin configurar: mensaje fijo)"}')

    if carrier is None:
        print('  Proveedor      : sin configurar, no se puede marcar')
    else:
        print(f'  Proveedor      : {carrier.nombre}, marcando desde {carrier.numero_origen}')
        try:
            numeros = carrier.numeros()
            print(f'  Credenciales   : aceptadas; {len(numeros)} numero(s): {", ".join(numeros)}')
            if carrier.numero_origen not in numeros:
                print(f'  [!] {carrier.numero_origen} no esta en la cuenta: el proveedor '
                      f'rechazara la llamada')
        except carrier_mod.CarrierError as ex:
            print(f'  [!] {ex}')

    for aviso in avisos:
        print(f'  [~] {aviso}')
    # Para marcar, lo de telefonia bloquea igual que lo demas.
    for bloqueo in bloqueos + telefonia:
        print(f'  [!] {bloqueo}')
    print()
    return not (bloqueos or telefonia)


def main():
    parser = argparse.ArgumentParser(description='Hace una llamada saliente')
    parser.add_argument('telefono', nargs='?', default='',
                        help='Numero de destino, en cualquier formato')
    parser.add_argument('--ahora', action='store_true',
                        help='Marca aunque este fuera de la franja horaria del destinatario')
    parser.add_argument('--revisar', action='store_true',
                        help='Comprueba la cadena y sale, sin marcar')
    parser.add_argument('--si', action='store_true',
                        help='No pide confirmacion. Para scripts, no para probar a mano')
    args = parser.parse_args()

    try:
        config = ajustes.cargar()
    except ajustes.ConfigError as ex:
        print(f'\n  {ex}\n', file=sys.stderr)
        return 2

    ajustes.aplicar_entorno(config)
    carrier = carrier_mod.construir(ajustes.carrier(config))

    if args.revisar or not args.telefono:
        revisar(config, carrier)
        return 0 if args.revisar else 1

    if carrier is None:
        print('\n  No hay proveedor configurado: revisa la seccion "carrier" de '
              'config.json.\n', file=sys.stderr)
        return 2

    pais_defecto = str(ajustes.llamadas(config).get('pais_defecto') or '593')
    numero = telefono.normalizar(args.telefono, pais_defecto)
    if not numero:
        print(f'\n  {args.telefono!r} no es un numero utilizable. Escribelo con el '
              f'prefijo del pais, por ejemplo +593999123456.\n', file=sys.stderr)
        return 2

    # La lista de no-llamar no admite excepciones, ni siquiera con --ahora: quien
    # pidio no recibir llamadas no las pidio solo de un tipo.
    if almacen.esta_bloqueado(numero):
        print(f'\n  {numero} esta en la lista de no-llamar. No se marca.\n', file=sys.stderr)
        return 3

    permitido, motivo = telefono.puede_llamar(numero, ajustes.llamadas(config))
    if not permitido and not args.ahora:
        siguiente = telefono.proximo_momento_valido(numero, ajustes.llamadas(config))
        print(f'\n  {motivo}')
        if siguiente:
            print(f'  Se podria llamar a partir de las {siguiente:%H:%M} del {siguiente:%d/%m}.')
        print('  Usa --ahora para marcar igualmente.\n', file=sys.stderr)
        return 4

    if not ajustes.url_publica():
        print('\n  Falta web.url_publica: el proveedor no sabria a donde pedir las '
              'instrucciones y la llamada saldria muda.\n', file=sys.stderr)
        return 2

    print()
    print(f'  Llamar a  : {telefono.formatear(numero)}')
    print(f'  Desde     : {telefono.formatear(carrier.numero_origen)}')
    print(f'  Como      : {ajustes.negocio(config).get("nombre")}')
    if not permitido:
        print(f'  [~] Fuera de horario, se marca igual: {motivo}')
    if not args.si:
        # Se pide con el numero delante y no antes de resolverlo: lo que hay que
        # confirmar es el numero al que se va a marcar de verdad, no el que se tecleo.
        respuesta = input('\n  ¿Marcar ahora? [s/N] ').strip().lower()
        if respuesta not in ('s', 'si', 'sí', 'y', 'yes'):
            print('  Cancelado.\n')
            return 0

    llamada_id = almacen.crear_llamada(
        direccion='saliente', telefono=numero, pais=telefono.pais(numero),
        carrier=carrier.nombre, estado='marcando', es_prueba=True)
    if not permitido:
        # Queda anotado aunque se haya forzado: el dia que alguien reclame por una
        # llamada a deshora hay que poder ver que salio de aqui y con que motivo.
        almacen.anotar_evento(llamada_id, 'error',
                              f'Franja horaria ignorada desde la terminal: {motivo}')

    base = ajustes.url_publica()
    token = ajustes.token()
    try:
        resultado = carrier.iniciar_llamada(
            numero,
            url_webhook=f'{base}/twiml/{llamada_id}?t={token}',
            url_estado=f'{base}/estado/{llamada_id}?t={token}',
        )
    except carrier_mod.CarrierError as ex:
        almacen.actualizar_llamada(llamada_id, estado='fallida', error=str(ex)[:500])
        almacen.anotar_evento(llamada_id, 'error', str(ex))
        print(f'\n  El proveedor rechazo la llamada:\n  {ex}\n', file=sys.stderr)
        return 5

    almacen.actualizar_llamada(llamada_id, carrier_call_id=resultado.call_id)
    almacen.anotar_evento(llamada_id, 'dialed', f'Marcada por {carrier.nombre}')
    print(f'\n  Marcando. Llamada {llamada_id} ({resultado.call_id}).')
    print(f'  La conversacion aparece en http://localhost:8600/llamadas/{llamada_id}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
