(function () {
  'use strict';

  // Cada campo de la pantalla, atado a su sitio dentro de config.json. Se declara
  // una vez y sirve para leer y para guardar: con dos listas separadas, añadir un
  // campo y olvidarse de la otra deja un ajuste que se ve y no se guarda.
  var CAMPOS = [
    ['negocio-nombre', 'negocio', 'nombre'],
    ['negocio-idioma', 'negocio', 'idioma'],
    ['negocio-descripcion', 'negocio', 'descripcion'],
    ['negocio-vocabulario', 'negocio', 'vocabulario'],
    ['saludo-entrante', 'negocio', 'saludo_entrante'],
    ['guion-entrante', 'negocio', 'guion_entrante'],
    ['saludo-saliente', 'negocio', 'saludo_saliente'],
    ['guion-saliente', 'negocio', 'guion_saliente'],
    ['ia-anthropic', 'ia', 'anthropic_api_key'],
    ['ia-modelo', 'ia', 'modelo'],
    ['ia-deepgram', 'ia', 'deepgram_api_key'],
    ['ia-cartesia', 'ia', 'cartesia_api_key'],
    ['ia-voz-es', 'ia', 'cartesia_voz_es'],
    ['ia-voz-en', 'ia', 'cartesia_voz_en'],
    ['carrier-proveedor', 'carrier', 'proveedor'],
    ['carrier-space', 'carrier', 'space'],
    ['carrier-project', 'carrier', 'project_id'],
    ['carrier-token', 'carrier', 'api_token'],
    ['carrier-entrante', 'carrier', 'numero_entrante'],
    ['carrier-numero', 'carrier', 'numero_origen'],
    ['web-publica', 'web', 'url_publica'],
    ['web-stream', 'web', 'url_stream'],
    ['web-token', 'web', 'token'],
    ['web-clave', 'web', 'clave_acceso'],
    ['llamadas-inicio', 'llamadas', 'hora_inicio'],
    ['llamadas-fin', 'llamadas', 'hora_fin']
  ];

  var NUMERICOS = ['llamadas-inicio', 'llamadas-fin'];

  var guardarBtn = document.getElementById('guardar');
  var descartarBtn = document.getElementById('descartar');
  var pie = document.getElementById('pie');
  var pieEstado = document.getElementById('pie-estado');
  var avisos = document.getElementById('avisos');
  var estadoServicio = document.getElementById('estado-servicio');
  var cabeceraNumero = document.getElementById('cabecera-numero');
  var listado = document.getElementById('listado-llamadas');

  // Lo que había en los campos la última vez que se cargó o se guardó. Es contra
  // esto contra lo que se decide si hay cambios sin guardar.
  var guardado = {};

  function texto(valor) {
    return valor === null || valor === undefined ? '' : String(valor);
  }

  function escapar(s) {
    var d = document.createElement('div');
    d.textContent = texto(s);
    return d.innerHTML;
  }

  /** Un aviso que se puede cerrar, y que no se lleva por delante al anterior.
   *
   * Antes era un hueco fijo en el pie: el mensaje nuevo pisaba al viejo y todos
   * desaparecían solos a los seis segundos, así que un error que llegara mientras
   * mirabas otra parte de la pantalla se perdía sin remedio. Ahora se apilan, cada
   * uno lleva su aspa, y **los errores no se van solos**: un fallo que se borra
   * antes de que lo leas es un fallo que no ha avisado de nada.
   */
  function avisar(mensaje, esError) {
    if (!mensaje) { return null; }

    var caja = document.createElement('div');
    caja.className = 'aviso';
    caja.dataset.tipo = esError ? 'error' : 'ok';
    caja.setAttribute('role', esError ? 'alert' : 'status');

    var cuerpo = document.createElement('span');
    cuerpo.className = 'aviso-texto';
    cuerpo.textContent = mensaje;

    var cerrar = document.createElement('button');
    cerrar.type = 'button';
    cerrar.className = 'aviso-cerrar';
    cerrar.title = 'Cerrar';
    cerrar.setAttribute('aria-label', 'Cerrar aviso');
    cerrar.innerHTML = '&times;';

    var ido = false;
    function quitar() {
      if (ido) { return; }
      ido = true;
      caja.classList.remove('abierto');
      setTimeout(function () {
        if (caja.parentNode) { caja.parentNode.removeChild(caja); }
      }, 200);
    }
    cerrar.addEventListener('click', quitar);

    caja.appendChild(cuerpo);
    caja.appendChild(cerrar);
    avisos.appendChild(caja);
    void caja.offsetWidth;
    caja.classList.add('abierto');

    // Los buenos se van solos porque son confirmaciones y estorban; los errores se
    // quedan hasta que alguien los cierra.
    if (!esError) { setTimeout(quitar, 6000); }
    return quitar;
  }

  // ---- cambios sin guardar -------------------------------------------------
  //
  // El botón de guardar vivía al final de la página y había que bajar hasta él sin
  // saber si quedaba algo por guardar. Ahora la barra va fija abajo y sólo se
  // enciende cuando de verdad hay algo distinto de lo último que se cargó: un botón
  // siempre disponible no dice nada, y uno que se enciende sí.

  function instantanea() {
    var foto = {};
    CAMPOS.forEach(function (campo) {
      var el = document.getElementById(campo[0]);
      if (el) { foto[campo[0]] = el.value; }
    });
    return foto;
  }

  function hayCambios() {
    var ahora = instantanea();
    return Object.keys(ahora).some(function (k) { return ahora[k] !== guardado[k]; });
  }

  function refrescarPie() {
    var sucio = hayCambios();
    pie.dataset.sucio = sucio ? 'si' : 'no';
    guardarBtn.disabled = !sucio;
    descartarBtn.hidden = !sucio;
    pieEstado.textContent = sucio
      ? 'Hay cambios sin guardar'
      : 'Todo guardado';
  }

  function fijarGuardado() {
    guardado = instantanea();
    refrescarPie();
  }

  function vigilarCampos() {
    CAMPOS.forEach(function (campo) {
      var el = document.getElementById(campo[0]);
      if (!el) { return; }
      el.addEventListener('input', refrescarPie);
      el.addEventListener('change', refrescarPie);
    });
  }

  descartarBtn.addEventListener('click', function () {
    CAMPOS.forEach(function (campo) {
      var el = document.getElementById(campo[0]);
      if (el && campo[0] in guardado) { el.value = guardado[campo[0]]; }
    });
    refrescarPie();
    avisar('Se han descartado los cambios.');
  });

  // Cerrar la pestaña con la mitad de un guion escrito y perderlo es el tipo de
  // fallo que no deja rastro y nadie sabe explicar después.
  window.addEventListener('beforeunload', function (ev) {
    if (!hayCambios()) { return; }
    ev.preventDefault();
    ev.returnValue = '';
  });

  // ---- campos secretos: ver, copiar, pegar --------------------------------

  var ICONOS = {
    ver: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5c-5 0-9 4.5-10 7 1 2.5 5 7 10 7s9-4.5 10-7c-1-2.5-5-7-10-7zm0 12a5 5 0 110-10 5 5 0 010 10zm0-2.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5z"/></svg>',
    ocultar: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5c-5 0-9 4.5-10 7 .6 1.4 2.3 3.9 4.9 5.5L4 20.4 5.4 21.8l16-16L20 4.4l-2.6 2.6C15.8 5.7 14 5 12 5zm0 4a3 3 0 012.8 4.1l-3.9 3.9A3 3 0 0112 9zm7.4-.6l-2.2 2.2c.2.4.3.9.3 1.4a5 5 0 01-5 5c-.5 0-1-.1-1.4-.3l-1.7 1.7c.8.2 1.7.3 2.6.3 5 0 9-4.5 10-7-.5-1.2-1.7-2.5-2.6-3.3z"/></svg>',
    copiar: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 1H4a2 2 0 00-2 2v14h2V3h12V1zm3 4H8a2 2 0 00-2 2v14a2 2 0 002 2h11a2 2 0 002-2V7a2 2 0 00-2-2zm0 16H8V7h11v14z"/></svg>',
    pegar: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 2h-4.2A3 3 0 0012 0a3 3 0 00-2.8 2H5a2 2 0 00-2 2v16a2 2 0 002 2h14a2 2 0 002-2V4a2 2 0 00-2-2zm-7 0a1 1 0 110 2 1 1 0 010-2zm7 18H5V4h2v3h10V4h2v16z"/></svg>',
    ok: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 16.2L4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z"/></svg>'
  };

  function boton(clase, titulo, icono) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'icono ' + clase;
    b.title = titulo;
    b.setAttribute('aria-label', titulo);
    b.innerHTML = icono;
    return b;
  }

  function confirmarEnBoton(b, iconoOriginal) {
    // Un tic durante un segundo. Sin esta señal, copiar no produce ningun cambio
    // visible y no hay forma de saber si funciono salvo pegando en otro sitio.
    b.innerHTML = ICONOS.ok;
    b.dataset.ok = 'si';
    setTimeout(function () {
      b.innerHTML = iconoOriginal;
      b.dataset.ok = 'no';
    }, 1100);
  }

  function prepararSecretos() {
    var filas = document.querySelectorAll('.fila-secreto');
    Array.prototype.forEach.call(filas, function (fila) {
      var input = fila.querySelector('input');

      // Un enlace para compartir no es un secreto que ocultar, y sale ya visible.
      var esEnlace = input.readOnly;
      if (esEnlace) { input.type = 'text'; }

      var bVer = boton('ver', 'Mostrar', ICONOS.ver);
      bVer.addEventListener('click', function () {
        var oculto = input.type === 'password';
        input.type = oculto ? 'text' : 'password';
        bVer.innerHTML = oculto ? ICONOS.ocultar : ICONOS.ver;
        bVer.title = oculto ? 'Ocultar' : 'Mostrar';
        bVer.setAttribute('aria-label', bVer.title);
      });

      var bCopiar = boton('copiar', 'Copiar', ICONOS.copiar);
      bCopiar.addEventListener('click', function () {
        if (!input.value) { avisar('Ese campo está vacío, no hay nada que copiar.', true); return; }
        copiar(input.value).then(function () {
          confirmarEnBoton(bCopiar, ICONOS.copiar);
        }).catch(function () {
          avisar('El navegador no dejó copiar. Selecciona el texto y usa Ctrl+C.', true);
        });
      });

      var bPegar = boton('pegar', 'Pegar', ICONOS.pegar);
      bPegar.addEventListener('click', function () {
        pegar().then(function (valor) {
          if (!valor) {
            avisar('No hay nada en el portapapeles.', true);
            return;
          }
          // Se limpia al pegar: copiar una clave de una web arrastra espacios y
          // saltos de linea invisibles, y una clave con un salto detras falla con
          // un error de autenticacion que no dice nada de eso.
          input.value = valor.trim();
          confirmarEnBoton(bPegar, ICONOS.pegar);
        }).catch(function () {
          input.focus();
          avisar('El navegador no dejó leer el portapapeles. Pega con Ctrl+V.', true);
        });
      });

      if (!esEnlace) { fila.appendChild(bVer); }
      fila.appendChild(bCopiar);
      if (!esEnlace) { fila.appendChild(bPegar); }
    });
  }

  function copiar(valor) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(valor);
    }
    return Promise.reject(new Error('sin portapapeles'));
  }

  function pegar() {
    if (navigator.clipboard && navigator.clipboard.readText) {
      return navigator.clipboard.readText();
    }
    return Promise.reject(new Error('sin portapapeles'));
  }

  // ---- configuracion ------------------------------------------------------

  function pintarConfig(datos) {
    var config = datos.config || {};
    CAMPOS.forEach(function (campo) {
      var el = document.getElementById(campo[0]);
      if (!el) { return; }
      var seccion = config[campo[1]] || {};
      var valor = seccion[campo[2]];
      if (Array.isArray(valor)) { valor = valor.join(', '); }
      el.value = texto(valor);
    });

    var titulo = (config.negocio || {}).nombre || 'Panel';
    document.getElementById('titulo').textContent = titulo;
    document.title = titulo + ' · Panel';

    var ruta = document.getElementById('ruta-config');
    if (ruta) { ruta.textContent = datos.ruta || 'config.json'; }

    // El número del negocio, en la cabecera y en todas las pestañas: es el que se
    // dicta en voz alta y el que dice si este panel es el del número que crees.
    var numero = ((config.carrier || {}).numero_entrante || '').trim();
    cabeceraNumero.textContent = numero ? 'Atiende en ' + numero : '';
    cabeceraNumero.hidden = !numero;

    pintarEnlaces(datos.enlaces || {});

    pintarEstado(datos.bloqueos || [], datos.telefonia || [],
                 datos.avisos || []);

    // Lo recien cargado es, por definicion, lo que hay guardado.
    fijarGuardado();
  }

  function pintarEnlaces(enlaces) {
    var caja = document.getElementById('enlaces-compartir');
    if (!enlaces.demo) {
      // Sin URL publica no hay tunel, y un enlace a localhost no abre nada en el
      // ordenador de otro. Mejor no ensenar ninguno que ensenar uno que no sirve.
      caja.hidden = true;
      return;
    }
    document.getElementById('enlace-demo').value = enlaces.demo;
    document.getElementById('enlace-panel').value = enlaces.panel;
    caja.hidden = false;
  }

  function pintarEstado(bloqueos, telefonia, avisos) {
    // Tres niveles, no dos. Faltar el tunel no es un fallo: es el estado normal
    // mientras se ensena por navegador, y pintarlo en rojo hace buscar un problema
    // que no existe justo la manana en que menos tiempo hay.
    if (bloqueos.length) {
      estadoServicio.dataset.nivel = 'bloqueado';
      estadoServicio.innerHTML = '<strong>El agente no puede ni hablar.</strong> ' +
        bloqueos.map(escapar).join(' · ');
    } else if (telefonia.length) {
      estadoServicio.dataset.nivel = 'aviso';
      estadoServicio.innerHTML = '<strong>Por navegador funciona; por teléfono no.</strong> ' +
        telefonia.map(escapar).join(' · ');
    } else if (avisos.length) {
      estadoServicio.dataset.nivel = 'aviso';
      estadoServicio.innerHTML = '<strong>Funciona, con reparos.</strong> ' +
        avisos.map(escapar).join(' · ');
    } else {
      estadoServicio.dataset.nivel = 'ok';
      estadoServicio.textContent = 'Nada bloquea una llamada.';
    }
  }

  function recogerConfig() {
    var salida = {};
    CAMPOS.forEach(function (campo) {
      var el = document.getElementById(campo[0]);
      if (!el) { return; }
      salida[campo[1]] = salida[campo[1]] || {};
      var valor = el.value;
      if (NUMERICOS.indexOf(campo[0]) !== -1) {
        valor = valor === '' ? null : parseInt(valor, 10);
        if (valor !== null && isNaN(valor)) { valor = null; }
      } else if (campo[2] === 'vocabulario') {
        valor = valor.split(',').map(function (t) { return t.trim(); })
          .filter(function (t) { return t; });
      } else {
        valor = valor.trim();
      }
      if (valor !== null) { salida[campo[1]][campo[2]] = valor; }
    });
    return salida;
  }

  function cargarConfig() {
    return fetch('/api/config').then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { throw new Error(d.mensaje || 'error'); }
        pintarConfig(d);
      })
      .catch(function (ex) {
        estadoServicio.dataset.nivel = 'bloqueado';
        estadoServicio.textContent = 'No se pudo leer la configuración: ' + ex.message;
      });
  }

  guardarBtn.addEventListener('click', function () {
    guardarBtn.disabled = true;
    avisar('Guardando…');
    fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(recogerConfig())
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) {
          refrescarPie();
          avisar(d.mensaje || 'No se pudo guardar.', true);
          return;
        }
        pintarEstado(d.bloqueos || [], d.telefonia || [], d.avisos || []);
        fijarGuardado();
        avisar('Guardado. Los guiones y las claves ya están activos; el idioma necesita reiniciar.');
      })
      .catch(function (ex) {
        refrescarPie();
        avisar('No se pudo guardar: ' + ex.message, true);
      });
  });

  // ---- llamadas -----------------------------------------------------------

  function pintarLlamadas(filas) {
    if (!filas.length) {
      listado.innerHTML = '<p class="vacio">Todavía no hay ninguna llamada.</p>';
      return;
    }
    var html = '<table><thead><tr>' +
      '<th>#</th><th>Cuándo</th><th>Dirección</th><th>Número</th>' +
      '<th>Estado</th><th class="num">Turnos</th><th class="num">Duración</th>' +
      '</tr></thead><tbody>';
    filas.forEach(function (l) {
      html += '<tr data-id="' + l.id + '">' +
        '<td>' + l.id + '</td>' +
        '<td>' + escapar((l.creada_en || '').replace('T', ' ')) + '</td>' +
        '<td>' + escapar(l.direccion) + '</td>' +
        '<td>' + escapar(l.telefono) + '</td>' +
        '<td><span class="etiqueta" data-estado="' + escapar(l.estado) + '">' +
          escapar(l.estado) + '</span></td>' +
        '<td class="num">' + l.turnos + '</td>' +
        '<td class="num">' + (l.duracion_segundos || 0) + ' s</td>' +
        '</tr>';
    });
    listado.innerHTML = html + '</tbody></table>';

    Array.prototype.forEach.call(listado.querySelectorAll('tr[data-id]'), function (fila) {
      fila.addEventListener('click', function () { abrirLlamada(fila.dataset.id); });
    });
  }

  function cargarLlamadas() {
    return fetch('/api/llamadas').then(function (r) { return r.json(); })
      .then(function (d) { pintarLlamadas(d.llamadas || []); })
      .catch(function () {
        listado.innerHTML = '<p class="vacio">No se pudieron leer las llamadas.</p>';
      });
  }

  /** Los turnos de una conversación, en el formato que comparten el modal de una
   *  llamada y el de un pedido. */
  function pintarTurnos(turnos, conMetricas) {
    if (!turnos.length) {
      return '<p class="vacio">Sin conversación registrada.</p>';
    }
    var html = '';
    turnos.forEach(function (t) {
      var quien = t.rol === 'agente' ? 'Agente' : 'Cliente';
      html += '<p class="turno" data-rol="' + escapar(t.rol) + '"><strong>' +
        quien + ':</strong> ' + escapar(t.texto) +
        (t.interrumpido ? ' <em>(interrumpido)</em>' : '') +
        (conMetricas && t.latencia_ms
          ? ' <span class="ms">' + t.latencia_ms + ' ms</span>' : '') +
        '</p>';
    });
    return html;
  }

  // Antes la conversación se desplegaba debajo de la tabla y empujaba el contenido,
  // asi que había que hacer scroll para leerla y volver a subir para elegir otra.
  // El modal la pone encima, se cierra con Esc y deja la tabla donde estaba.
  function abrirLlamada(id) {
    mostrarModal();
    modalTitulo.textContent = 'Llamada #' + id;
    modalCuerpo.innerHTML = esqueleto();

    fetch('/api/llamadas/' + id)
      .then(function (r) {
        if (!r.ok && r.status !== 404) { throw new Error('HTTP ' + r.status); }
        return r.json();
      })
      .then(function (d) {
        if (d.error || d.detail) {
          modalCuerpo.innerHTML = '<p class="vacio">' +
            escapar(d.mensaje || d.detail || 'No se pudo leer la llamada.') + '</p>';
          return;
        }
        var l = d.llamada || {};
        var html = '<dl class="ficha">' +
          '<dt>Cuándo</dt><dd>' +
            escapar((l.creada_en || '').replace('T', ' ')) + '</dd>' +
          '<dt>Dirección</dt><dd>' + escapar(l.direccion) + '</dd>' +
          '<dt>Teléfono</dt><dd>' + (escapar(l.telefono) || '—') + '</dd>' +
          '<dt>Estado</dt><dd>' + escapar(l.estado) + '</dd>' +
          '<dt>Duración</dt><dd>' + (l.duracion_segundos || 0) + ' s</dd>' +
          '</dl>';
        html += '<h4>Conversación</h4><div class="conversacion-modal">' +
          pintarTurnos(d.turnos || [], true) + '</div>';
        if (d.eventos && d.eventos.length) {
          html += '<h4>Eventos</h4><ul class="eventos">';
          d.eventos.forEach(function (e) {
            html += '<li>' + escapar((e.creado_en || '').replace('T', ' ')) + ' — ' +
              escapar(e.tipo) + ': ' + escapar(e.detalle) + '</li>';
          });
          html += '</ul>';
        }
        modalCuerpo.innerHTML = html;
      })
      .catch(function (ex) {
        modalCuerpo.innerHTML = '<p class="vacio">No se pudo leer la llamada (' +
          escapar(ex.message) + ').</p>';
      });
  }

  document.getElementById('marcar').addEventListener('click', function () {
    var campo = document.getElementById('marcar-numero');
    var numero = campo.value.trim();
    if (!numero) { avisar('Escribe un número.', true); return; }
    if (!window.confirm('Se va a llamar a ' + numero + ' ahora mismo. ¿Seguro?')) { return; }

    var btn = this;
    btn.disabled = true;
    avisar('Marcando…');
    fetch('/api/llamar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ telefono: numero })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        btn.disabled = false;
        if (d.error) { avisar(d.mensaje, true); return; }
        avisar('Sonando. Llamada ' + d.id + '.' + (d.aviso ? ' Aviso: ' + d.aviso : ''));
        cargarLlamadas();
      })
      .catch(function (ex) {
        btn.disabled = false;
        avisar('No se pudo marcar: ' + ex.message, true);
      });
  });

  // ---- pestanas -----------------------------------------------------------

  Array.prototype.forEach.call(document.querySelectorAll('.pestana'), function (p) {
    p.addEventListener('click', function () {
      Array.prototype.forEach.call(document.querySelectorAll('.pestana'), function (o) {
        o.classList.remove('activa');
      });
      Array.prototype.forEach.call(document.querySelectorAll('.panel'), function (o) {
        o.classList.remove('activa');
      });
      p.classList.add('activa');
      document.getElementById(p.dataset.panel).classList.add('activa');
      if (p.dataset.panel === 'p-llamadas') { cargarLlamadas(); }
      // Al entrar en Pedidos se refresca ya, sin esperar a la siguiente vuelta del
      // sondeo: quien cambia de pestaña quiere ver lo de ahora, no lo de hace tres
      // segundos.
      if (p.dataset.panel === 'p-pedidos') { cargarPedidos(); }
    });
  });

  // ---- pedidos ------------------------------------------------------------

  var listadoPedidos = document.getElementById('listado-pedidos');
  var contadorPedidos = document.getElementById('contador-pedidos');

  function pintarPedidos(filas, pendientes) {
    contadorPedidos.textContent = pendientes || '';
    contadorPedidos.hidden = !pendientes;

    if (!filas.length) {
      listadoPedidos.innerHTML = '<p class="vacio">Todavía no ha apuntado nada. ' +
        'Pídele algo por la demo de voz y aparecerá aquí.</p>';
      contadorPedidos.hidden = true;
      return;
    }

    var html = '<table><thead><tr>' +
      '<th>#</th><th>Cuándo</th><th>Qué pide</th><th>Para</th><th>Cuándo lo quiere</th>' +
      '<th>Llamada</th><th></th></tr></thead><tbody>';
    filas.forEach(function (p) {
      // Un pedido que el cliente cambio no es lo mismo que uno ya atendido: no se
      // reabre, se quedo obsoleto solo. Ofrecer «Reabrir» ahi devolveria a la cocina
      // un encargo que el cliente anulo en la misma llamada.
      var cambiado = !!p.reemplazado_por;
      var acciones = cambiado
        ? '<span class="detalle">cambiado por #' + p.reemplazado_por + '</span>'
        : '<button type="button" class="mini" data-id="' + p.id +
          '" data-atendido="' + (p.atendido ? '1' : '0') + '">' +
          (p.atendido ? 'Reabrir' : 'Atendido') + '</button>';

      html += '<tr data-atendido="' + (p.atendido ? 'si' : 'no') +
        '" data-pedido="' + p.id + '" title="Ver el detalle y la conversación">' +
        '<td>' + p.id + '</td>' +
        '<td>' + escapar((p.creado_en || '').replace('T', ' ')) + '</td>' +
        '<td><strong>' + escapar(p.resumen) + '</strong>' +
        (p.detalles ? '<br><span class="detalle">' + escapar(p.detalles) + '</span>' : '') +
        '</td>' +
        '<td>' + escapar(p.nombre) + '</td>' +
        '<td>' + escapar(p.cuando_texto) + '</td>' +
        '<td>' + (p.llamada_id || '') + '</td>' +
        '<td class="num">' + acciones + '</td>' +
        '</tr>';
    });
    listadoPedidos.innerHTML = html + '</tbody></table>';

    Array.prototype.forEach.call(listadoPedidos.querySelectorAll('button.mini'),
      function (b) {
        b.addEventListener('click', function () {
          fetch('/api/pedidos/' + b.dataset.id, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ atendido: b.dataset.atendido !== '1' })
          }).then(function () { cargarPedidos(); });
        });
      });
  }

  // ---- llamada en curso, en vivo -------------------------------------------

  var enVivo = document.getElementById('en-vivo');
  var enVivoQuien = document.getElementById('en-vivo-quien');
  var enVivoUltimo = document.getElementById('en-vivo-ultimo');
  var llamadaEnCurso = null;

  function pintarEnVivo(llamada, turnos) {
    llamadaEnCurso = llamada;
    if (!llamada) {
      enVivo.hidden = true;
      return;
    }
    enVivo.hidden = false;
    enVivoQuien.textContent = (llamada.direccion === 'entrante' ? 'de ' : 'a ') +
      (llamada.telefono || 'desconocido');

    // En la cabecera sólo cabe lo último que se ha dicho; la conversación entera
    // está a un clic. Poner más aquí empujaría hacia abajo la pestaña que se mira.
    var ultimo = turnos.length ? turnos[turnos.length - 1] : null;
    enVivoUltimo.textContent = ultimo
      ? (ultimo.rol === 'agente' ? 'Agente: ' : 'Cliente: ') + ultimo.texto
      : 'Todavía no ha dicho nada.';
  }

  enVivo.addEventListener('click', function () {
    if (llamadaEnCurso) { abrirLlamada(llamadaEnCurso.id); }
  });

  // ---- modal de detalle ----------------------------------------------------

  var modalFondo = document.getElementById('modal-fondo');
  var modalTitulo = document.getElementById('modal-titulo');
  var modalCuerpo = document.getElementById('modal-cuerpo');

  var cierreModal = null;

  /** El hueco que ocupa el contenido mientras llega.
   *
   * No es decoración: el modal se abría con un «Cargando…» de una línea y, en cuanto
   * respondía la consulta, la caja pegaba un salto hasta su tamaño real **en mitad de
   * la animación de entrada**. Ese salto se comía la transición y lo que se veía era
   * un pop. Con el hueco ya ocupado, la caja no cambia de tamaño y la apertura se
   * lee entera.
   */
  function esqueleto() {
    var filas = '';
    for (var i = 0; i < 5; i++) {
      filas += '<div class="hueso hueso-etiqueta"></div>' +
               '<div class="hueso hueso-valor"></div>';
    }
    var lineas = '';
    // Anchos distintos para que parezca conversación y no una tabla: todas iguales
    // se leen como un bloque cargando, no como frases.
    ['92%', '74%', '86%', '61%'].forEach(function (ancho) {
      lineas += '<div class="hueso hueso-turno" style="width:' + ancho + '"></div>';
    });
    return '<div class="esqueleto" aria-hidden="true">' +
           '<div class="esqueleto-ficha">' + filas + '</div>' +
           '<div class="hueso hueso-titulo"></div>' + lineas +
           '</div><p class="sr-solo" role="status">Cargando…</p>';
  }

  /** Muestra el modal con su transición de entrada.
   *
   * `hidden` es `display: none`, y desde ahí no hay nada que transicionar. Hay que
   * quitarlo primero, dejar que el navegador registre el estado inicial —eso es lo
   * que fuerza la lectura de `offsetWidth`— y sólo entonces poner la clase. Sin ese
   * paso intermedio los dos estados caen en el mismo fotograma y el cuadro aparece
   * de golpe, que es justo lo que se quiere evitar.
   */
  function mostrarModal() {
    clearTimeout(cierreModal);
    modalFondo.hidden = false;
    void modalFondo.offsetWidth;
    modalFondo.classList.add('abierto');
  }

  function cerrarModal() {
    if (modalFondo.hidden) { return; }
    modalFondo.classList.remove('abierto');
    // Se oculta cuando la salida ha terminado, no antes: con `hidden` inmediato el
    // cuadro desaparece de golpe y la animación de cierre no llega a verse. El
    // tiempo va aquí y en el CSS, así que si se cambia uno hay que cambiar el otro.
    cierreModal = setTimeout(function () {
      modalFondo.hidden = true;
      modalCuerpo.innerHTML = '';
    }, 180);
  }

  document.getElementById('modal-cerrar').addEventListener('click', cerrarModal);
  modalFondo.addEventListener('click', function (ev) {
    // Sólo el fondo: un clic dentro del cuadro no debe cerrarlo.
    if (ev.target === modalFondo) { cerrarModal(); }
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && !modalFondo.hidden) { cerrarModal(); }
  });

  function abrirPedido(id) {
    mostrarModal();
    modalTitulo.textContent = 'Pedido #' + id;
    modalCuerpo.innerHTML = esqueleto();

    fetch('/api/pedidos/' + id)
      .then(function (r) {
        // Mismo motivo que en `cargarPedidos`: un 404 aqui no es una respuesta vacia,
        // es una ruta que este servicio todavia no tiene.
        if (!r.ok && r.status !== 404) { throw new Error('HTTP ' + r.status); }
        return r.json();
      })
      .then(function (d) {
        if (d.error || d.detail) {
          modalCuerpo.innerHTML = '<p class="vacio">' +
            escapar(d.mensaje || d.detail || 'No se pudo leer el pedido.') + '</p>';
          return;
        }
        var p = d.pedido;
        var html = '<dl class="ficha">' +
          '<dt>Qué pide</dt><dd><strong>' + escapar(p.resumen) + '</strong></dd>' +
          '<dt>A nombre de</dt><dd>' + (escapar(p.nombre) || '—') + '</dd>' +
          '<dt>Para cuándo</dt><dd>' + (escapar(p.cuando_texto) || '—') + '</dd>' +
          '<dt>Teléfono</dt><dd>' + (escapar(p.telefono) || '—') + '</dd>' +
          '<dt>Apuntado</dt><dd>' +
            escapar((p.creado_en || '').replace('T', ' ')) + '</dd>' +
          '<dt>Estado</dt><dd>' + (p.reemplazado_por
              ? 'cambiado por #' + p.reemplazado_por
              : (p.atendido ? 'atendido' : 'pendiente')) + '</dd>';
        if (p.detalles) {
          html += '<dt>Detalles</dt><dd>' + escapar(p.detalles) + '</dd>';
        }
        html += '</dl>';

        // La conversacion va con el pedido porque la pregunta de quien lo mira es
        // «¿esto es lo que el cliente dijo de verdad?», y la respuesta es la llamada.
        if (d.turnos && d.turnos.length) {
          html += '<h4>De esta conversación (llamada #' +
            escapar(p.llamada_id) + ')</h4><div class="conversacion-modal">';
          d.turnos.forEach(function (t) {
            var quien = t.rol === 'agente' ? 'Agente' : 'Cliente';
            html += '<p class="turno" data-rol="' + escapar(t.rol) + '"><strong>' +
              quien + ':</strong> ' + escapar(t.texto) + '</p>';
          });
          html += '</div>';
        } else {
          html += '<p class="vacio">Sin conversación registrada.</p>';
        }
        modalCuerpo.innerHTML = html;
      })
      .catch(function () {
        modalCuerpo.innerHTML = '<p class="vacio">No se pudo leer el pedido.</p>';
      });
  }

  // ---- sondeo --------------------------------------------------------------

  var pulsoVivo = document.getElementById('pulso-vivo');
  var temporizadorVivo = null;
  // Cada vuelta abre una conexion a la base. 3 s deja ver aparecer un pedido
  // mientras se habla sin robarle tiempo al turno de la llamada en curso; fuera de
  // esos dos momentos no hay nada que pueda cambiar de un segundo a otro, asi que
  // se espacia. El sondeo no para nunca del todo porque el aviso de llamada en
  // curso vive en la cabecera y tiene que aparecer se este donde se este.
  var MS_SONDEO_ATENTO = 3000;
  var MS_SONDEO_TRANQUILO = 15000;

  function ritmo() {
    var enPedidos = document.getElementById('p-pedidos').classList.contains('activa');
    return (llamadaEnCurso || enPedidos) ? MS_SONDEO_ATENTO : MS_SONDEO_TRANQUILO;
  }

  function cargarPedidos() {
    // `fetch` sólo rechaza si no hay red: un 404 o un 500 llegan aquí como respuesta
    // buena. Sin comprobar `r.ok`, un servidor con código viejo —que no conoce esta
    // ruta— se veía como «todavía no ha apuntado nada», que es la respuesta correcta
    // a una pregunta que nadie hizo. Pasó de verdad: el navegador cargaba este JS
    // nuevo del disco mientras el proceso seguía con el Python de antes.
    return fetch('/api/vivo')
      .then(function (r) {
        if (!r.ok) { throw new Error('HTTP ' + r.status); }
        return r.json();
      })
      .then(function (d) {
        // La tabla sólo se repinta con su pestaña delante; el aviso de la cabecera,
        // siempre, que para eso está arriba.
        if (document.getElementById('p-pedidos').classList.contains('activa')) {
          pintarPedidos(d.pedidos || [], d.pendientes || 0);
        }
        pintarEnVivo(d.en_curso, d.turnos || []);
      })
      .catch(function (ex) {
        sondearVivo(false);
        pintarEnVivo(null, []);
        listadoPedidos.innerHTML = '<p class="vacio">No se pudieron leer los pedidos (' +
          escapar(ex.message) + ').' +
          (String(ex.message).indexOf('404') >= 0
            ? ' Esta ruta es nueva: el servicio está corriendo con una versión' +
              ' anterior. Reinícialo y vuelve a esta pestaña.'
            : '') +
          '</p>';
      });
  }

  var sondeando = false;

  function sondearVivo(encender) {
    clearTimeout(temporizadorVivo);
    sondeando = !!encender;
    pulsoVivo.hidden = !encender;
    if (!encender) { return; }

    // Encadenado y no `setInterval`: si una vuelta tarda, la siguiente espera en vez
    // de amontonarse encima y dejar varias consultas a la vez contra la base.
    function vuelta() {
      if (!sondeando) { return; }
      // Con la ventana de fondo el navegador no repinta nada, asi que sondear seria
      // gastar por gastar — pero se sigue programando la siguiente vuelta, para que
      // al volver se refresque solo.
      var trabajo = document.hidden ? Promise.resolve() : cargarPedidos();
      trabajo.then(function () {
        if (sondeando) { temporizadorVivo = setTimeout(vuelta, ritmo()); }
      });
    }
    temporizadorVivo = setTimeout(vuelta, ritmo());
  }

  listadoPedidos.addEventListener('click', function (ev) {
    if (ev.target.closest('button')) { return; }
    var fila = ev.target.closest('tr[data-pedido]');
    if (fila) { abrirPedido(fila.dataset.pedido); }
  });

  prepararSecretos();
  vigilarCampos();
  cargarConfig();
  cargarLlamadas();
  cargarPedidos();
  // El sondeo arranca con la pagina y ya no para: el aviso de llamada en curso vive
  // en la cabecera, asi que tiene que poder aparecer este uno donde este.
  sondearVivo(true);
})();
