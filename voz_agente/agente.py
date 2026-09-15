"""Orquestador de la conversación por voz: audio entra, audio sale.

    audio del cliente ──> STT ──> Claude ──> TTS ──> audio de respuesta

Claude se consume en streaming y se sintetiza frase a frase, de modo que el
primer audio sale mientras el modelo todavía está redactando el resto. Sin eso,
el silencio antes de responder se vuelve incómodo en una llamada real.
"""

import logging
import os
import time

from voz_agente.herramientas import MAX_VUELTAS, definiciones_activas, instrucciones
from voz_agente.proveedores.base import dividir_en_frases, limpiar_para_voz

logger = logging.getLogger(__name__)

MODELO_CLAUDE = 'claude-haiku-4-5-20251001'

# Un prompt de chat no sirve para voz: produce respuestas largas, con markdown y
# enlaces, imposibles de escuchar. Estas reglas van DESPUÉS del prompt de negocio
# porque lo último que lee el modelo es lo que más pesa: puestas antes, el prompt
# del chatbot las sobrescribía y el agente contestaba con asteriscos y links.
ESTILO_VOZ = """REGLAS DE FORMATO — tienen prioridad sobre cualquier instrucción anterior.

Estás hablando por TELÉFONO. El cliente te escucha, no te lee.

- Máximo dos frases por respuesta. Si necesitas más, pregunta primero.
- PROHIBIDO markdown: nada de asteriscos, negritas, viñetas ni encabezados.
- PROHIBIDOS los enlaces y las URL: nadie puede hacer clic en una llamada. Si hay
  que derivar, ofrece que un asesor le contacte o pide un correo.
- Nada de emojis ni caracteres especiales: todo se lee en voz alta tal cual.
- Números y montos en palabras cuando suene natural.
- Si no entiendes, pide que repita en lugar de suponer.
- Habla en el idioma en que te hablen.
"""


