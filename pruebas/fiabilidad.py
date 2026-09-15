"""Cuantas veces de diez guarda el pedido cuando deberia.

    python pruebas/fiabilidad.py 10

Existe porque el momento culminante de una demo no puede ser una moneda al
aire, y eso no se intuye: se cuenta. Con el guion que esperaba confirmacion
antes de apuntar salia 5 de 10; apuntando primero, 10 de 10.

El escenario del encargo fallaba una vez si y otra no. Un fallo intermitente en el
momento culminante de la demo hay que medirlo, no intuirlo: si guarda ocho de diez,
hay que arreglarlo antes del dia 9.
"""

import os
import sys

sys.path.insert(0, r'D:\skytech\vozagente')

# Cada corrida con su propia base. Antes escribian en la de verdad, y una tanda dejaba
# veinte pedidos identicos en la pestana de Pedidos del panel — que es justo la que se
# proyecta al ensenar que el encargo quedo guardado.
import tempfile  # noqa: E402
os.environ['VOZ_BD'] = os.path.join(tempfile.mkdtemp(prefix='voz_pruebas_'), 'pruebas.db')

from nucleo import ajustes, almacen, negocio as negocio_cfg  # noqa: E402
from nucleo.herramientas import EjecutorHerramientas  # noqa: E402

CONFIG = ajustes.cargar(recargar=True)
ajustes.aplicar_entorno(CONFIG)

from voz_agente.agente import MODELO_CLAUDE, AgenteVoz  # noqa: E402

DATOS = ajustes.negocio(CONFIG)

CONVERSACION = [
    'Quiero encargar una torta.',
    'De tres leches, para diez personas.',
    'Para el viernes, a nombre de Maria.',
]

VECES = int(sys.argv[1]) if len(sys.argv) > 1 else 10


def una_vez():
    llamada = almacen.crear_llamada('demo', 'prueba', estado='en curso',
                                    carrier='prueba', es_prueba=True)
    ejecutor = EjecutorHerramientas(llamada, telefono='+593999000111')
    agente = AgenteVoz(
        stt=None, tts=None, prompt_negocio='',
        modelo=os.environ.get('VOZ_MODELO_CLAUDE') or MODELO_CLAUDE,
        contexto=negocio_cfg.contexto(DATOS, 'entrante'),
        herramientas=ejecutor,
    )
    ultima = ''
    for frase in CONVERSACION:
        ultima = agente.pensar(frase)
    return bool(ejecutor.pedidos), ultima


def main():
    guardo = 0
    mintio = 0
    for intento in range(1, VECES + 1):
        ok, ultima = una_vez()
        bajo = ultima.lower()
        # Lo peor no es que no guarde: es que no guarde Y diga que si.
        afirma = any(p in bajo for p in ('anotad', 'apuntad', 'registrad', 'guardad'))
        if ok:
            guardo += 1
        elif afirma:
            mintio += 1
        marca = 'guardo' if ok else ('MINTIO' if afirma else 'no guardo')
        print(f'  {intento:>2}. {marca:<10} {ultima[:88]}')

    print()
    print(f'  Guardo        : {guardo}/{VECES}')
    print(f'  No guardo     : {VECES - guardo}/{VECES}')
    print(f'  Y encima mintio: {mintio}/{VECES}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
