# Agente de voz

Un asistente telefónico que atiende y hace llamadas por un negocio. Contesta al
teléfono, entiende lo que le dicen, responde con voz y deja la conversación
registrada.

Un servicio, un negocio. Se describe entero en la configuración —quién es, qué vende,
qué debe decir— y no hace falta tocar código para cambiarlo: en `ejemplos/` hay una
panadería y una clínica dental funcionando sobre el mismo código.

```
python servidor.py            # atiende llamadas y sirve la demo
python llamar.py +593999123456   # hace una llamada
```

## Puesta en marcha

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt   # Linux: .venv/bin/pip
copy config.example.json config.json            # Linux: cp
```

Hace falta un **PostgreSQL** al que conectarse (vale el local de toda la vida) y poner
sus datos en la sección `bd` de `config.json`. **La base y las tablas se crean solas**
al arrancar: no hay que preparar nada a mano, sólo que Postgres esté levantado y que
el usuario de `bd` pueda crear bases.

Si no puede, el servicio lo dice como bloqueo y no lo esconde. Es a propósito: sin
base el agente conversaría igual y no guardaría ni un pedido, y eso no se manifiesta
como un error sino como una pestaña de Pedidos vacía que parece normal.

Quien venga de una versión anterior, que tenía el registro en un `llamadas.db` de
SQLite, lo pasa con:

```bash
.venv\Scripts\python.exe migrar_a_postgres.py            # dice qué copiaría
.venv\Scripts\python.exe migrar_a_postgres.py --hazlo    # lo copia
```

Conserva los identificadores —un pedido apunta a su llamada por id— y **no borra el
`llamadas.db`**: mientras exista, volver atrás es cambiar el `almacen.py`.

Luego se arranca y se configura desde el panel:

```bash
python servidor.py
```

y se abre <http://localhost:8600/panel>. Ahí se pone todo: el negocio, los guiones,
las claves de los proveedores y la telefonía. Arriba se ve, en todo momento, qué
impediría que una llamada sonara bien.

**Los ajustes viven en PostgreSQL**, en la tabla `ajuste`, una fila por sección.
`config.json` sigue existiendo y hace dos cosas que la base no puede hacer: guarda la
sección `bd` —cómo se llega a la base, que no puede estar dentro de ella— y es la copia
de seguridad. Si Postgres no contesta, el servicio arranca con lo último guardado en el
fichero y lo dice en ámbar arriba del panel, en vez de quedarse sin atender llamadas.

Al guardar desde el panel se escriben los dos: primero la base, después el espejo.
Editar `config.json` a mano sigue funcionando para la sección `bd`; para el resto, lo
que mande la base gana en la siguiente carga.

`python servidor.py --revisar` comprueba la configuración y sale sin arrancar.

## Montarlo para otro negocio

**El código no sabe de panaderías.** Sabe atender un teléfono, entender lo que le
dicen y apuntar lo que le piden; quién es, qué vende y qué debe decir entra entero por
configuración. En `ejemplos/` hay negocios ya escritos para partir de uno:

```bash
python usar_ejemplo.py                    # los lista
python usar_ejemplo.py clinica --hazlo    # lo aplica
```

Sólo tocan lo que describe al negocio —nombre, idioma, vocabulario, descripción,
guiones y franja horaria—. **No tocan claves, telefonía, URL ni la conexión a la
base**: eso es de la instalación, y sobrescribirlo al probar otro ejemplo dejaría el
servicio sin poder llamar.

Los dos que hay no son adorno: `panaderia` toma encargos y `clinica` toma solicitudes
de cita **sin confirmar hora** y deriva a una persona si quien llama dice que tiene
dolor. Mismo código, comportamientos distintos, y la diferencia está toda en el guion.

Para un negocio nuevo, lo más rápido es copiar el ejemplo más parecido, cambiarle la
descripción y el guion, y afinar desde el panel — que se relee en cada llamada.

## El panel

<http://localhost:8600/panel>

Cuatro pestañas: **Negocio** (qué sabe el agente), **Guiones** (qué dice),
**Claves y telefonía**, y **Llamadas** (marcar, y leer lo que se habló).

Los campos de claves llevan tres botones: **ojo** para verla, **copiar** y **pegar**.
Al pegar se recortan espacios y saltos de línea — copiar una clave de una web arrastra
un salto invisible detrás, y eso falla luego con un error de autenticación que no dice
nada de eso.

**El panel no puede quedar expuesto.** No lleva contraseña, enseña las claves y desde
él se marca. Y el riesgo es real, no teórico: para recibir llamadas hay que publicar
este mismo servicio con un túnel, y un túnel expone el **puerto entero**.

Por eso cada ruta del panel comprueba la cabecera `Host` y rechaza lo que no venga de
`localhost`. No comprueba la IP: cloudflared se conecta desde la propia máquina, así
que a través del túnel la petición llega igualmente de 127.0.0.1 — lo que sí cambia es
el `Host`. Los webhooks del proveedor no se ven afectados y siguen entrando por el
túnel con normalidad.

El aviso de arriba del panel nació de que el fallo típico de esto no es una excepción:
es una llamada que suena y sale muda, y averiguar por qué cuesta una llamada por
intento.

**Lo mínimo para que hable**: `negocio.nombre`, `ia.anthropic_api_key` y `web.token`.
Sin `web.url_publica` no hay llamadas (pero la demo por navegador funciona).

## Comprobar que sigue bien

```bash
.venv\Scripts\python.exe pruebas	est_nucleo.py    # gratis, en un segundo
.venv\Scripts\python.exe pruebas\escenarios.py     # habla con Claude: cuesta y tarda
```

Los primeros no llaman a ningun proveedor: comprueban a que numero se marca, a que
hora, que sabe el agente y que puede guardar. Son independientes del negocio y pasan
igual con cualquier ejemplo cargado.

Los escenarios someten al agente a las conversaciones que rompen una demo —que niegue
ser una maquina, que se invente un precio, que diga que anoto algo sin anotarlo— y
cuestan unos centimos por pasada. **Estan escritos para la panaderia**: preguntan por
tortas y por porciones, asi que con otro negocio cargado fallan por hablar de lo que
no es. Al montar un cliente nuevo hay que reescribirles las frases; lo que se comprueba
—las tres trampas de arriba— vale para cualquier sector.

Para la reunion, ver `DEMO.md`.

## Probar sin gastar una llamada

```bash
python servidor.py
```

y abrir <http://localhost:8600/>. Se habla por el micrófono y contesta. Es el mismo
motor, el mismo guion y la misma voz que por teléfono: lo único que no ejercita es el
tramo del operador. Para enseñar el agente a alguien, **esto antes que un softphone** —
un softphone añade sus propios fallos y cuesta días distinguirlos de los tuyos.

`getUserMedia` sólo funciona sobre HTTPS o en localhost, así que la demo se abre en
local aunque el servicio esté publicado.

## Enseñarlo desde fuera

Por defecto **el servicio sólo se abre desde la máquina donde corre**: la demo y el
panel dan 403 a cualquier otra cosa. Los webhooks del proveedor no, esos entran
siempre — traen su propia autenticación y para eso existe el túnel.

Eso no es prudencia de más. Un túnel expone el **puerto entero**, y ahí viven la demo
—que gasta saldo de tres proveedores en cada turno—, el panel con las claves a la
vista y el botón de marcar.

Para abrirlo, se pone una **clave de acceso** en el panel (Claves y telefonía →
Enseñarlo desde fuera). En cuanto la hay, el panel enseña dos enlaces con la clave ya
puesta, listos para copiar y mandar:

```
https://loquesea.trycloudflare.com/?k=tu-clave        hablar con el agente
https://loquesea.trycloudflare.com/panel?k=tu-clave   este panel
```

La clave viaja en la URL la primera vez y se guarda en una cookie, así que el enlace
funciona con un solo clic y luego se navega con normalidad. Cubre también el
WebSocket de la conversación, que es por donde se gasta el dinero.

**No es autenticación de verdad y no pretende serlo**: es una frase secreta para poder
enseñar el producto unos días sin dejarlo abierto a quien dé con la URL. Quien tenga
el enlace entra. Para algo permanente, un proxy con TLS y usuarios delante.

Para publicarlo hace falta el túnel, y **el mismo túnel sirve para las tres cosas** —
la demo, el panel y los webhooks:

```bash
cloudflared tunnel --url http://localhost:8600
```

Después, en el panel, se pega el dominio que imprime cloudflared en **URL pública** y
**URL del audio** (esta con `wss://` y acabada en `/twilio`). El túnel rápido cambia
de nombre en cada arranque, así que hay que rehacerlo cada vez.

