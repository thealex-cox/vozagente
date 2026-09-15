"""Quién es el agente: nombre, saludo y todo lo que sabe.

Es el corazón del producto. Un negocio se describe en `config.json` y de ahí salen
las dos cosas que definen una llamada: la **primera frase**, que se dice literal y
sin pasar por el modelo para que salga al instante, y el **contexto**, que son las
instrucciones que el modelo lee y redacta con sus palabras.

La distinción importa y se confunde: el saludo se oye tal cual está escrito; el
guion no. Para forzar una frase exacta a mitad de la conversación hay que escribir
en el guion `Di exactamente: "..."`.
"""

# Reglas que se pegan a cualquier guion, lo haya escrito quien lo haya escrito. Van
# siempre las últimas: es lo último que lee el modelo y lo que gana cuando el guion
# dice lo contrario. La primera no es estilo, es lo que se enseña si alguien reclama.
REGLAS = """Reglas que no dependen del guion:
- Si te preguntan si eres un robot, un bot, una grabacion o una inteligencia artificial, responde que SI, con naturalidad y sin rodeos. Nunca lo esquives ni cambies de tema.
- Hablas EN PLURAL, a nombre del negocio: «le llamamos», «nuestro equipo». Nunca te presentes con un nombre propio de persona.
- No inventes precios, plazos, direcciones, promociones ni disponibilidad. Si no esta en lo que se te ha dado, di que lo confirma un compañero y no lo adivines.
- No pidas ni aceptes datos de tarjeta, contraseñas ni numeros de documento por telefono.
- Si pide que no le llamen mas, discupate brevemente, confirma que queda anotado y despidete. No preguntes por que ni intentes retenerlo.
- Habla claro y corto. Esto es una llamada: dos o tres frases por intervencion, no un parrafo.
- Di las horas y los numeros como los diria una persona por telefono, no como estan escritos: «de ocho de la mañana a dos de la tarde», no «de ocho a catorce horas»; «veinte porciones», no «20 porciones». Lo que escribes se convierte en voz, y una hora en formato de reloj suena a maquina.
- No hables de ninguna otra empresa ni de quien te haya construido: este negocio es el unico del que sabes algo."""

REGLAS_EN = """Rules that apply whatever the script says:
- If you are asked whether you are a robot, a bot, a recording or an AI, say YES, plainly and without hedging. Never dodge it or change the subject.
- Speak in the PLURAL, on behalf of the business: "we are calling", "our team". Never introduce yourself with a person's name.
- Do not invent prices, lead times, addresses, promotions or availability. If it is not in what you were given, say a colleague will confirm it and do not guess.
- Do not ask for or accept card details, passwords or ID numbers over the phone.
- If they ask not to be called again, apologise briefly, confirm it is noted and say goodbye. Do not ask why or try to keep them.
- Keep it short and clear. This is a phone call: two or three sentences per turn, not a paragraph.
- Say times and numbers the way a person would on the phone, not the way they are written: "eight in the morning to two in the afternoon", not "zero eight hundred to fourteen hundred". What you write is turned into speech, and clock format sounds like a machine.
- Do not talk about any other company, or about who built you: this business is the only one you know anything about."""

# Lo que suena si no hay puente de audio configurado. No nombra al negocio: a estas
# alturas puede no tener nombre utilizable, y una frase incompleta con un hueco donde
# iba el nombre es peor que una genérica.
SIN_AGENTE = {
    'es': 'Gracias por su llamada. En este momento no podemos atenderle. '
          'Por favor intente mas tarde.',
    'en': 'Thank you for your call. We cannot take it right now. '
          'Please try again later.',
}


def idioma(datos):
    valor = (datos.get('idioma') or '').strip().lower()
    return valor if valor in ('es', 'en') else 'es'


def nombre(datos):
    return (datos.get('nombre') or '').strip()


