# Guion de la demostración

Para el día 9. Lee esto antes, no durante.

**Cambió lo más importante desde la primera versión de este guion:** la noche del 8
entraron seis llamadas de teléfono de verdad desde tu celular, el agente contestó,
conversó y anotó el pedido #68. Ya no hay que enseñar esto por navegador. El navegador
pasa a ser el plan B.

## Diez minutos antes

**0. Comprueba que PostgreSQL está arriba.** El registro y los ajustes viven ahí. Si
el servicio de Postgres no arranca, el agente **sigue atendiendo llamadas** con la
copia de `config.json` —eso está previsto— pero no guardará ni un pedido, que es
justo el remate de la demo. El panel lo dice en ámbar arriba del todo si pasa.

**1. Arranca el servicio.** En una terminal, dentro de `D:\skytech\vozagente`:

```
.venv\Scripts\python.exe servidor.py
```

Con el túnel puesto dice «Nada bloquea una llamada». Si dice «La demo por navegador
funciona. Por teléfono todavía no», es que falta el punto 2. Lo que sí es un problema
son las líneas marcadas `[!]`: ésas salen con nombre y apellidos y hasta que no
desaparezcan el agente no habla.

**2. Levanta el túnel. Ahora es obligatorio**, no opcional: la llamada de teléfono
entra desde fuera y tiene que llegar hasta este portátil. En otra terminal:

```
cloudflared tunnel --url http://localhost:8600
```

Copia el dominio que imprime y pégalo en el panel → **Claves y telefonía**:

- **URL pública**: `https://loquesea.trycloudflare.com`
- **URL del audio**: `wss://loquesea.trycloudflare.com/twilio`

Guarda. Abajo aparecen los dos enlaces con la clave puesta, listos para mandar.

> El túnel **cambia de nombre cada vez que lo reinicias**. Si algo deja de funcionar a
> mitad, es esto el 90% de las veces.

**3. Apunta el número al túnel.** Es el paso que se olvida, y sin él la llamada entra,
el proveedor pide instrucciones a un dominio que ya no existe y **se cae al
descolgar**. Copia la URL que imprimió cloudflared y:

```
.venv\Scripts\python.exe apuntar.py https://loquesea.trycloudflare.com
```

Guarda la URL en el panel y repunta el webhook del número, las dos cosas de una vez.
Termina imprimiendo a dónde apunta cada número, releído del proveedor. **Hay que
volver a pasarlo cada vez que reinicies cloudflared**, porque el dominio cambia.

> El `+1 208 379 8293` es el de producción de skytech y `apuntar.py` **se niega** a
> tocarlo. El de la demo es el `+1 208 398 6190`.

**4. Deja dos pestañas abiertas**: la demo (`localhost:8600`) y el panel
(`localhost:8600/panel`), en Pedidos.

**5. Deja las dos pestañas presentables.** Ya lo están: quedan las seis llamadas de
teléfono del día 8, la del navegador y el pedido #68, y nada más. Si vuelves a ensayar,
pásalo otra vez antes de empezar:

```
.venv\Scripts\python.exe limpiar.py            dice qué hay, sin tocar nada
.venv\Scripts\python.exe limpiar.py --hazlo    lo borra, dejando un volcado .sql al lado
```

No toca las llamadas que tengan transcripción, así que el ensayo bueno se queda.

Y si alguna llamada aparece **«en curso» con duración cero** —pasa si el servicio se
cae o se reinicia con una llamada abierta, porque una entrante no recibe el aviso de
estado del proveedor—, se arregla preguntándole al proveedor cómo acabó:

```
.venv\Scripts\python.exe conciliar.py          dice qué corregiría, sin tocar nada
.venv\Scripts\python.exe conciliar.py --hazlo  lo escribe, dejando un volcado .sql al lado
```

> Los dos hacen su copia con `pg_dump`, no copiando un fichero: **el registro vive
> ahora en PostgreSQL**, no en `llamadas.db`. El `llamadas.db` que sigue en la carpeta
> es la copia de antes de migrar y no lo lee nadie.

**6. Haz una llamada tú.** Marca al `+1 208 398 6190` desde tu celular y haz el
encargo entero una vez. No lo despaches con un «hola»: **este ensayo es tu plan B**, y
sólo queda registrado lo que se habla. Si no lo haces y algo falla en directo, la
pestaña de Llamadas no tiene nada que enseñar.

