"""Puente entre el motor de voz y las herramientas de este producto.

El motor (`agente.py`, `puente_twilio.py`) importa las herramientas de aquí, que es
donde estaban en skytech. Las de verdad viven en `nucleo/herramientas.py`, con el
resto de lo que es propio de este producto; esto sólo reexporta, para que los dos
árboles del motor se puedan seguir comparando fichero a fichero.

`definiciones_activas()` e `instrucciones()` se llaman sin argumentos desde
`agente.py`. El negocio que las acota llega por `AgenteVoz(config_negocio=...)`; sin
él se asume que están todas activas, que es lo que quiere la demo.
"""

from nucleo.herramientas import (  # noqa: F401
    DEFINICIONES,
    MAX_VUELTAS,
    NOMBRE_PEDIDO,
    EjecutorHerramientas,
    definiciones_activas,
    instrucciones,
    nombres_activos,
)