def saludo(datos, direccion):
    """La primera frase, la que el puente dice sin pasar por el modelo.

    Si el negocio no la ha escrito se compone con su nombre. Corta a propósito: por
    teléfono cada segundo de saludo es un segundo en que quien descolgó no sabe si le
    habla una persona, y ahí es donde cuelgan. El aviso de que es automático se
    mantiene aunque se recorte todo lo demás — es lo único que no puede faltar.

    Entrante y saliente no dicen lo mismo: quien nos llama ya sabe a dónde llamó, y
    abrirle con «le llamamos» suena a que le hemos marcado nosotros.
    """
    entrante = direccion == 'entrante'
    escrito = datos.get('saludo_entrante' if entrante else 'saludo_saliente') or ''
    if escrito.strip():
        return escrito.strip()

    marca = nombre(datos)
    if idioma(datos) == 'en':
        if entrante:
            return (f'Thanks for calling {marca}. You are speaking with an automated '
                    f'assistant. How can I help you?')
        return f'Hi, this is the {marca} automated assistant. Do you have a moment?'

    if entrante:
        return (f'Gracias por llamar a {marca}. Le atiende un asistente automatico. '
                f'¿En que puedo ayudarle?')
    return f'Hola, soy el asistente automatico de {marca}. ¿Tiene un momento?'


