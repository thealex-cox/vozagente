"""Lo único que el agente puede hacer además de hablar: apuntar lo que le piden.

Una sola herramienta, y genérica a propósito. El mismo servicio atiende a una
panadería, a una clínica y a un taller; una herramienta con «sabor» y «porciones»
obligaría a tocar el código para cada negocio nuevo, y una batería de herramientas
por sector obligaría a mantenerlas todas. `resumen` en texto libre lo cubre todo, y
el guion del negocio es quien dice qué preguntar antes de invocarla.

**Se ejecuta en este mismo proceso.** En skytech el agente vivía aparte y las
herramientas iban por HTTP contra Django, con su token y sus timeouts. Aquí es una
llamada a función: sin salto de red, sin autenticación entre partes y sin la clase de
fallos en que la llamada va bien y la herramienta se pierde en un 403.

**El id de la llamada NO lo elige el modelo**, lo pone quien construye el ejecutor.
Es la misma regla que en skytech y por el mismo motivo: al otro lado del teléfono hay
un desconocido dictándole cosas al agente, y con un id por parámetro cualquiera podría
pedir que se apunte algo en la ficha de otro.
"""

import json
import logging

from nucleo import almacen

logger = logging.getLogger(__name__)

# Tope de vueltas de herramienta por turno. Cada vuelta es un silencio para quien
# está al teléfono; dos ya se notan.
MAX_VUELTAS = 2

NOMBRE_PEDIDO = 'anotar_pedido'
NOMBRE_NO_LLAMAR = 'registrar_no_llamar'
NOMBRE_TERMINAR = 'terminar_llamada'
TODAS = (NOMBRE_PEDIDO, NOMBRE_NO_LLAMAR, NOMBRE_TERMINAR)

DEFINICIONES = [
    {
        'name': NOMBRE_PEDIDO,
        'description': (
            'Guarda lo que el cliente pide para que una persona lo atienda despues: '
            'un encargo, una reserva, una cita o una peticion concreta. Usala solo '
            'cuando haya algo que atender, no para saludos ni para preguntas de '
            'horario. Llamala UNA vez, cuando ya tengas los datos que pide el guion.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'resumen': {
                    'type': 'string',
                    'description': (
                        'Que pide, en una frase y con las palabras del cliente. '
                        'Ejemplo: "Torta de tres leches para veinte personas".'
                    ),
                },
                'nombre': {
                    'type': 'string',
                    'description': 'A nombre de quien, si lo ha dicho. Si no, vacio.',
                },
                'cuando': {
                    'type': 'string',
                    'description': (
                        'Cuando lo quiere, tal como lo dijo el cliente: "el sabado", '
                        '"manana por la tarde". No lo conviertas a fecha.'
                    ),
                },
                'detalles': {
                    'type': 'string',
                    'description': 'Cualquier otra cosa que haya pedido. Vacio si no hay.',
                },
                'corrige': {
                    'type': 'integer',
                    'description': (
                        'Si el cliente cambia algo de un pedido que ya apuntaste en '
                        'esta llamada, pon aqui su numero y vuelve a mandar el pedido '
                        'entero ya corregido. El anterior deja de contar. Sin esto '
                        'quedarian los dos y en el negocio verian dos encargos.'
                    ),
                },
            },
            'required': ['resumen'],
        },
    },
    {
        'name': 'registrar_no_llamar',
        'description': (
            'Registra que esta persona no quiere recibir mas llamadas. Usala en '
            'cuanto lo pida, sin preguntar por que. No sirve para otra cosa.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'motivo': {
                    'type': 'string',
                    'description': 'Lo que dijo, si dio algun motivo. Vacio si no.',
                },
            },
            'required': [],
        },
    },
    {
        'name': NOMBRE_TERMINAR,
        'description': (
            'Cuelga la llamada. Usala SOLO cuando ya os habeis despedido y no queda '
            'nada pendiente: el cliente se ha despedido, o ya has resuelto lo suyo y '
            'le has dicho adios. Di la despedida en este mismo turno, ANTES de '
            'llamarla, porque despues de usarla ya no puedes decir nada mas. Si hay '
            'la menor duda de que el cliente vaya a seguir hablando, no la uses: '
            'colgarle a alguien que iba a decir algo es peor que una pausa de mas.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'motivo': {
                    'type': 'string',
                    'description': (
                        'Por que se da por terminada, en pocas palabras: '
                        '"el cliente se despidio", "pedido anotado y confirmado".'
                    ),
                },
            },
            'required': [],
        },
    },
]

