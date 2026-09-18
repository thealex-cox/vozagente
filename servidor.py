"""Arranca el servicio: webhooks, puente de audio y demo por navegador.

    python servidor.py                    # localhost:8600
    python servidor.py --host 0.0.0.0 --puerto 8600

Un solo proceso sirve las tres cosas, y eso es deliberado: el modelo de voz tarda en
cargar y ocupa memoria, así que partirlo en dos procesos significaría cargarlo dos
veces. En skytech estaban separados por obligación —Django 2.2 no puede correr el
código async del agente—, no por diseño.

Lo que se publica al mundo (detrás del túnel o del proxy) son sólo:

    POST /twiml/<id>      instrucciones al descolgar una llamada saliente
    POST /entrante        instrucciones al recibir una llamada
    POST /estado/<id>     avisos de estado del proveedor
    WS   /twilio          el audio de la llamada

`/panel`, `/api/*`, `/`, `/salud`, `/llamadas` y `/ws` son para uso local. El panel
enseña las claves y desde él se marca, así que **rechaza toda peticion cuyo `Host` no
sea localhost** — que es lo que lo mantiene cerrado al publicar el servicio con un
túnel, porque un túnel expone el puerto entero, no una ruta.
"""

import argparse
import logging
import sys

from nucleo import ajustes

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def preparar():
    """Carga la configuración al entorno y avisa de lo que impediría funcionar.

    Se hace **antes** de importar el motor: éste lee su configuración de variables de
    entorno al importarse, y hacerlo al revés dejaría el TTS y el reconocedor
    elegidos con los valores de antes.
    """
    try:
        config = ajustes.cargar()
    except ajustes.ConfigError as ex:
        print(f'\n  {ex}\n', file=sys.stderr)
        raise SystemExit(2)

    ajustes.aplicar_entorno(config)

    # Antes de revisar nada: si la base no existe se crea. Una instalacion nueva que
    # arranca sin base conversa igual y no guarda ni un pedido, y eso no sale como un
    # error sino como una pestaña de Pedidos vacia que parece normal.
    base_lista, mensaje_base = ajustes.asegurar_base(config)
    if mensaje_base:
        print(f'\n  {mensaje_base}')
    if base_lista:
        # Se recarga porque la base puede acabar de nacer: los ajustes que hubiera en
        # ella no se leyeron en la carga anterior.
        config = ajustes.cargar(recargar=True)
        ajustes.aplicar_entorno(config)

    bloqueos, telefonia, avisos = ajustes.revisar(config)
    if not base_lista:
        bloqueos.append('No hay base de datos: el agente hablaria, pero ni las '
                        'llamadas ni los pedidos quedarian guardados.')

    nombre = ajustes.negocio(config).get('nombre') or '(sin nombre)'
    print(f'\n  Negocio: {nombre}')
    for aviso in avisos:
        print(f'  [~] {aviso}')
    for pendiente in telefonia:
        print(f'  [tel] {pendiente}')
    for bloqueo in bloqueos:
        print(f'  [!] {bloqueo}')

    if bloqueos:
        print('\n  El agente no puede ni hablar. El servicio arranca igual para que'
              '\n  puedas corregirlo desde el panel.\n')
    elif telefonia:
        # No es un fallo: es el estado normal mientras no hay tunel levantado, y la
        # demo por navegador —que es como se ensena esto— funciona perfectamente.
        print('\n  La demo por navegador funciona. Por telefono todavia no:'
              '\n  levanta el tunel y pon las dos URL en el panel.\n')
    else:
        print('  Nada bloquea una llamada.\n')
    return config


def main():
    parser = argparse.ArgumentParser(description='Servicio de voz')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--puerto', type=int, default=8600)
    parser.add_argument('--revisar', action='store_true',
                        help='Comprueba la configuracion y sale, sin arrancar')
    args = parser.parse_args()

    preparar()
    if args.revisar:
        return

    import uvicorn
    from fastapi.staticfiles import StaticFiles
    from voz_agente.servidor_demo import app
    from nucleo.web import router
    from nucleo.acceso import ControlDeAcceso
    from nucleo.panel import CARPETA as CARPETA_PANEL, router as router_panel

    app.include_router(router)
    app.include_router(router_panel)
    app.mount('/panel-estatico', StaticFiles(directory=CARPETA_PANEL),
              name='panel-estatico')

    # Envuelve la aplicacion entera, incluido el WebSocket de la demo. Desde esta
    # maquina no pide nada; desde fuera exige la clave. Los webhooks del proveedor
    # pasan siempre: traen la suya en la URL.
    app = ControlDeAcceso(app, lambda: ajustes.clave_acceso())

    print(f'  Panel:               http://localhost:{args.puerto}/panel')
    print(f'  Demo por navegador:  http://localhost:{args.puerto}/')
    print(f'  Estado del servicio: http://localhost:{args.puerto}/salud')
    if args.host not in ('127.0.0.1', 'localhost'):
        # El panel enseña las claves y desde él se marca. Lo protege la comprobación
        # de la cabecera Host —no la IP, porque un túnel se conecta desde la propia
        # máquina—, así que sigue cerrado. Pero conviene decirlo al abrir el servicio
        # a la red, que es justo cuando se da por hecho lo contrario.
        print('')
        print(f'  [~] Escuchando en {args.host}, no solo en local. El panel sigue')
        print('      cerrado a todo lo que no venga de esta maquina.')
    print('')
    uvicorn.run(app, host=args.host, port=args.puerto, log_level='info')


if __name__ == '__main__':
    main()
