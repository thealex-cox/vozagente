"""Deja constancia de lo que se habla en la llamada.

**Divergencia deliberada respecto a la versión de skytech**, que es de donde viene
este motor: allí el puente vivía en otro proceso sin ORM y mandaba los turnos por
HTTP a Django. Aquí el puente y el registro son el mismo proceso, así que se escribe
directo en SQLite. Desaparecen el salto HTTP, su token y la clase de fallos en que el
audio va perfecto y la transcripción se pierde por un 403.

**La interfaz pública es la misma a propósito** —`anotar`, `anotar_consumo`,
`marcar_interrumpido`, `cerrar`, `activa`— para que `puente_twilio.py` no tenga que
cambiar y los dos árboles se puedan comparar fichero a fichero.

Las dos reglas que sí se conservan, porque vienen de estar en el camino del audio:

- **Nunca levanta.** Que el registro falle no puede cortar una llamada en curso.
- **Se guarda turno a turno**, no al colgar. Una llamada que se corta o un reinicio
  dejarían la conversación entera sin registrar; lo que llegó, queda.
"""

import logging
import time

from nucleo import almacen

logger = logging.getLogger(__name__)


class Bitacora:
    """Registra los turnos de una llamada concreta."""

    def __init__(self, llamada_id, sesion=''):
        self.llamada_id = llamada_id or None
        self.sesion = (sesion or '')[:64]
        self.orden = 0
        self.inicio = time.monotonic()
        # Última intervención del agente: puede haber que corregirla más tarde si el
        # cliente la interrumpe. Ver `marcar_interrumpido`.
        self.ultimo_orden_agente = None

    @property
    def activa(self):
        """Sin llamada no hay dónde guardar: la demo por navegador usa el mismo
        motor y no tiene ficha. En ese caso el agente funciona igual, sólo que la
        conversación no queda registrada."""
        return bool(self.llamada_id)

    def anotar(self, rol, texto, latencia_ms=0, interrumpido=False):
        """Guarda un turno. Devuelve su orden dentro de la llamada."""
        if not self.activa or not (texto or '').strip():
            return None
        self.orden += 1
        almacen.anotar_turno(
            self.llamada_id, rol, texto, sesion=self.sesion, orden=self.orden,
            latencia_ms=latencia_ms, interrumpido=interrumpido,
        )
        if rol == 'agente':
            self.ultimo_orden_agente = self.orden
        return self.orden

    def marcar_interrumpido(self):
        """El cliente cortó al agente a mitad de la frase.

        Se corrige el turno ya escrito en vez de escribir otro: la interrupción se
        conoce después de haber registrado lo que el agente iba diciendo, y sin esto
        la transcripción afirma que dijo entera una frase que nadie llegó a oír.
        """
        if not self.activa or self.ultimo_orden_agente is None:
            return None
        return almacen.marcar_interrumpido(
            self.llamada_id, self.sesion, self.ultimo_orden_agente)

    def anotar_consumo(self, consumos):
        """Lo que la llamada gastó con cada proveedor.

        Se manda una vez al colgar y no turno a turno: el total sólo se conoce al
        final, y guardar cifras parciales obligaría a decidir en cada una si suma o
        sustituye.
        """
        if not self.activa:
            return None
        return almacen.anotar_consumo(self.llamada_id, consumos)

    async def cerrar(self):
        """Deja la llamada cerrada en el registro.

        Antes no hacía nada: el estado y la duración llegaban por el aviso del
        proveedor a `/estado/<id>`. Las salientes lo siguen teniendo, porque la URL
        viaja en la propia petición que crea la llamada; **las entrantes no la
        tienen**, así que se quedaban en «en curso» y con duración cero para
        siempre. Se vio en las primeras llamadas reales al número, el día que se
        estrenó el camino de entrada.

        Sólo escribe si nadie ha dicho ya cómo acabó: el aviso del proveedor
        distingue «ocupado» de «sin respuesta», y esto lo pisaría con un
        «finalizada» genérico que borra el motivo.

        Se mantiene `async` para no tocar `puente_twilio.py`.
        """
        if not self.activa:
            return None
        actual = almacen.llamada(self.llamada_id)
        if actual is not None and actual['estado'] != 'en curso':
            return None
        return almacen.actualizar_llamada(
            self.llamada_id, estado='finalizada',
            duracion_segundos=int(time.monotonic() - self.inicio))