**7. Y di una frase por navegador**, que es el plan B y conviene saber que entra.
Pulsa Iniciar, espera a que termine de calibrar el micrófono —dice «Calibrando, no
hable todavía»— y habla. Si el número de al lado de la barra no se mueve, el micrófono
no está entrando; míralo ahora y no delante de todos.

## Por teléfono, que es lo que conviene enseñar

**Se llama al `+1 208 398 6190`.** Para que entre por tu número ecuatoriano y no por
el americano, está el desvío de tu línea CNT a ese número. Al agente le da igual por
dónde entre.

**Comprueba el saldo en dinero de CNT, no el plan de minutos.** Cuatro llamadas se
cortaron a los 42, 43 y 21 segundos y parecía fallo del agente: era el saldo a $0,00.
El paquete trae 30 minutos internacionales, pero CNT los cobra del saldo. Marcar con
`00` en vez de `+` lo empeora, no lo arregla. Se recarga y deja de pasar.

Lo medido el día 8: la llamada más larga 2m18s sin cortes, primer audio entre 2,5 y
3,3 segundos, respuestas siguientes en 1,2-1,4.

**Las respuestas son más lentas ahora, y es a propósito.** Medido el 18: 0,7 s el
saludo y entre 2,2 y 2,8 s las respuestas. El agente espera 1200 ms de silencio antes
de dar tu turno por terminado, en vez de los 800 de antes. Con 800 cortaba a quien
hace la pausa normal antes de decir un nombre —«...a nombre de» se enviaba a
transcribir dos veces y el encargo se perdía entero—, y un encargo se toma dictando
nombres. Si en el ensayo lo notas lento, se baja sin tocar código con la variable
`VOZ_SILENCIO_FIN_MS`.

## La demostración, en tres minutos

**Primero enséñale lo que sabe.** Pregúntale algo que esté en su ficha:

> ¿A qué hora abren los domingos?

Contesta con el horario real, dicho como lo diría una persona.

**Luego enséñale que no se inventa nada.** Pregúntale un precio:

> ¿Cuánto cuesta una torta para veinte personas?

Dice que depende del diseño y que un compañero lo confirma. **No suelta una cifra.**
Esa es la diferencia entre esto y un chatbot que te mete en un problema con un cliente.

**Después, que admita lo que es.** Es lo que todo el mundo pregunta:

> ¿Tú eres una persona o un robot?

Dice que sí, que es un robot, sin rodeos. Va en las reglas y no se puede desactivar
desde el guion: es lo que se enseña si alguien reclama.

**Y el remate: haz un encargo.**

> Quiero encargar una torta de selva negra para veinte personas, para el sábado, a
> nombre de [su nombre].

Dice «Un momento, lo anoto», guarda, y **sólo entonces** confirma.

**No hace falta refrescar el panel: el pedido aparece solo.** Ten la pestaña de Pedidos
a la vista mientras hablas y déjala en pantalla — se actualiza cada tres segundos. Y
arriba, en la cabecera, hay una franja roja con **la llamada en curso y lo último que
se ha dicho**, que se ve desde cualquier pestaña. Un clic en ella abre la conversación
entera.

Ese es el momento de la demo. Todo lo anterior lo hace cualquier chatbot; esto es lo
que convierte una conversación en trabajo hecho.

**Y al despedirte, cuelga él.** Di «gracias, nada más» y espera: se despide y corta la
llamada a los dos o tres segundos. No cuelgues tú — que cuelgue el agente delante de
todos es mejor de lo que parece, porque la pregunta siguiente suele ser «¿y se queda la
línea abierta gastando?».

**Si te sobra tiempo**, cambia el encargo a mitad («mejor cheesecake y para cuarenta»)
y enseña que el pedido viejo queda marcado como cambiado, no duplicado. O pincha
cualquier fila de Pedidos: se abre una ficha con el encargo **y la conversación de la
que salió**, que es la respuesta a «¿y cómo sé que dijo eso?».

Y si quien escucha es técnico, el dato que le va a interesar es dónde queda todo eso:
es una base PostgreSQL normal, la tabla `pedido`, consultable desde cualquier
herramienta. No hay que integrarse con nada nuestro para sacar los encargos. **Pero no
abras la tabla `ajuste`** — ahí están las claves.