class AgenteVoz:
    def __init__(self, stt, tts, api_key=None, prompt_negocio='', modelo=MODELO_CLAUDE,
                 contexto='', herramientas=None):
        self.stt = stt
        self.tts = tts
        self.modelo = modelo
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY', '')
        # Datos de esta llamada concreta (qué cotización se está confirmando, con
        # quién). Se guarda aparte del prompt de negocio a propósito: ver `pensar`.
        self.contexto = contexto
        # Sin ejecutor el agente sigue funcionando, sólo que informativo: es lo que
        # pasa en la demo por navegador, que no tiene ficha de llamada detrás.
        self.herramientas = herramientas
        # El estilo de voz va al final a propósito: es lo último que lee el modelo
        # y así gana sobre las instrucciones de formato del prompt de negocio.
        partes = [prompt_negocio] if prompt_negocio else []
        partes.append(ESTILO_VOZ)
        self.tools = definiciones_activas()
        if herramientas is not None and herramientas.activo and self.tools:
            partes.append(instrucciones())
        self.system_prompt = '\n\n'.join(partes)
        self.historial = []
        self._cliente = None
        # Tokens gastados en esta llamada. Se acumulan aquí porque este es el único
        # punto por el que pasan todas las respuestas —incluidas las vueltas de
        # herramienta, que también se facturan— y porque Django no tiene forma de
        # saberlo: la conversación con el modelo ocurre en este proceso.
        self.uso = {'entrada': 0, 'salida': 0, 'cache_lectura': 0, 'cache_escritura': 0}

    def _claude(self):
        if self._cliente is None:
            import anthropic
            self._cliente = anthropic.Anthropic(api_key=self.api_key)
        return self._cliente

    def precalentar(self):
        """Deja lista la conexión con el modelo antes de que haga falta.

        **Medido en una llamada real: la primera respuesta tardó 13,3 s y la segunda
        1,0 s.** La diferencia no es el modelo, es todo lo que pasa una sola vez y
        pasaba con el cliente ya esperando al teléfono: importar `anthropic`, montar
        el cliente, resolver el DNS y negociar el TLS. Quince segundos de silencio en
        una llamada son una llamada perdida, y encima parece que el agente no ha
        entendido, no que todavía no ha empezado.

        Se llama mientras suena el saludo —seis segundos en los que nadie espera
        nada—, así que en tiempo no cuesta nada y en dinero, unos pocos tokens.

        **Nunca levanta.** Que esto falle no puede tumbar una llamada que sin ello
        funcionaría igual, sólo que más lenta.
        """
        try:
            comienzo = time.monotonic()
            self._claude().messages.create(
                model=self.modelo,
                max_tokens=1,
                messages=[{'role': 'user', 'content': '.'}],
            )
            logger.info(f'Conexion con el modelo lista en {time.monotonic() - comienzo:.2f}s')
            return True
        except Exception as ex:
            logger.warning(f'No se pudo precalentar la conexion con el modelo: {ex}')
            return False

    def escuchar(self, ruta_audio):
        return self.stt.transcribir(ruta_audio)

    def _bloque_system(self):
        """El bloque de sistema, con el punto de caché al final de todo.

        **Aquí la caché va al revés que en el motor de skytech, y a propósito.** Allí
        el prompt de negocio eran 148 KB idénticos en todas las llamadas y el
        `contexto` eran los datos de la cotización concreta, distintos cada vez: el
        punto de caché iba tras el prompt y el contexto quedaba fuera, porque meter
        algo que cambia en cada llamada dentro del prefijo cacheado lo invalidaría
        entero cada vez.

        En este producto es al contrario. El prompt de negocio va vacío y el
        `contexto` —quién es el negocio, qué vende, su guion— es **lo único** que hay
        y es el mismo en todas las llamadas hasta que alguien edite `config.json`.
        Dejarlo fuera del prefijo cacheado sería reenviarlo entero en cada turno de
        cada llamada, pagándolo a precio completo.

        **Ojo: con Haiku 4.5 esto normalmente no llega a cachear nada.** Su prefijo
        mínimo cacheable son 4.096 tokens y la descripción de un negocio pequeño
        ronda los 900; por debajo del mínimo no cachea y no avisa —`cache_control` se
        acepta y `cache_read_input_tokens` sale 0—. No es un fallo: con una base de
        conocimiento grande (un FAQ de verdad) se cruza el umbral y empieza a
        cachear sin tocar nada. Para comprobarlo, mirar `usage.cache_read_input_tokens`
        en vez de suponerlo.
        """
        partes = []
        if self.system_prompt:
            partes.append(self.system_prompt)
        if self.contexto:
            partes.append(self.contexto)
        if not partes:
            return []

        return [{
            'type': 'text',
            'text': '\n\n'.join(partes),
            'cache_control': {'type': 'ephemeral'},
        }]

    def pensar(self, texto_usuario, al_completar_frase=None):
        """Consulta a Claude en streaming, resolviendo las herramientas que pida.

        al_completar_frase se invoca con cada frase terminada, para ir sintetizando
        sin esperar el final. Devuelve la respuesta completa.

        Cada vuelta de herramienta es un silencio para quien está al teléfono, así
        que hay un tope. La última se pide con `tool_choice: none` en lugar de
        retirar las definiciones: van delante del system en el prefijo de caché, y
        quitarlas invalidaría el prompt entero justo en la petición que ya llega
        tarde. Además así el modelo no puede terminar el turno pidiendo una
        herramienta que nadie va a responder — un `tool_use` sin su `tool_result`
        deja el historial inválido y la llamada muere en el turno siguiente.
        """
        self.historial.append({'role': 'user', 'content': texto_usuario})
        self.ultimo_ttft = None
        inicio = time.time()
        system = self._bloque_system()
        con_tools = self.herramientas is not None and self.herramientas.activo and self.tools
        dicho = []

        for vuelta in range(MAX_VUELTAS + 1):
            extra = {}
            if con_tools:
                extra['tools'] = self.tools
                if vuelta == MAX_VUELTAS:
                    extra['tool_choice'] = {'type': 'none'}

            texto, mensaje = self._responder(system, extra, al_completar_frase, inicio)
            if texto.strip():
                dicho.append(texto.strip())
            if mensaje.content:
                self.historial.append({'role': 'assistant', 'content': mensaje.content})

            usos = [b for b in mensaje.content if getattr(b, 'type', '') == 'tool_use']
            if not usos:
                break
            self.historial.append({'role': 'user', 'content': self._ejecutar(usos)})

        return ' '.join(dicho)

    def _responder(self, system, extra, al_completar_frase, inicio):
        """Una vuelta: streamea el texto por frases y devuelve el mensaje completo."""
        respuesta, pendiente = '', ''

        with self._claude().messages.stream(
            model=self.modelo,
            max_tokens=300,
            system=system,
            messages=self.historial,
            **extra,
        ) as stream:
            for fragmento in stream.text_stream:
                if self.ultimo_ttft is None:
                    self.ultimo_ttft = time.time() - inicio
                respuesta += fragmento
                pendiente += fragmento
                if al_completar_frase:
                    frases = dividir_en_frases(pendiente)
                    # La última puede estar incompleta: se deja para la vuelta siguiente.
                    if len(frases) > 1:
                        for frase in frases[:-1]:
                            al_completar_frase(limpiar_para_voz(frase))
                        pendiente = frases[-1]
            mensaje = stream.get_final_message()

        self._anotar_uso(getattr(mensaje, 'usage', None))

        # La frase puente («déjeme revisarlo») sale por aquí, y tiene que salir ANTES
        # de que se ejecute la herramienta: es lo único que tapa la espera.
        if al_completar_frase and pendiente.strip():
            al_completar_frase(limpiar_para_voz(pendiente))

        return respuesta, mensaje

    def _anotar_uso(self, uso):
        """Suma los tokens de una vuelta al total de la llamada.

        No levanta nunca y no comprueba nada más allá de lo imprescindible: es
        contabilidad, y un contador roto no puede cortar una conversación en curso.
        """
        if uso is None:
            return
        for clave, atributo in (
            ('entrada', 'input_tokens'),
            ('salida', 'output_tokens'),
            ('cache_lectura', 'cache_read_input_tokens'),
            ('cache_escritura', 'cache_creation_input_tokens'),
        ):
            try:
                self.uso[clave] += int(getattr(uso, atributo, 0) or 0)
            except (TypeError, ValueError):
                continue

    def _ejecutar(self, usos):
        bloques = []
        for uso in usos:
            logger.info(f'herramienta {uso.name}')
            salida = self.herramientas.ejecutar(uso.name, dict(uso.input or {}))
            bloques.append({
                'type': 'tool_result',
                'tool_use_id': uso.id,
                'content': salida,
            })
        return bloques

    def hablar(self, texto, ruta_salida):
        return self.tts.sintetizar(texto, ruta_salida)

    def turno(self, ruta_audio_entrada, carpeta_salida):
        """Un turno completo de conversación, con métricas de latencia.

        El dato que importa es 'primer_audio': cuánto tarda el cliente en oír algo
        desde que deja de hablar. Por encima de ~1.5 s la llamada se siente rota.
        """
        metricas, t0 = {}, time.time()

        texto_usuario = self.escuchar(ruta_audio_entrada)
        metricas['stt'] = time.time() - t0

        audios, marca_primer_audio = [], []

        def sintetizar_frase(frase):
            indice = len(audios)
            destino = os.path.join(
                carpeta_salida, f'respuesta_{indice:02d}.{self.tts.extension}'
            )
            self.hablar(frase, destino)
            if not marca_primer_audio:
                marca_primer_audio.append(time.time() - t0)
            audios.append(destino)

        inicio_llm = time.time()
        respuesta = self.pensar(texto_usuario, al_completar_frase=sintetizar_frase)
        metricas['llm_y_tts'] = time.time() - inicio_llm
        metricas['primer_audio'] = marca_primer_audio[0] if marca_primer_audio else None
        metricas['total'] = time.time() - t0

        return {
            'texto_usuario': texto_usuario,
            'respuesta': respuesta,
            'audios': audios,
            'metricas': metricas,
        }
