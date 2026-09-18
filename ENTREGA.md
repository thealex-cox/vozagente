# Entrega técnica

Agente telefónico que atiende llamadas por un negocio: contesta, entiende lo que le
dicen, responde con voz y apunta lo que le piden. Un servicio, un negocio.

Este documento es para quien recibe el código. Para poner en marcha una demo, el
`README.md`; para el guion de una demostración, `DEMO.md`.

## Qué hace, en una frase

Entra una llamada → el operador pide instrucciones por HTTP → le decimos que abra un
WebSocket con nosotros → por ahí viaja el audio en los dos sentidos → lo transcribimos,
se lo damos a Claude, sintetizamos su respuesta y la devolvemos. Todo lo dicho queda
en PostgreSQL, turno a turno.

## Herramientas

| Pieza | Qué se usa | Por qué |
|---|---|---|
| Lenguaje | **Python 3.10** | |
| Web y WebSocket | **FastAPI** + **uvicorn** | un solo proceso sirve webhooks, audio y panel |
| Base de datos | **PostgreSQL** con **psycopg 3** | registro de llamadas y ajustes |
| Modelo | **Claude** (`claude-haiku-4-5`) vía **anthropic** | rapidez: en una llamada la latencia manda |
| Reconocimiento | **Deepgram** · respaldo **faster-whisper** local | Deepgram entiende mucho mejor el audio telefónico (8 kHz) |
| Voz | **Cartesia** · respaldos **edge-tts** y **piper** | Cartesia hace streaming; los respaldos tardan ~1,9 s en la primera frase |
| Telefonía | **SignalWire** (o **Twilio**) | hablan el mismo dialecto: TwiML/LaML |
| Audio | **av** (FFmpeg) | convierte el MP3 de edge-tts a µ-law |
| Publicación | **cloudflared** | el operador tiene que alcanzar la máquina desde fuera |

Sin claves de Deepgram y Cartesia el agente **funciona igual**, sólo que más lento y
entendiendo peor. La de Anthropic sí es obligatoria.

## Cómo está montado

```
servidor.py          arranca todo en un proceso

nucleo/              lo propio de este producto
  ajustes.py         configuración: PostgreSQL manda, config.json es el espejo
  almacen.py         registro de llamadas: llamada, turno, evento, consumo, pedido…
  web.py             los webhooks que consulta el operador
  panel.py           el panel y su API, cerrados a lo que no venga de localhost
  carrier.py         habla con SignalWire o Twilio
  telefono.py        E.164, país y franja horaria legal
  negocio.py         compone el saludo y el contexto que lee el modelo
  herramientas.py    lo único que el agente puede hacer además de hablar
  acceso.py          la clave para enseñarlo desde fuera
  estatico/          el panel (HTML, CSS y JS a mano, sin framework)

voz_agente/          el motor de voz, portado de otro proyecto
  puente_twilio.py   el WebSocket del audio: VAD, interrupciones, colgar
  agente.py          el bucle con Claude, en streaming y por frases
  audio.py           µ-law ↔ PCM, 8 kHz, tramas de 20 ms
  bitacora.py        escribe los turnos según se producen
  proveedores/       un fichero por proveedor de voz y reconocimiento
  estatico/          la demo por navegador
```

**Los dos árboles están separados a propósito.** `voz_agente/` viene de otro proyecto
y se mantiene comparable fichero a fichero con su origen; lo que diverge lleva un
comentario que dice por qué. Todo lo que es de este producto vive en `nucleo/`.

## El recorrido de una llamada

1. `POST /entrante` — el operador pregunta qué hacer. Se crea la ficha y se devuelve
   TwiML con `<Connect><Stream>` apuntando al WebSocket.
2. `WS /twilio` — llega el audio en µ-law a 8 kHz, en tramas de 20 ms. El puente
   detecta cuándo el interlocutor deja de hablar (1200 ms de silencio), manda el
   tramo al reconocedor y el texto a Claude.
3. La respuesta se sintetiza **por frases** y se devuelve según sale, para que el
   primer audio llegue cuanto antes. Si el interlocutor habla encima, se descarta lo
   que quede en el búfer del operador.
4. Cada turno se escribe en PostgreSQL al producirse, no al colgar.
5. Cuando la conversación termina, el agente usa `terminar_llamada` y el puente cierra
   el WebSocket, que es lo que cuelga.

Las salientes son iguales salvo el arranque: las crea `llamar.py` y el operador pide
las instrucciones a `POST /twiml/{id}`.