Un detalle que sólo aparece al enseñarlo desde otro sitio: el micrófono del navegador
exige HTTPS, así que la demo **no** funciona por `http://` a una IP de la red local.
Por el túnel sí, porque cloudflared da HTTPS.

## Poner el teléfono

El operador tiene que poder alcanzar este servicio desde fuera. En desarrollo, un
túnel:

```bash
cloudflared tunnel --url http://localhost:8600
```

y en `config.json`:

```json
"web": {
  "url_publica": "https://loquesea.trycloudflare.com",
  "url_stream": "wss://loquesea.trycloudflare.com/twilio",
  "token": "un-secreto-largo"
}
```

**El túnel rápido de cloudflared cambia de nombre en cada arranque**, así que estas
dos URL caducan cada vez que se reinicia. Es la causa número uno de «marca y no dice
nada».

Para **recibir** llamadas, además hay que apuntar el número en la consola del
operador a:

```
https://tu-dominio/entrante?t=<web.token>
```

**El `?t=` no es opcional.** SignalWire firma sus webhooks con un secreto del *space*
que la cuenta no expone —se probaron 5.600 combinaciones contra 16 peticiones reales
y ninguna la reproduce—, así que ese token es la única autenticación posible. Sin él
la petición se rechaza con un 403 y la llamada muere sin dejar ficha.