def contexto(datos, direccion, con_herramientas=None):
    """Todo lo que el agente sabe en esta llamada.

    Va como bloque de sistema y se compone en cada llamada en vez de vivir en un
    fichero que el proceso lee al arrancar. Es lo que permite retocar el guion y que
    la llamada siguiente ya lo use, sin reiniciar el servicio.

    `con_herramientas` cambia el final del bloque, y es lo que evita que el agente
    mienta en una direccion o en la otra: sin herramienta no puede guardar nada y hay
    que prohibirle decir que lo hizo; con ella si puede, y prohibirselo le haria
    negarse a apuntar un pedido que si sabe apuntar. Vacio, se deduce de la
    configuracion del negocio.
    """
    from nucleo import herramientas

    ingles = idioma(datos) == 'en'
    entrante = direccion == 'entrante'
    marca = nombre(datos)
    if con_herramientas is None:
        con_herramientas = bool(herramientas.nombres_activos(datos))
    partes = []

    encabezado = (f'You are the automated phone assistant for {marca}.' if ingles else
                  f'Eres el asistente telefonico automatico de {marca}.')
    if entrante:
        encabezado += (' This is an INCOMING call: the customer called us. Ask how you '
                       'can help; do not assume why they are calling.'
                       if ingles else
                       ' Esta es una llamada ENTRANTE: el cliente nos ha llamado a '
                       'nosotros. Pregunta en que puedes ayudar; no des por supuesto '
                       'para que llama.')
    else:
        encabezado += (' This is an OUTGOING call: we called them, so they were not '
                       'expecting it. Be brief and check it is a good moment before '
                       'going on.'
                       if ingles else
                       ' Esta es una llamada SALIENTE: le hemos marcado nosotros, asi '
                       'que no la esperaba. Se breve y comprueba que es buen momento '
                       'antes de seguir.')
    partes.append(encabezado)

    descripcion = (datos.get('descripcion') or '').strip()
    if descripcion:
        partes.append(f'{"About the business" if ingles else "Sobre el negocio"}:\n{descripcion}')

    guion = (datos.get('guion_entrante' if entrante else 'guion_saliente') or '').strip()
    if guion:
        partes.append(f'{"Script" if ingles else "Guion"}:\n{guion}')

    # Esta es la regla que mas cuesta que se cumpla y la que mas dano hace cuando no
    # se cumple. En una conversacion real el agente pidio un telefono y contesto
    # «Perfecto, anotado»: no anoto nada, no tiene donde, y prometio una llamada de
    # vuelta que nadie iba a hacer.
    #
    # Decir solo «no puedes registrar nada» NO basta, y por un motivo concreto: el
    # guion le pide tomar un pedido, asi que la instruccion contradice a la tarea y
    # el modelo resuelve la contradiccion fingiendo. Hay que decirle ademas que hacer
    # en su lugar, y prohibir la palabra exacta con la que miente.
    # Sin herramienta no puede guardar nada, y decirselo no basta: el guion le pide
    # tomar un pedido, asi que la instruccion contradice a la tarea y el modelo
    # resuelve la contradiccion fingiendo. Paso de verdad — pidio un telefono y
    # contesto «Perfecto, anotado». Hay que decirle ademas que hacer en su lugar y
    # prohibir la palabra exacta con la que miente.
    SIN_HERRAMIENTAS_ES = (
        'No tienes base de datos, ni registros, ni agenda, ni herramientas. No puedes '
        'consultar nada, ni agendar, ni guardar nada: ni un nombre ni un telefono, ni '
        'siquiera un momento.\n'
        'Por eso, cuando la conversacion pida tomar datos:\n'
        '- Preguntalos y repiteselos, tal como diga el guion. Eso esta bien.\n'
        '- Pero NUNCA digas que quedan anotados, guardados, agendados, registrados ni '
        'confirmados. Ni "anotado", ni "queda apuntado", ni "listo".\n'
        '- Di en su lugar que un companero lo confirma, y que hasta entonces no hay '
        'nada reservado.\n'
        '- NO pidas telefono, correo ni direccion salvo que el guion los pida por su '
        'nombre: no puedes quedartelos, y pedirlos da a entender que si.'
    )

    SIN_HERRAMIENTAS_EN = (
        'You have no database, no records, no calendar and no tools. You cannot look '
        'anything up, book anything, or save anything - not even a name or a phone '
        'number, and not even for a moment.\n'
        'So, when the conversation calls for taking down details:\n'
        '- Ask for them and repeat them back, exactly as the script says. That is fine.\n'
        '- But NEVER say they are saved, noted, booked, registered or confirmed. Not '
        '"noted", not "got it down", not "all set".\n'
        '- Say instead that a colleague will confirm it, and that nothing is booked '
        'until they do.\n'
        '- Do NOT ask for a phone number, an email or an address unless the script '
        'asks for them by name: you cannot keep them, and asking implies you can.'
    )

    if con_herramientas:
        # Lo que puede y lo que no. Sigue sin poder consultar: la herramienta solo
        # escribe, asi que un agente que crea que puede mirar el historial del
        # cliente se lo inventaria igual que antes.
        partes.append(
            'You cannot look anything up: you have no history, no catalogue and no '
            'calendar to check. What you CAN do is write down what the customer asks '
            'for, with the tool described below, so a person can deal with it later.'
            if ingles else
            'No puedes consultar nada: no tienes historial, ni catalogo, ni agenda que '
            'mirar. Lo que SI puedes es apuntar lo que el cliente pide, con la '
            'herramienta que se describe mas abajo, para que una persona lo atienda '
            'despues.')
        partes.append(herramientas.instrucciones(datos, ingles))
    else:
        partes.append(SIN_HERRAMIENTAS_EN if ingles else SIN_HERRAMIENTAS_ES)

    partes.append(REGLAS_EN if ingles else REGLAS)
    return '\n\n'.join(partes)


def vocabulario(datos):
    """Términos que el reconocedor va a oír, separados por comas.

    No es para el modelo sino para Deepgram y Whisper. El que importa es el nombre
    del negocio: es la palabra que más se dice y la que un reconocedor destroza sin
    ayuda — «Gracias por llamar a X» se transcribía «Gracias por llamar a.».
    """
    terminos = [nombre(datos)]
    extra = datos.get('vocabulario') or []
    if isinstance(extra, str):
        extra = [t.strip() for t in extra.split(',')]
    terminos.extend(str(t).strip() for t in extra if str(t).strip())

    vistos, unicos = set(), []
    for termino in terminos:
        clave = termino.lower()
        if termino and clave not in vistos:
            vistos.add(clave)
            unicos.append(termino)
    return ', '.join(unicos)


def mensaje_sin_agente(datos):
    return SIN_AGENTE.get(idioma(datos), SIN_AGENTE['es'])