## Rutas

**Públicas** (las llama el operador, autenticadas con `?t=<web.token>`):

```
POST /entrante          qué decir al recibir una llamada
POST /twiml/{id}        qué decir al descolgar una saliente
POST /estado/{id}       avisos de estado del operador
WS   /twilio            el audio
```

**Locales** (rechazan todo `Host` que no sea localhost, salvo con clave de acceso):

```
GET  /panel             el panel
GET  /                  la demo por navegador
GET  /salud             estado del servicio, en JSON
GET/POST /api/config    configuración
GET  /api/vivo          pedidos y llamada en curso, para el sondeo del panel
GET  /api/llamadas[/id]  ·  GET /api/pedidos[/id]  ·  POST /api/llamar  ·  …
```

## Decisiones que conviene entender antes de tocar nada

**El registro nunca levanta hacia fuera.** `almacen.py` traga sus excepciones y
devuelve `None`. Está en el camino del audio: que falle guardar no puede cortar una
llamada en curso. La contrapartida es que un fallo ahí sale como un log y una tabla
vacía, no como un error — por eso hay tantas comprobaciones al arrancar.

**El agente sólo puede escribir, nunca consultar.** Tiene tres herramientas:
`anotar_pedido`, `registrar_no_llamar` y `terminar_llamada`. No tiene catálogo, ni
agenda, ni historial. Conectarlo al sistema de un cliente es trabajo aparte por cliente.

**Sólo puede decir que anotó algo después de que la herramienta responda que sí.** Va
en el prompt y hay tests que lo comprueban. Es la diferencia entre esto y un chatbot
que le promete a un cliente algo que no ha quedado registrado.

**El token de la query no es pereza.** SignalWire firma sus webhooks con un secreto que
la cuenta no expone, así que ese `?t=` es la única autenticación posible. Está
explicado en `nucleo/carrier.py`.

**El panel enseña las claves y desde él se marca**, y para recibir llamadas hay que
publicar el puerto entero con un túnel. Por eso cada ruta del panel comprueba la
cabecera `Host`.

## Configuración

Vive en PostgreSQL, tabla `ajuste`, una fila por sección. `config.json` guarda la
sección `bd` —cómo llegar a la base, que no puede estar dentro de ella— y es el espejo
de respaldo: si Postgres no contesta, el servicio arranca con lo último guardado y lo
avisa, en vez de quedarse sin atender llamadas.

**Las claves están en texto plano**, en la base y en el fichero. Para producción hay
que cambiarlo; para un portátil de desarrollo fue una decisión consciente.

`config.json` no se versiona. `config.example.json` sí, y documenta la forma.

## Probarlo

```bash
python pruebas/test_nucleo.py      # 42 tests, gratis, un segundo
python pruebas/escenarios.py       # habla con Claude: cuesta céntimos y tarda
python servidor.py --revisar       # comprueba la configuración y sale
```

Los primeros no llaman a ningún proveedor y son independientes del negocio. Los
escenarios someten al agente a lo que rompe una demo —que niegue ser una máquina, que
invente un precio, que diga que anotó sin anotar— y **están escritos para la
panadería**: con otro negocio cargado hay que reescribirles las frases.

## Lo que falta para producción

Esto es una demo sólida, no un producto instalado. Por orden:

1. **Dominio fijo.** El túnel rápido de cloudflared cambia de nombre en cada arranque
   y hay que volver a pasar `apuntar.py`. Es la causa número uno de «marca y no dice
   nada».
2. **Arranque como servicio**, para que sobreviva a un reinicio.
3. **Las claves fuera del texto plano.**
4. **Vigilancia**: hoy nadie avisa si el servicio se cae.
5. **Números locales.** Ecuador (ARCOTEL) exige identificar al titular. SignalWire sólo
   vende números de EE. UU.; Telnyx acepta dirección de cualquier país. Es papeleo del
   cliente, no código.

## Scripts de operación

```bash
python apuntar.py <url>            apunta el número al túnel de ahora. Tras cada reinicio
python llamar.py +593...           hace una llamada saliente
python conciliar.py                pregunta al operador cómo acabaron las llamadas
python limpiar.py                  borra las llamadas de prueba sin transcripción
python usar_ejemplo.py             carga un negocio de ejemplos/
python migrar_a_postgres.py        pasa un llamadas.db antiguo a PostgreSQL
```

Todos informan por defecto y sólo escriben con `--hazlo`.
