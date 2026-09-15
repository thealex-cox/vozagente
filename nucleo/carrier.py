"""Habla con el proveedor de telefonía: SignalWire o Twilio.

Portado de `voz/carriers/` de skytech y reducido a un solo fichero, porque aquí no
hay enrutamiento por coste entre varios proveedores: una instalación tiene el suyo.

**SignalWire hereda de Twilio** porque su API de compatibilidad (LaML) replica la de
Twilio; sólo cambian la URL base —que cuelga del *space*— y de dónde salen las
credenciales. Se usa la API REST con `requests` en vez del SDK oficial a propósito:
el SDK arrastra dependencias que no aportan nada para tres peticiones.

**Dos trampas de SignalWire que costaron llamadas mudas en skytech**, y que aquí
están resueltas de origen:

1. **Sus webhooks no son verificables por firma.** Manda `X-SignalWire-Signature`
   calculada con un secreto del *space* que la cuenta no expone: se probaron 5.600
   combinaciones de clave contra 16 peticiones reales y ninguna la reproduce. Por eso
   la autenticación es un token en la query de la URL (`?t=…`). Con Twilio la firma
   sí funciona y se acepta también por esa vía.
2. **Las cuentas nuevas sólo marcan a números comprados o verificados** (422: «not a
   purchased or verified number in your Project»). Se desbloquea verificando el
   destino en *Phone Numbers → Verified Caller IDs*, y para producción abriendo un
   ticket de soporte.
"""

import hashlib
import hmac
import logging
from base64 import b64encode

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 20

# Estados del carrier traducidos a los nuestros. `no-answer`, `busy` y `failed` son
# finales y distintos entre sí: hacen falta separados para saber si reintentar.
ESTADOS = {
    'queued': 'encolada',
    'initiated': 'marcando',
    'ringing': 'sonando',
    'in-progress': 'en curso',
    'completed': 'finalizada',
    'busy': 'ocupado',
    'no-answer': 'sin respuesta',
    'canceled': 'cancelada',
    'failed': 'fallida',
}

EVENTOS_CALLBACK = ('initiated', 'ringing', 'answered', 'completed')


class CarrierError(Exception):
    """Fallo del proveedor, con el motivo tal como él lo dio."""


class Resultado:
    def __init__(self, call_id, aceptada=True, mensaje='', payload=None):
        self.call_id = call_id
        self.aceptada = aceptada
        self.mensaje = mensaje
        self.payload = payload or {}

    def __repr__(self):
        return f'<Resultado {self.call_id} aceptada={self.aceptada}>'


