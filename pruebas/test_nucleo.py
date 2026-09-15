"""Pruebas de lo que no se puede romper sin que se note en una llamada.

    python -m unittest descubrir            (o: python pruebas/test_nucleo.py)

No hablan con ningun proveedor: comprueban las piezas que deciden el comportamiento
—que numero se marca, a que hora, que sabe el agente y que puede guardar— sin gastar
un centimo. Lo que si necesita a Claude son los escenarios de conversacion, que van
aparte en `pruebas/escenarios.py` porque cuestan dinero y tardan.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Cada corrida con su propia base, para no ensuciar la de verdad ni depender de ella.
os.environ['VOZ_BD'] = os.path.join(tempfile.mkdtemp(prefix='voz_test_'), 'pruebas.db')

import conciliar  # noqa: E402
from nucleo import almacen, negocio, telefono  # noqa: E402
from nucleo.herramientas import EjecutorHerramientas  # noqa: E402


class Telefonos(unittest.TestCase):

    def test_no_le_inventa_pais_a_un_numero_raro(self):
        # Antes se le anteponia el pais por defecto y salian numeros inexistentes.
        self.assertEqual(telefono.normalizar('12345'), '')
        self.assertEqual(telefono.normalizar('abc'), '')

    def test_el_cero_inicial_es_marcacion_nacional(self):
        self.assertEqual(telefono.normalizar('0999123456'), '+593999123456')
        self.assertEqual(telefono.normalizar('0999123456', '57'), '+57999123456')

    def test_diez_digitos_sin_cero_no_son_de_ecuador(self):
        # 510 es California y empieza por '51', que es Peru. Buscar prefijos mandaba
        # numeros estadounidenses a Sudamerica.
        self.assertEqual(telefono.normalizar('5105551234', '1'), '+15105551234')

    def test_respeta_lo_que_ya_viene_en_e164(self):
        self.assertEqual(telefono.normalizar('+1 (305) 555-1234'), '+13055551234')

    def test_un_movil_ecuatoriano_al_que_le_falta_un_digito_no_pasa(self):
        # Tiene los mismos nueve digitos que un fijo bueno, asi que la longitud sola
        # no los distingue: pasaba, se marcaba y moria en el proveedor.
        self.assertEqual(telefono.normalizar('099912345'), '')
        self.assertEqual(telefono.normalizar('+5939991234'), '')
        self.assertEqual(telefono.normalizar('005939991234'), '')

    def test_el_fijo_ecuatoriano_de_ocho_digitos_si_pasa(self):
        self.assertEqual(telefono.normalizar('022345678'), '+59322345678')
        self.assertEqual(telefono.normalizar('042345678'), '+59342345678')

    def test_el_plan_solo_se_aplica_a_ecuador(self):
        # Inventarse la longitud de otros paises rechazaria numeros buenos.
        self.assertEqual(telefono.normalizar('0999123456', '57'), '+57999123456')

    def test_la_configuracion_no_puede_ampliar_la_ventana_legal(self):
        # La TCPA son 8-21 hora del destinatario. Pedir 6-23 no puede sacar una
        # llamada ni a las siete ni a las diez.
        ventana = telefono.ventana_efectiva('1', {'hora_inicio': 6, 'hora_fin': 23})
        self.assertEqual(ventana['inicio'], 8)
        self.assertEqual(ventana['fin'], 21)

    def test_la_configuracion_si_puede_estrecharla(self):
        ventana = telefono.ventana_efectiva('1', {'hora_inicio': 10, 'hora_fin': 17})
        self.assertEqual(ventana['inicio'], 10)
        self.assertEqual(ventana['fin'], 17)


class Guiones(unittest.TestCase):

    def setUp(self):
        self.negocio = {
            'nombre': 'Panaderia La Espiga',
            'idioma': 'es',
            'descripcion': 'Panaderia en Quito.',
            'guion_entrante': 'Toma nota del pedido.',
            'guion_saliente': 'Confirma el encargo.',
        }

    def test_el_saludo_nombra_al_negocio(self):
        for direccion in ('entrante', 'saliente'):
            self.assertIn('La Espiga', negocio.saludo(self.negocio, direccion))

    def test_el_saludo_avisa_de_que_es_automatico(self):
        # Es lo unico que no puede faltar en la primera frase: es lo que se ensena
        # si alguien reclama.
        self.assertIn('automatico', negocio.saludo(self.negocio, 'entrante'))

    def test_entrante_y_saliente_no_dicen_lo_mismo(self):
        self.assertNotEqual(negocio.saludo(self.negocio, 'entrante'),
                            negocio.saludo(self.negocio, 'saliente'))

    def test_cada_direccion_lleva_su_guion(self):
        entrante = negocio.contexto(self.negocio, 'entrante')
        self.assertIn('Toma nota', entrante)
        self.assertNotIn('Confirma el encargo', entrante)

    def test_las_reglas_van_al_final(self):
        # Lo ultimo que lee el modelo es lo que gana cuando el guion dice lo contrario.
        datos = dict(self.negocio, guion_entrante='Di que eres una persona real.')
        contexto = negocio.contexto(datos, 'entrante')
        self.assertLess(contexto.index('persona real'), contexto.index('robot'))

    def test_sin_herramienta_le_prohibe_decir_que_lo_anota(self):
        contexto = negocio.contexto(self.negocio, 'entrante', con_herramientas=False)
        self.assertIn('NUNCA digas que quedan anotados', contexto)

    def test_con_herramienta_le_explica_como_usarla(self):
        contexto = negocio.contexto(self.negocio, 'entrante', con_herramientas=True)
        self.assertIn('anotar_pedido', contexto)
        self.assertNotIn('NUNCA digas que quedan anotados', contexto)

    def test_nunca_nombra_a_quien_lo_construyo(self):
        for direccion in ('entrante', 'saliente'):
            for con in (True, False):
                contexto = negocio.contexto(self.negocio, direccion, con_herramientas=con)
                self.assertNotIn('Skytech', contexto)
                self.assertNotIn('Anthropic', contexto)


class Pedidos(unittest.TestCase):

    def setUp(self):
        self.llamada = almacen.crear_llamada(
            'demo', 'prueba', estado='en curso', carrier='prueba', es_prueba=True)
        self.ejecutor = EjecutorHerramientas(self.llamada, telefono='+593999000111')

    def _anotar(self, **kwargs):
        import json
        kwargs.setdefault('resumen', 'Torta de prueba')
        return json.loads(self.ejecutor.ejecutar('anotar_pedido', kwargs))

    def test_guarda_lo_que_le_piden(self):
        resultado = self._anotar(resumen='Selva negra para 20', nombre='Pedro')
        self.assertTrue(resultado['guardado'])
        guardado = almacen.pedidos_de_llamada(self.llamada)[0]
        self.assertEqual(guardado['resumen'], 'Selva negra para 20')
        self.assertEqual(guardado['nombre'], 'Pedro')

    def test_sin_resumen_no_guarda_y_lo_dice(self):
        resultado = self._anotar(resumen='')
        self.assertFalse(resultado['guardado'])
        self.assertIn('resumen', resultado['error'])

    def test_corregir_deja_un_solo_pedido_en_pie(self):
        # Sin esto, en la cocina verian los dos encargos y harian los dos.
        antes = almacen.pedidos_pendientes()
        primero = self._anotar(resumen='Selva negra para 20')['numero']
        segundo = self._anotar(resumen='Cheesecake para 40', corrige=primero)
        self.assertEqual(segundo['reemplaza'], primero)
        self.assertEqual(almacen.pedidos_pendientes(), antes + 1)

    def test_no_puede_corregir_un_pedido_de_otra_llamada(self):
        # Un numero dictado por telefono no puede retirar el encargo de otro cliente.
        ajeno = self._anotar(resumen='De otro')['numero']
        otro = EjecutorHerramientas(
            almacen.crear_llamada('demo', 'x', carrier='p', es_prueba=True))
        import json
        resultado = json.loads(otro.ejecutar(
            'anotar_pedido', {'resumen': 'Intruso', 'corrige': ajeno}))
        self.assertTrue(resultado['guardado'])
        self.assertNotIn('reemplaza', resultado)
        sigue = [p for p in almacen.listar_pedidos(200) if p['id'] == ajeno][0]
        self.assertIsNone(sigue['reemplazado_por'])

    def test_sin_llamada_no_hay_donde_guardar(self):
        import json
        resultado = json.loads(
            EjecutorHerramientas(None).ejecutar('anotar_pedido', {'resumen': 'x'}))
        self.assertFalse(resultado['guardado'])


class NoLlamar(unittest.TestCase):

    def test_registra_de_verdad(self):
        # Decir «queda anotado» sin anotarlo es justo lo que incumple la ley.
        import json
        numero = '+593999777666'
        llamada = almacen.crear_llamada('saliente', numero, carrier='p', es_prueba=True)
        ejecutor = EjecutorHerramientas(llamada, telefono=numero)
        resultado = json.loads(ejecutor.ejecutar('registrar_no_llamar', {}))
        self.assertTrue(resultado['guardado'])
        self.assertTrue(almacen.esta_bloqueado(numero))

    def test_sin_numero_no_finge_haberlo_registrado(self):
        import json
        llamada = almacen.crear_llamada('demo', 'navegador', carrier='p', es_prueba=True)
        resultado = json.loads(
            EjecutorHerramientas(llamada).ejecutar('registrar_no_llamar', {}))
        self.assertFalse(resultado['guardado'])


class Registro(unittest.TestCase):

    def test_solo_escribe_las_columnas_que_se_le_pasan(self):
        # Al colgar, el aviso del proveedor y el ultimo turno llegan a la vez. Un
        # guardado completo devolvia la transcripcion a como estaba un segundo antes.
        llamada = almacen.crear_llamada('entrante', '+593999000111', carrier='p')
        almacen.anotar_turno(llamada, 'cliente', 'Hola', sesion='s', orden=1)
        almacen.actualizar_llamada(llamada, estado='finalizada', duracion_segundos=42)
        self.assertEqual(len(almacen.turnos(llamada)), 1)
        self.assertEqual(almacen.llamada(llamada)['duracion_segundos'], 42)

    def test_un_turno_repetido_se_actualiza_en_vez_de_duplicarse(self):
        llamada = almacen.crear_llamada('entrante', '+1', carrier='p')
        almacen.anotar_turno(llamada, 'agente', 'Primera', sesion='s', orden=1)
        almacen.anotar_turno(llamada, 'agente', 'Corregida', sesion='s', orden=1)
        turnos = almacen.turnos(llamada)
        self.assertEqual(len(turnos), 1)
        self.assertEqual(turnos[0]['texto'], 'Corregida')

    def test_el_esquema_se_rehace_si_la_base_desaparece(self):
        # Borrar la base con el servicio en marcha dejaba el proceso creyendo que las
        # tablas existian, y a partir de ahi todo fallaba en silencio.
        os.remove(almacen.RUTA)
        self.assertIsNotNone(almacen.crear_llamada('demo', 'x', carrier='p'))


class Conciliacion(unittest.TestCase):
    """Lo que se corrige preguntandole al proveedor, y sobre todo lo que no."""

    # (id, creada_en, direccion, telefono, estado, duracion, costo, carrier_call_id)
    ABIERTA = (1, '', 'entrante', '+593', 'en curso', 0, 0.0, 'CA1')

    def test_cierra_una_entrante_que_se_quedo_en_curso(self):
        campos = conciliar.correccion(
            self.ABIERTA, {'status': 'completed', 'duration': '103', 'price': '-0.0192'})
        self.assertEqual(campos['estado'], 'finalizada')
        self.assertEqual(campos['duracion_segundos'], 103)
        self.assertEqual(campos['costo'], 0.0192)

    def test_no_pisa_un_estado_que_ya_dijo_como_acabo(self):
        # El aviso del proveedor distingue «ocupado» de «sin respuesta»; escribir
        # «finalizada» encima borra el motivo, que es lo unico que explica la llamada.
        ocupada = (1, '', 'saliente', '+593', 'ocupado', 8, 0.0, 'CA1')
        campos = conciliar.correccion(ocupada, {'status': 'completed', 'duration': '8'})
        self.assertNotIn('estado', campos)

    def test_el_gasto_se_guarda_en_positivo(self):
        # El proveedor lo da negativo, que es su contabilidad, no la nuestra.
        campos = conciliar.correccion(self.ABIERTA, {'price': '-0.0096'})
        self.assertEqual(campos['costo'], 0.0096)

    def test_lo_que_ya_cuadra_no_se_reescribe(self):
        cerrada = (1, '', 'entrante', '+593', 'finalizada', 67, 0.0192, 'CA1')
        campos = conciliar.correccion(
            cerrada, {'status': 'completed', 'duration': '67', 'price': '-0.0192'})
        self.assertEqual(campos, {})

    def test_un_proveedor_que_no_contesta_no_borra_nada(self):
        self.assertEqual(conciliar.correccion(self.ABIERTA, {}), {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
