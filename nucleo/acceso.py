"""Quién puede entrar cuando el servicio está publicado.

Para recibir llamadas hay que publicar este servicio con un túnel, y un túnel expone
el **puerto entero**. Eso deja tres cosas al alcance de cualquiera que dé con la URL:
la demo por voz —que gasta saldo de tres proveedores en cada turno—, el panel —con
las claves a la vista— y el botón de marcar.

La regla es una sola, y se aplica en un middleware ASGI en vez de ruta por ruta
porque así cubre también el WebSocket de la demo, que vive en el motor y no conviene
tocar:

1. **Los webhooks del proveedor pasan siempre.** Traen su propia autenticación (el
   `?t=` de `nucleo/web.py`) y tienen que funcionar desde fuera: para eso existe el
   túnel.
2. **Desde esta máquina se entra sin nada.** Es el caso normal mientras se trabaja.
3. **Desde fuera hace falta la clave** de `web.clave_acceso`. Sin clave configurada,
   desde fuera no se entra a nada que no sea un webhook.

La clave viaja como `?k=…` la primera vez y se guarda en una cookie, para que
funcionen después los ficheros estáticos y el WebSocket, que no llevan query.

**No es autenticación de verdad y no pretende serlo**: es una frase secreta para
enseñar el producto unos días sin dejarlo abierto. Para algo permanente, un proxy
con TLS y usuarios delante.
"""

import hmac
import logging
from urllib.parse import parse_qs

logger = logging.getLogger(__name__)

COOKIE = 'voz_acceso'

# Rutas del proveedor. Se autentican solas con el token de la query y tienen que
# entrar desde fuera; pedirles además la clave rompería las llamadas.
PUBLICAS = ('/twiml/', '/entrante', '/estado/', '/twilio')

HOSTS_LOCALES = ('localhost', '127.0.0.1', '::1', '[::1]')


def es_publica(ruta):
    return any(ruta == p or ruta.startswith(p) for p in PUBLICAS)


def host_local(host):
    """Mira la cabecera `Host`, no la IP de origen.

    La IP no distingue nada: cloudflared se conecta desde la propia máquina, así que
    a través del túnel la petición llega igualmente de 127.0.0.1. Lo que sí cambia es
    el `Host` — `localhost:8600` en el navegador de esta máquina, y el dominio del
    túnel cuando viene de fuera.
    """
    host = (host or '').strip().lower()
    if not host:
        return False
    if host.startswith('['):
        host = host.split(']')[0] + ']'
    else:
        host = host.split(':')[0]
    return host in HOSTS_LOCALES


def _cabecera(scope, nombre):
    nombre = nombre.encode()
    for clave, valor in scope.get('headers') or []:
        if clave == nombre:
            return valor.decode('latin-1')
    return ''


def _clave_de_la_peticion(scope):
    """La clave que trae esta petición, de la query o de la cookie."""
    consulta = parse_qs(scope.get('query_string', b'').decode('latin-1'))
    if consulta.get('k'):
        return consulta['k'][0], True

    cookies = _cabecera(scope, 'cookie')
    for trozo in cookies.split(';'):
        nombre, _, valor = trozo.strip().partition('=')
        if nombre == COOKIE:
            return valor, False
    return '', False


class ControlDeAcceso:
    """Middleware ASGI. Cubre HTTP y WebSocket con la misma regla.

    Se escribe a mano en vez de usar `BaseHTTPMiddleware` porque ese no ve los
    WebSocket, y el WebSocket es justo por donde pasa la conversación de la demo —
    dejarlo fuera sería cerrar la puerta y no la ventana.
    """

    def __init__(self, app, clave_actual):
        self.app = app
        # Se pasa una función y no el valor: la clave se puede cambiar desde el panel
        # y tiene que surtir efecto sin reiniciar.
        self.clave_actual = clave_actual

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)

        ruta = scope.get('path', '')
        if es_publica(ruta):
            return await self.app(scope, receive, send)

        if host_local(_cabecera(scope, 'host')):
            return await self.app(scope, receive, send)

        clave = (self.clave_actual() or '').strip()
        recibida, venia_en_la_url = _clave_de_la_peticion(scope)

        if not clave:
            logger.warning(
                f'Acceso a {ruta} desde fuera rechazado: no hay web.clave_acceso. '
                f'Ponla en el panel para poder abrir la demo desde otro sitio.')
            return await self._rechazar(scope, send, sin_clave=True)

        if not (recibida and hmac.compare_digest(recibida, clave)):
            logger.warning(f'Acceso a {ruta} desde fuera rechazado: clave incorrecta')
            return await self._rechazar(scope, send)

        if venia_en_la_url and scope['type'] == 'http':
            # Se guarda para que el resto —CSS, JS y el WebSocket— entren sin
            # arrastrar la clave en cada URL. `SameSite=Lax` y `Path=/`; no se marca
            # `Secure` a mano porque el túnel ya es HTTPS y marcarlo la rompería si
            # alguien lo publica por HTTP.
            return await self.app(scope, receive, self._con_cookie(send, clave))

        return await self.app(scope, receive, send)

    def _con_cookie(self, send, clave):
        async def enviar(mensaje):
            if mensaje['type'] == 'http.response.start':
                cabeceras = list(mensaje.get('headers') or [])
                cabeceras.append((
                    b'set-cookie',
                    f'{COOKIE}={clave}; Path=/; SameSite=Lax; Max-Age=86400'.encode()))
                mensaje = dict(mensaje, headers=cabeceras)
            await send(mensaje)
        return enviar

    async def _rechazar(self, scope, send, sin_clave=False):
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return

        if sin_clave:
            cuerpo = ('<h1>No disponible desde fuera</h1>'
                      '<p>Este servicio solo se abre desde la maquina donde corre. '
                      'Para poder abrirlo desde otro sitio, pon una clave de acceso '
                      'en el panel (Claves y telefonia) y comparte el enlace que '
                      'aparece alli.</p>')
        else:
            cuerpo = ('<h1>Clave incorrecta</h1>'
                      '<p>El enlace tiene que llevar la clave de acceso al final, '
                      'asi: <code>?k=tu-clave</code>.</p>')

        datos = (f'<!doctype html><html lang="es"><head><meta charset="utf-8">'
                 f'<title>Sin acceso</title><style>body{{font:15px/1.6 system-ui,'
                 f'sans-serif;margin:4rem auto;max-width:34rem;padding:0 1.5rem;'
                 f'color:#1d2126}}h1{{font-size:1.2rem}}code{{background:#eef1f5;'
                 f'padding:.1rem .35rem;border-radius:3px}}</style></head><body>'
                 f'{cuerpo}</body></html>').encode()

        await send({'type': 'http.response.start', 'status': 403,
                    'headers': [(b'content-type', b'text/html; charset=utf-8'),
                                (b'content-length', str(len(datos)).encode())]})
        await send({'type': 'http.response.body', 'body': datos})