## Lo que va a preguntar tu jefe

**«¿Esto funciona por teléfono de verdad?»**
Sí, probado el 8 de septiembre con llamadas reales. Lo que falta es un número
ecuatoriano propio, y no es un problema de código: Ecuador (ARCOTEL) exige identificar
al titular, así que todos los proveedores piden papeles. SignalWire **sólo vende
números de EE. UU.** —su listado de Ecuador devuelve números +1—; Twilio pide domicilio
en Ecuador y registro mercantil, que son papeles del cliente; **Telnyx acepta dirección
de cualquier país**, pide una carta y prueba de domicilio, tarda unas 72 horas y ronda
los 78 dólares al mes. Ése es el camino cuando haya un cliente. Hasta entonces, el
desvío desde la línea de Ecuador hace el mismo papel y no cuesta papeleo.

**«¿Cuánto tarda en contestar?»**
El saludo sale por debajo del segundo; las respuestas, entre 2,2 y 2,8 segundos.
Medido en llamadas reales, no estimado. De esos segundos, 1,2 son espera deliberada:
es lo que aguanta antes de dar por terminado lo que estás diciendo, y bajarlo hace que
corte a quien duda a mitad de frase. Es un ajuste, no un límite técnico.

**«¿Cuánto cuesta cada llamada?»**
En telefonía, **las seis llamadas del día 8 costaron 9,6 centavos en total** —eso es lo
que factura el proveedor, no una estimación—: un céntimo por minuto recibido. Súmale
reconocimiento, voz y modelo, que son unos pocos céntimos por minuto.

Lo que hay que decir a continuación, porque reordena el producto: **recibir** ronda el
céntimo por minuto, **llamar** a un móvil ecuatoriano ronda los 53. Cincuenta veces. Por
eso esto es un producto de **recibir** llamadas; las campañas salientes en Ecuador son
otro producto, y caro.

**«¿Sirve para otro negocio?»**
Sí, y es el punto: se cambia el nombre, la descripción y el guion en el panel, y en dos
minutos es otra empresa. Nada del código sabe de panaderías.

**«¿Puede agendar citas / consultar el stock?»**
Hoy sólo puede apuntar lo que le piden. Consultar exige conectarlo al sistema que tenga
el negocio, y eso es trabajo aparte para cada cliente.

## Si algo falla en directo

| Qué ves | Qué es |
|---|---|
| La llamada se cae al descolgar | El webhook del número no apunta al túnel de ahora, o le falta el `?t=` |
| Se corta a mitad, a los 40 segundos | Saldo de CNT a cero. No es el agente |
| **No da ni tono, ni suena** | No llegó al proveedor. Saldo de CNT, casi siempre. En el panel no habrá ni rastro, porque nunca entró |
| Suena y no contesta nadie | El servicio se cerró, o el túnel se cayó. Mira la terminal |
| Conversa bien pero no aparece el pedido | PostgreSQL. El panel lo dice en ámbar arriba |
| **Te corta a media frase** | Sube `VOZ_SILENCIO_FIN_MS` (está en 1200) y reinicia |
| Por navegador, el agente no responde | Mira el número junto a la barra. Si no se mueve, es el micrófono |
| Por navegador, «No se pudo conectar» | El servicio se cerró. Vuelve a arrancarlo |
| Suena entrecortado | Red. Cuelga y vuelve a llamar |

**Plan B, en dos escalones**: si el teléfono da guerra, enseña lo mismo por navegador
en `localhost:8600`, que es el mismo motor. Si tampoco, en el panel → Llamadas están
las conversaciones del día 8 con su transcripción, incluida la que dejó el pedido #68.
Enseñar una conversación leída no es lo mismo, pero se entiende.

## Lo que NO conviene enseñar

- **La pestaña de Claves** con el proyector encendido: son las claves de verdad. Y
  desde que los ajustes están en PostgreSQL, tampoco conviene abrir la tabla `ajuste`
  en pgAdmin delante de nadie: las claves están ahí dentro, en claro.
- **Marcar tú a un móvil ecuatoriano** delante de todos. La saliente a Ecuador no está
  probada con esta cuenta —la que dio el error 30006 en agosto era la personal, en
  Trial, y no es ésta— y además cuesta cincuenta veces más que recibir. Si te lo piden,
  enseña la entrante: es lo verificado y es el producto.