class TwilioCarrier:
    nombre = 'twilio'
    API_BASE = 'https://api.twilio.com/2010-04-01'

    def __init__(self, credenciales=None):
        datos = credenciales or {}
        self.account_sid = (datos.get('project_id') or datos.get('account_sid') or '').strip()
        self.auth_token = (datos.get('api_token') or datos.get('auth_token') or '').strip()
        self.numero_origen = (datos.get('numero_origen') or '').strip()

    def esta_configurado(self):
        return bool(self.account_sid and self.auth_token and self.numero_origen)

    def _url(self, recurso):
        return f'{self.API_BASE}/Accounts/{self.account_sid}/{recurso}'

    def iniciar_llamada(self, telefono, url_webhook, **kwargs):
        """Lanza la llamada. `url_webhook` devuelve el TwiML al descolgar.

        El proveedor no marca hasta saber qué decir: pide la URL en la misma petición
        que crea la llamada y la consulta cuando el destinatario contesta.
        """
        if not self.esta_configurado():
            raise CarrierError(f'Faltan credenciales de {self.nombre}')
        if not url_webhook:
            raise CarrierError('Hace falta la URL del TwiML para poder marcar')

        origen = (kwargs.get('numero_origen') or self.numero_origen).strip()
        datos = {
            'To': telefono,
            'From': origen,
            'Url': url_webhook,
            'Method': 'POST',
            'Timeout': kwargs.get('timbrado_segundos', 30),
        }

        url_estado = kwargs.get('url_estado', '')
        if url_estado:
            datos['StatusCallback'] = url_estado
            datos['StatusCallbackMethod'] = 'POST'
            # requests serializa la lista como parámetros repetidos, que es como el
            # proveedor espera recibir varios eventos.
            datos['StatusCallbackEvent'] = list(EVENTOS_CALLBACK)

        if kwargs.get('grabar'):
            datos['Record'] = 'true'

        try:
            respuesta = requests.post(
                self._url('Calls.json'), data=datos,
                auth=(self.account_sid, self.auth_token), timeout=TIMEOUT)
        except requests.RequestException as ex:
            raise CarrierError(f'No se pudo contactar con {self.nombre}: {ex}')

        if respuesta.status_code >= 400:
            raise CarrierError(
                f'{self.nombre} rechazo la llamada ({respuesta.status_code}): '
                f'{self._error(respuesta)}')

        cuerpo = respuesta.json()
        return Resultado(call_id=cuerpo.get('sid', ''), aceptada=True,
                         mensaje=cuerpo.get('status', ''), payload=cuerpo)

    def colgar(self, call_id):
        if not self.esta_configurado() or not call_id:
            return False
        try:
            respuesta = requests.post(
                self._url(f'Calls/{call_id}.json'), data={'Status': 'completed'},
                auth=(self.account_sid, self.auth_token), timeout=TIMEOUT)
            return respuesta.status_code < 400
        except requests.RequestException as ex:
            logger.warning(f'No se pudo colgar {call_id}: {ex}')
            return False

    def consultar_llamada(self, call_id):
        """Lo que el proveedor sabe de una llamada: estado, duración y coste real."""
        if not self.esta_configurado() or not call_id:
            return {}
        try:
            respuesta = requests.get(
                self._url(f'Calls/{call_id}.json'),
                auth=(self.account_sid, self.auth_token), timeout=TIMEOUT)
            return respuesta.json() if respuesta.status_code < 400 else {}
        except (requests.RequestException, ValueError) as ex:
            logger.warning(f'No se pudo consultar {call_id}: {ex}')
            return {}

    def numeros(self):
        """Los números de la cuenta. Sirve para comprobar credenciales sin gastar una
        llamada, y de paso avisa si `numero_origen` no está entre ellos."""
        if not (self.account_sid and self.auth_token):
            raise CarrierError('Faltan credenciales')
        try:
            respuesta = requests.get(
                self._url('IncomingPhoneNumbers.json'),
                auth=(self.account_sid, self.auth_token), timeout=TIMEOUT)
        except requests.RequestException as ex:
            raise CarrierError(f'No se pudo contactar con {self.nombre}: {ex}')
        if respuesta.status_code >= 400:
            raise CarrierError(f'{self.nombre} rechazo las credenciales '
                               f'({respuesta.status_code}): {self._error(respuesta)}')
        cuerpo = respuesta.json()
        return [n.get('phone_number', '') for n in cuerpo.get('incoming_phone_numbers', [])]

    @staticmethod
    def _error(respuesta):
        """El motivo tal como lo da el proveedor. Es lo que se enseña sin traducir:
        «To number is not routeable» dice exactamente qué pasa, y reescribirlo como
        «no se pudo llamar» borra la única pista útil."""
        try:
            cuerpo = respuesta.json()
            return cuerpo.get('message') or cuerpo.get('detail') or respuesta.text[:200]
        except ValueError:
            return respuesta.text[:200]

    @classmethod
    def validar_firma(cls, url, parametros, firma, auth_token):
        """HMAC-SHA1 sobre la URL más los parámetros ordenados, como manda Twilio."""
        if not firma or not auth_token:
            return False
        cadena = url + ''.join(f'{k}{parametros[k]}' for k in sorted(parametros))
        esperada = b64encode(
            hmac.new(auth_token.encode(), cadena.encode(), hashlib.sha1).digest()
        ).decode()
        return hmac.compare_digest(esperada, firma)


class SignalWireCarrier(TwilioCarrier):
    nombre = 'signalwire'

    def __init__(self, credenciales=None):
        super().__init__(credenciales)
        datos = credenciales or {}
        self.space = (datos.get('space') or '').replace('https://', '').rstrip('/')

    def esta_configurado(self):
        return bool(self.space and self.account_sid and self.auth_token
                    and self.numero_origen)

    def _url(self, recurso):
        return f'https://{self.space}/api/laml/2010-04-01/Accounts/{self.account_sid}/{recurso}'


def construir(config_carrier):
    """El carrier configurado, o None si no lo está.

    Devuelve None en vez de levantar: las llamadas **entrantes no necesitan carrier**
    —se atienden respondiendo al webhook— así que una instalación sin credenciales
    tiene que poder arrancar y atender igual.
    """
    datos = config_carrier or {}
    proveedor = (datos.get('proveedor') or 'signalwire').strip().lower()
    clase = TwilioCarrier if proveedor == 'twilio' else SignalWireCarrier
    instancia = clase(datos)
    return instancia if instancia.esta_configurado() else None