Las salientes no necesitan esto: la URL viaja en la propia petición que crea la
llamada.

## Qué se publica y qué no

Detrás del túnel o del proxy sólo deben quedar expuestas:

| Ruta | Para qué |
|---|---|
| `POST /twiml/<id>` | qué decir al descolgar una llamada saliente |
| `POST /entrante` | qué decir al recibir una llamada |
| `POST /estado/<id>` | avisos de estado del operador |
| `WS /twilio` | el audio de la llamada |

`/panel`, `/api/*`, `/`, `/ws`, `/salud` y `/llamadas` **se cierran solos a lo que no venga de esta máquina**, salvo que se ponga una clave de acceso (ver arriba). Sin esa puerta, cualquiera con la URL del túnel podría hablar con el agente y quemar el saldo, o abrir el panel y leer las claves.

## Escribir el guion

Dos campos por dirección, y no dicen lo mismo:

- **`saludo_*`** se dice **literal**, sin pasar por el modelo, para que salga al
  instante. Vacío, se compone con el nombre del negocio.
- **`guion_*`** son **instrucciones** que el modelo lee y redacta con sus palabras.
  Por eso dos llamadas con el mismo guion no suenan idénticas. Para forzar una frase
  exacta hay que escribir `Di exactamente: "..."`.

Entrante y saliente se escriben aparte a propósito: quien nos llama ya sabe a dónde
llamó, y abrirle con «le llamamos» suena a que le hemos marcado nosotros.

Lo que se escriba en `descripcion` es **todo** lo que el agente sabe. Conviene incluir
lo que **no** sabe y debe derivar a una persona: sin eso, ante una pregunta que el
guion no cubre, se lo inventa.

Se relee en cada llamada, así que se retoca el fichero y la llamada siguiente ya lo
usa. No hay que reiniciar nada.

### Lo que el agente hace siempre, se escriba lo que se escriba

Van pegadas al final de cualquier guion, porque es lo último que lee el modelo y lo
que gana cuando el guion dice lo contrario:

- **Si le preguntan si es un robot, dice que sí.** Sin rodeos. Es lo que se enseña si
  alguien reclama, y no se puede desactivar desde el guion.
- No inventa precios, plazos ni disponibilidad.
- No pide datos de tarjeta ni contraseñas.
- Si le piden que no llamen más, se disculpa y se despide sin insistir.

## Llamar

```bash
python llamar.py +593999123456      # pide confirmacion antes de marcar
python llamar.py --revisar          # comprueba la cadena entera sin gastar nada
python llamar.py +59399... --ahora  # se salta la franja horaria
```

**La franja horaria es la del destinatario, no la nuestra.** En EE. UU. la TCPA son
8:00-21:00 hora suya y saltársela cuesta entre 500 y 1.500 USD por llamada. Lo que se
configure en `llamadas` sólo puede **estrechar** ese techo, nunca ampliarlo. `--ahora`
lo salta para probar y lo deja anotado en la ficha.

La **lista de no-llamar** no admite excepciones, ni con `--ahora`.

En Ecuador, ARCOTEL exige autorización previa para llamadas comerciales.

## Ver qué se dijo

```
http://localhost:8600/llamadas          # las ultimas 50, en JSON
http://localhost:8600/llamadas/7        # la conversacion, legible
```

Todo va a PostgreSQL, a la base que diga `bd` en `config.json`. Las tablas se crean
solas; la base no, porque `CREATE DATABASE` necesita permisos que el servicio no
tiene por qué tener —de eso se encarga `migrar_a_postgres.py`, o se crea a mano desde
pgAdmin—. La conversación se guarda **turno a turno**, no al colgar: una llamada que se
corta o un reinicio dejarían la conversación entera sin registrar.