# Instrucciones que acompañan a la definición. Van en el prompt porque el esquema
# dice QUÉ recibe la herramienta, no CUÁNDO usarla ni qué se puede decir después.
INSTRUCCIONES_ES = """Sobre `anotar_pedido`, la unica herramienta que tienes:
- Usala cuando el cliente pida algo que alguien tenga que atender despues. No la uses para saludar, ni para responder el horario, ni para conversacion suelta.
- ANTES de usarla di una frase corta, tipo «Un momento, lo anoto». La herramienta tarda un par de segundos y por telefono un silencio se lee como llamada cortada.
- Solo DESPUES de que la herramienta responda que quedo guardado puedes decir que esta anotado. Si responde que fallo, dilo con naturalidad: que un compañero lo confirmara. Nunca digas que quedo guardado sin haberla usado.
- Llamala una sola vez por pedido, cuando ya tengas los datos.
- Si el cliente cambia algo de un pedido que ya apuntaste, vuelve a llamarla con el pedido entero corregido y pon en `corrige` el numero que te devolvio la primera vez. Sin eso quedarian dos encargos y en el negocio harian los dos.

Sobre `registrar_no_llamar`:
- Usala EN CUANTO alguien diga que no quiere mas llamadas, y solo para eso.
- No preguntes por que ni intentes retenerlo. Discupate, confirma que queda registrado y despidete.
- Igual que la otra: solo puedes decir que queda registrado despues de que responda que si.

Sobre `terminar_llamada`:
- Es la que cuelga. Usala cuando la conversacion ya ha acabado de verdad: os habeis despedido, o ya resolviste lo suyo y le dijiste adios.
- Di la despedida en el MISMO turno y ANTES de llamarla. Despues de usarla no vas a poder decir nada mas: la linea se cierra en cuanto termina de sonar lo que acabas de decir.
- No la uses para hacer una pausa ni si el cliente podria seguir hablando. Ante la duda, no cuelgues: preguntale si necesita algo mas y espera.
- No anuncies que vas a colgar ni digas «voy a cerrar la llamada». Despidete como lo haria una persona y ya esta."""

INSTRUCCIONES_EN = """About `anotar_pedido`, the only tool you have:
- Use it when the customer asks for something a person has to deal with later. Do not use it for greetings, opening hours, or small talk.
- BEFORE using it, say a short line like "One moment, let me write that down". The tool takes a couple of seconds and on the phone silence reads as a dropped call.
- Only AFTER the tool says it was saved may you say it is noted. If it says it failed, say so plainly: a colleague will confirm it. Never claim it was saved without using the tool.
- Call it once per order, when you already have the details.
- If the customer changes an order you already wrote down, call it again with the whole corrected order and put the number it gave you the first time in `corrige`. Without that, two orders would stand and the business would make both.

About `registrar_no_llamar`:
- Use it AS SOON AS someone says they do not want any more calls, and only for that.
- Do not ask why or try to keep them. Apologise, confirm it is registered, and say goodbye.
- Same rule as the other one: you may only say it is registered after it answers that it is.

About `terminar_llamada`:
- This is the one that hangs up. Use it when the conversation is genuinely over: you have both said goodbye, or you settled their request and said goodbye.
- Say the goodbye in the SAME turn and BEFORE calling it. After using it you will not be able to say anything else: the line closes as soon as what you just said finishes playing.
- Do not use it to pause, or if the customer might still be talking. When in doubt, do not hang up: ask whether they need anything else and wait.
- Do not announce that you are going to hang up. Say goodbye the way a person would and leave it there."""


