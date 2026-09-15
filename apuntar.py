"""Apunta el numero de la demo al tunel que este levantado ahora mismo.

    python apuntar.py https://loquesea.trycloudflare.com
    python apuntar.py                 usa la que ya haya guardada, y solo verifica

Hace de una vez los dos pasos que van juntos y se separan siempre: guardar la URL en
`config.json` y **repuntar el webhook del numero en el proveedor**. El panel solo hace
el primero, asi que despues de reiniciar el tunel la llamada entra, el proveedor pide
el TwiML a un dominio que ya no existe y la llamada se cae al descolgar. El dominio de
cloudflared cambia en cada arranque, o sea que esto toca en cada arranque.

**Nunca toca el numero de produccion de skytech.** Vive en la misma cuenta del
proveedor y su webhook apunta a `skytech-geo.com`: repuntarlo a un tunel de pruebas
dejaria sin atender las llamadas de verdad. Esta escrito como una negativa explicita y
no como un descuido que no llegue a pasar.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

from nucleo import ajustes, carrier as carrier_mod

# El numero de skytech en produccion. Esta lista es el unico motivo por el que este
# script puede negarse a hacer lo que se le pide.
INTOCABLES = ('+12083798293',)


def numero_de_la_demo(config):
    """El numero al que apunta la demo. Explicito, nunca deducido.

    No se usa `numero_origen`: ese dice con que identificador SALEN las llamadas, no
    cual las RECIBE, y ahora mismo apunta al numero de produccion. Deducir uno del
    otro es como se repunta por error el numero que no era.
    """
    return (config.get('carrier', {}).get('numero_entrante') or '').strip()


def main():
    parser = argparse.ArgumentParser(
        description='Apunta el numero de la demo al tunel actual')
    parser.add_argument('url', nargs='?', default='',
                        help='URL publica del tunel. Sin ella se usa la guardada.')
    parser.add_argument('--numero', default='',
                        help='Numero a repuntar. Por defecto, carrier.numero_entrante.')
    args = parser.parse_args()

    config = ajustes.cargar(recargar=True)

    base = (args.url or ajustes.url_publica(config)).strip().rstrip('/')
    if not base:
        print('\n  No hay URL. Pasala como argumento:')
        print('    python apuntar.py https://loquesea.trycloudflare.com\n')
        return 1
    if not base.startswith('https://'):
        print(f'\n  La URL tiene que ser https, y es {base!r}\n')
        return 1

    numero = (args.numero or numero_de_la_demo(config)).strip()
    if not numero:
        print('\n  No se sabe que numero repuntar. Ponlo en config.json como')
        print('  carrier.numero_entrante, o pasalo con --numero.\n')
        return 1
    if numero in INTOCABLES:
        print(f'\n  {numero} es el numero de PRODUCCION de skytech. No se toca.')
        print('  Si de verdad hace falta, se cambia a mano y sabiendo por que.\n')
        return 1

    if args.url:
        ajustes.guardar({'web': {
            'url_publica': base,
            'url_stream': f"{base.replace('https://', 'wss://')}/twilio",
        }})
        config = ajustes.cargar(recargar=True)

    carrier = carrier_mod.construir(config.get('carrier'))
    if carrier is None:
        print('\n  No hay carrier configurado.\n')
        return 1

    destino = f'{base}/entrante?t={ajustes.token(config)}'

    try:
        listado = requests.get(
            carrier._url('IncomingPhoneNumbers.json'),
            auth=(carrier.account_sid, carrier.auth_token), timeout=20)
        listado.raise_for_status()
        numeros = listado.json().get('incoming_phone_numbers', [])
    except Exception as ex:
        print(f'\n  No se pudo consultar el proveedor: {ex}\n')
        return 1

    ficha = next((n for n in numeros if n.get('phone_number') == numero), None)
    if ficha is None:
        print(f'\n  {numero} no esta en esta cuenta del proveedor.')
        print(f'  Los que hay: {", ".join(n.get("phone_number", "") for n in numeros)}\n')
        return 1

    try:
        respuesta = requests.post(
            carrier._url(f"IncomingPhoneNumbers/{ficha['sid']}.json"),
            auth=(carrier.account_sid, carrier.auth_token),
            data={'VoiceUrl': destino, 'VoiceMethod': 'POST'}, timeout=20)
        respuesta.raise_for_status()
    except Exception as ex:
        print(f'\n  No se pudo repuntar el webhook: {ex}\n')
        return 1

    # Se relee del proveedor en vez de dar por hecho que el POST hizo lo que decia:
    # lo que importa no es que la peticion saliera bien, sino a donde apunta ahora.
    comprobacion = requests.get(
        carrier._url('IncomingPhoneNumbers.json'),
        auth=(carrier.account_sid, carrier.auth_token), timeout=20)
    print()
    print(f'  Tunel   : {base}')
    print(f'  Audio   : {ajustes.url_stream()}')
    print()
    for n in comprobacion.json().get('incoming_phone_numbers', []):
        etiqueta = 'PRODUCCION' if n.get('phone_number') in INTOCABLES else 'demo'
        print(f"  {n.get('phone_number')}  [{etiqueta}]")
        print(f"      {n.get('voice_url')}")

    origen = config.get('carrier', {}).get('numero_origen', '')
    if origen in INTOCABLES:
        print()
        print(f'  AVISO: carrier.numero_origen es {origen}, el numero de produccion')
        print('  de skytech. Las llamadas SALIENTES de esta demo saldrian con ese')
        print('  identificador. Para la entrante da igual; para marcar, no.')
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