Se puede mirar desde pgAdmin mientras el agente escribe. Las tablas son `llamada`,
`turno`, `evento`, `consumo`, `pedido` y `no_llamar`.

## Cómo está montado

```
servidor.py      arranca todo (webhooks + puente de audio + demo)
llamar.py        hace una llamada desde la terminal
config.json      el negocio, las claves, las URL y la base. No se versiona.
migrar_a_postgres.py  pasa un llamadas.db antiguo a PostgreSQL. Se usa una vez.
usar_ejemplo.py  carga uno de los negocios de ejemplos/
ejemplos/        negocios ya escritos: panaderia, clinica. El codigo no sabe de ninguno.

nucleo/          lo propio de este producto
  ajustes.py     lee y escribe config.json
  negocio.py     compone saludo y contexto: quien es el agente
  telefono.py    E.164, pais y franja horaria legal
  carrier.py     habla con SignalWire o Twilio
  almacen.py     el registro, en PostgreSQL
  web.py         los webhooks del operador
  panel.py       el panel y su API, cerrados a lo que no sea local
  estatico/      la pagina del panel

pruebas/         test_nucleo.py (gratis) y escenarios.py (habla con Claude)

voz_agente/      el motor de voz
  puente_twilio.py  el audio de la llamada, en tiempo real
  agente.py         la conversacion con el modelo
  proveedores/      reconocimiento (Deepgram/Whisper) y voz (Cartesia/edge)
```

Un solo proceso sirve las tres cosas. El modelo de reconocimiento tarda en cargar y
ocupa memoria, así que partirlo significaría cargarlo dos veces.

### De dónde viene esto

`voz_agente/` viene del motor de voz de skytech y **se mantiene deliberadamente
parecido**, fichero a fichero, para poder comparar los dos árboles y portar arreglos
en un sentido o en otro. Lo que diverge lleva un comentario que dice por qué. Las tres
divergencias reales:

- **`bitacora.py`** escribe directo en la base. Allí el puente vivía en otro proceso sin
  ORM y mandaba los turnos por HTTP; aquí es el mismo proceso, así que desaparecen el
  salto HTTP, su token y los fallos en que el audio va bien y la transcripción se
  pierde por un 403.
- **`herramientas.py`** está vacío. Las de allí consultan cotizaciones y clientes de
  otra empresa: aquí no significan nada, y traerlas daría un agente que ofrece cosas
  que no puede hacer. Cuando este producto tenga las suyas, el sitio es ese fichero y
  el bucle ya está montado.
- **`agente.py`** cachea el contexto del negocio; allí lo dejaba fuera. Ver el
  comentario en `_bloque_system`: allí el contexto cambiaba en cada llamada y aquí es
  el mismo siempre.

## Proveedores

| Qué | Preferido | Respaldo |
|---|---|---|
| Reconocimiento | Deepgram (`nova-3`) | Whisper local |
| Voz | Cartesia | edge-tts |
| Modelo | Claude Haiku | — |

Se eligen solos según qué claves haya. Los respaldos funcionan pero se notan:
edge-tts tarda ~1,9 s en la primera frase y por teléfono eso se oye como una línea
muerta; Whisper sobre audio telefónico de 8 kHz entiende bastante peor.

**Whisper no se instala por defecto** (son ~1 GB de dependencias). Si se quiere el
respaldo local: `pip install faster-whisper`.

`vocabulario` en `config.json` es para el reconocedor, no para el modelo: le adelanta
los nombres propios que va a oír. Sin él, el nombre del negocio —la palabra que más se
va a decir— es justo la que peor transcribe.

## Cuando algo no funciona

| Síntoma | Causa habitual |
|---|---|
| Marca, descuelga y no dice nada | `web.url_publica` apunta a un túnel que ya no existe |
| El operador reproduce su aviso de error | El servicio está apagado y el túnel vivo (Cloudflare da 530) |
| `403` en los webhooks | Falta el `?t=` en la URL, o el token no coincide |
| La entrante no deja ficha | El número no apunta a `/entrante?t=…` en la consola del operador |
| Suena entrecortado | Sin `ia.cartesia_api_key`: el respaldo de voz no da el tiempo real |
| Entiende mal los nombres | Sin `ia.deepgram_api_key`, o falta `vocabulario` |
| Habla de otra empresa | `negocio.descripcion` vacía: sin ella no sabe de qué hablar |

`GET /salud` responde con el estado de todo: qué proveedor está activo, qué falta y
qué bloquea una llamada.