def nombres_activos(config_negocio=None):
    """Qué herramientas puede usar este negocio.

    `negocio.herramientas` en `config.json` lo acota; ausente, están todas. Existe
    para poder apagarla en un negocio que sólo informa: una herramienta descrita al
    modelo es una herramienta que va a ofrecer al cliente, y ofrecer apuntar un
    pedido en un negocio que no toma pedidos queda mal en cuanto el cliente acepta.
    """
    if config_negocio is None:
        return list(TODAS)
    pedidas = config_negocio.get('herramientas')
    if pedidas is None:
        return list(TODAS)
    if isinstance(pedidas, str):
        pedidas = [t.strip() for t in pedidas.split(',')]
    return [n for n in (pedidas or []) if n in TODAS]


def definiciones_activas(config_negocio=None):
    activos = nombres_activos(config_negocio)
    return [d for d in DEFINICIONES if d['name'] in activos]


def instrucciones(config_negocio=None, ingles=False):
    if not nombres_activos(config_negocio):
        return ''
    return INSTRUCCIONES_EN if ingles else INSTRUCCIONES_ES


class EjecutorHerramientas:
    """Ejecuta lo que el agente pide, contra la base de esta instalación."""

    # Las que dan la conversación por acabada. Sólo `terminar_llamada`: apuntar un
    # pedido no es despedirse, y colgar en cuanto se guarda dejaría al cliente con la
    # palabra en la boca — que es justo el fallo que esta lista existe para no tener.
    TERMINALES = (NOMBRE_TERMINAR,)

    def __init__(self, llamada_id=None, telefono='', activas=None):
        self.llamada_id = llamada_id or None
        self.telefono = telefono or ''
        self._activas = list(activas) if activas is not None else list(TODAS)
        # Lo consulta el puente al terminar de reproducir un turno, para decidir si
        # cuelga. Lo levanta `terminar_llamada`, y nada mas.
        self.conversacion_terminada = False
        # Para el panel y para las pruebas: qué apuntó esta llamada.
        self.pedidos = []

    @property
    def activo(self):
        """Sin llamada no hay contra qué guardar. El agente sigue hablando igual,
        sólo que sin poder apuntar nada — y el prompt se compone en consecuencia."""
        return bool(self.llamada_id and self._activas)

    def ejecutar(self, nombre, argumentos):
        """Devuelve el resultado ya serializado, listo para el bloque tool_result.

        **Nunca levanta.** Un fallo se convierte en un resultado que el modelo pueda
        explicar en voz alta; si esto propagara la excepción, el turno moriría con el
        cliente esperando al teléfono.
        """
        if not self.activo:
            return json.dumps({'guardado': False,
                               'error': 'No hay donde guardar en esta llamada.'},
                              ensure_ascii=False)

        if nombre not in self._activas:
            logger.warning(f'Herramienta no disponible: {nombre}')
            return json.dumps({'guardado': False,
                               'error': f'No tienes la herramienta {nombre}.'},
                              ensure_ascii=False)

        argumentos = argumentos or {}
        if nombre == NOMBRE_TERMINAR:
            return self._terminar_llamada(argumentos)
        if nombre == NOMBRE_NO_LLAMAR:
            return self._no_llamar(argumentos)
        return self._anotar_pedido(argumentos)

    def _terminar_llamada(self, argumentos):
        """Da la conversación por acabada. El puente cuelga cuando deje de sonar.

        No cuelga aquí: cortar en este instante se llevaría por delante la despedida
        que el agente acaba de decir y que todavía está sonando —el audio va por
        delante de lo que se oye—. Lo único que hace es levantar la bandera que
        `colgar_si_termino()` consulta cuando el turno termina de reproducirse.

        Queda como evento porque «por qué se cortó esta llamada» es la primera
        pregunta cuando alguien reclama, y sin esto la respuesta sería un silencio.
        """
        self.conversacion_terminada = True
        motivo = str(argumentos.get('motivo', '')).strip()
        try:
            almacen.anotar_evento(self.llamada_id, 'terminada_por_agente',
                                  motivo or 'El agente dio la conversacion por acabada')
        except Exception as ex:
            # Que no quede el evento no puede impedir que se cuelgue: la bandera ya
            # está levantada y es lo que de verdad cierra la llamada.
            logger.warning(f'No se pudo anotar el fin de la llamada: {ex}')

        logger.info(f'[llamada {self.llamada_id}] el agente da la llamada por '
                    f'terminada: {motivo or "sin motivo"}')
        return json.dumps(
            {'guardado': True,
             'mensaje': 'La llamada se cierra en cuanto termine de sonar lo que acabas '
                        'de decir. No digas nada mas.'},
            ensure_ascii=False)

    def _anotar_pedido(self, argumentos):
        resumen = str(argumentos.get('resumen', '')).strip()
        if not resumen:
            return json.dumps({'guardado': False,
                               'error': 'Falta el resumen de lo que pide.'},
                              ensure_ascii=False)

        try:
            pedido_id = almacen.crear_pedido(
                self.llamada_id,
                resumen=resumen,
                nombre=str(argumentos.get('nombre', '')).strip(),
                cuando_texto=str(argumentos.get('cuando', '')).strip(),
                detalles=str(argumentos.get('detalles', '')).strip(),
                telefono=self.telefono,
            )
        except Exception as ex:
            logger.exception(f'No se pudo guardar el pedido: {ex}')
            pedido_id = None

        if not pedido_id:
            return json.dumps(
                {'guardado': False,
                 'error': 'No se pudo guardar. Dile al cliente que un companero lo '
                          'confirmara, y no le asegures que quedo anotado.'},
                ensure_ascii=False)

        self.pedidos.append(pedido_id)
        respuesta = {'guardado': True, 'numero': pedido_id,
                     'mensaje': 'Queda anotado. Ya puedes confirmarselo al cliente.'}

        # Correccion de un pedido anterior. Solo de ESTA llamada: sin esa condicion,
        # un numero dictado por telefono podria retirar el encargo de otro cliente.
        corrige = argumentos.get('corrige')
        try:
            corrige = int(corrige) if corrige not in (None, '') else None
        except (TypeError, ValueError):
            corrige = None
        if corrige and corrige in self.pedidos[:-1]:
            almacen.reemplazar_pedido(corrige, pedido_id)
            respuesta['reemplaza'] = corrige
            respuesta['mensaje'] = ('Queda anotado el cambio y el anterior ya no '
                                    'cuenta. Ya puedes confirmarselo al cliente.')
        elif corrige:
            logger.warning(
                f'[llamada {self.llamada_id}] se pidio corregir el pedido {corrige}, '
                f'que no es de esta llamada; se ignora')

        logger.info(f'[llamada {self.llamada_id}] pedido {pedido_id}: {resumen[:80]}')
        return json.dumps(respuesta, ensure_ascii=False)

    def _no_llamar(self, argumentos):
        """Apunta que este numero no quiere mas llamadas.

        Es la peticion que menos margen admite: la ley obliga a cumplirla, y decir
        «queda anotado» sin anotarlo es exactamente lo que la incumple. Por eso
        devuelve un error claro cuando no hay numero — la demo por navegador, por
        ejemplo— en vez de fingir que se apunto.
        """
        if not self.telefono:
            return json.dumps(
                {'guardado': False,
                 'error': 'No se conoce el numero de quien llama, asi que no se puede '
                          'registrar. Dile que lo anotara un companero y discupate.'},
                ensure_ascii=False)

        try:
            hecho = almacen.bloquear(self.telefono,
                                     str(argumentos.get('motivo', '')).strip())
        except Exception as ex:
            logger.exception(f'No se pudo registrar el no-llamar: {ex}')
            hecho = None

        if not hecho:
            return json.dumps(
                {'guardado': False,
                 'error': 'No se pudo registrar. Dile que lo anotara un companero.'},
                ensure_ascii=False)

        logger.info(f'[llamada {self.llamada_id}] {self.telefono} pide no ser llamado')
        return json.dumps(
            {'guardado': True,
             'mensaje': 'Registrado. Ya puedes confirmarle que no volveran a llamarle.'},
            ensure_ascii=False)

    def cerrar(self):
        return None
