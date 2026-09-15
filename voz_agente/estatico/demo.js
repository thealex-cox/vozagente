(function () {
  'use strict';

  // Umbral de partida. Es solo el punto de arranque: al empezar se mide el ruido
  // real del sitio y se recalcula, porque un microfono flojo o una sala silenciosa
  // mueven este numero un orden de magnitud. Con un valor fijo, un microfono por
  // debajo del umbral no graba nunca y desde fuera se ve como «no me responde»:
  // no hay error, no hay peticion al servidor, no hay nada que mirar.
  var UMBRAL_BASE = 0.02;
  var UMBRAL_MINIMO = 0.008;   // por debajo se dispara con el ruido de la sala
  var UMBRAL_MAXIMO = 0.06;    // por encima habria que gritar
  var MARGEN_SOBRE_RUIDO = 2.5;
  var CALIBRACION_MS = 1200;
  var SILENCIO_MS = 900;
  var MIN_HABLA_MS = 350;

  var boton = document.getElementById('boton');
  var selIdioma = document.getElementById('idioma');
  var chkTecnico = document.getElementById('tecnico');
  var estado = document.getElementById('estado');
  var estadoTexto = document.getElementById('estado-texto');
  var nivel = document.getElementById('nivel');
  var nivelNum = document.getElementById('nivel-num');
  var conversacion = document.getElementById('conversacion');
  var aviso = document.getElementById('aviso');
  var titulo = document.getElementById('titulo');
  var subtitulo = document.getElementById('subtitulo');

  var ws = null;
  var stream = null;
  var audioCtx = null;
  var analizador = null;
  var grabadora = null;
  var trozos = [];
  var activo = false;
  var grabando = false;
  var hablandoAgente = false;
  var inicioHabla = 0;
  var ultimoSonido = 0;
  var temporizador = null;
  var cola = [];
  var reproduciendo = false;
  var turnoCerrado = true;
  var umbralVoz = UMBRAL_BASE;
  var calibrandoHasta = 0;
  var muestrasRuido = [];
  var nivelMaximo = 0;

  function mostrarAviso(texto) {
    aviso.textContent = texto;
    aviso.hidden = !texto;
  }

  function ponerEstado(valor, texto) {
    estado.dataset.valor = valor;
    estadoTexto.textContent = texto;
  }

  function burbuja(clase, quien, texto) {
    var div = document.createElement('div');
    div.className = 'turno ' + clase;
    if (quien) {
      var etiqueta = document.createElement('span');
      etiqueta.className = 'quien';
      etiqueta.textContent = quien;
      div.appendChild(etiqueta);
    }
    div.appendChild(document.createTextNode(texto));
    conversacion.appendChild(div);
    div.scrollIntoView({ behavior: 'smooth', block: 'end' });
    return div;
  }

  function chipMetricas(m) {
    if (!chkTecnico.checked) { return; }
    var partes = [];
    if (m.primer_audio != null) { partes.push('primer audio ' + m.primer_audio.toFixed(2) + ' s'); }
    if (m.ttft != null) { partes.push('TTFT ' + m.ttft.toFixed(2) + ' s'); }
    if (m.total != null) { partes.push('total ' + m.total.toFixed(2) + ' s'); }
    if (!partes.length) { return; }
    var div = document.createElement('div');
    div.className = 'metricas';
    div.textContent = partes.join('  ·  ');
    conversacion.appendChild(div);
    div.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }

  function b64ABlob(b64, mime) {
    var binario = atob(b64);
    var bytes = new Uint8Array(binario.length);
    for (var i = 0; i < binario.length; i++) { bytes[i] = binario.charCodeAt(i); }
    return new Blob([bytes], { type: mime });
  }

  function encolarAudio(blob) {
    cola.push(blob);
    if (!reproduciendo) { siguienteAudio(); }
  }

  function siguienteAudio() {
    var blob = cola.shift();
    if (!blob) {
      reproduciendo = false;
      hablandoAgente = false;
      cerrarTurnoSiProcede();
      return;
    }
    reproduciendo = true;
    hablandoAgente = true;
    ponerEstado('hablando', 'El asistente está hablando');
    var url = URL.createObjectURL(blob);
    var sonido = new Audio(url);
    var seguir = function () { URL.revokeObjectURL(url); siguienteAudio(); };
    sonido.onended = seguir;
    sonido.onerror = seguir;
    sonido.play().catch(seguir);
  }

  function cerrarTurnoSiProcede() {
    if (!activo || reproduciendo || cola.length || !turnoCerrado) { return; }
    ponerEstado('escuchando', 'Escuchando');
  }

  function ponerNegocio(nombre, saludo, idioma) {
    document.title = nombre;
    titulo.textContent = nombre;
    subtitulo.textContent = saludo
      ? 'Al descolgar dice: “' + saludo + '”'
      : 'Demostración por navegador. Hable con normalidad.';
    if (idioma && selIdioma.value !== idioma) {
      selIdioma.value = idioma;
      // El selector manda su idioma nada más conectar, antes de saber cuál es el
      // del negocio, así que el servidor puede haber rehecho el agente en el que
      // no toca. Se le vuelve a decir ya con el bueno; si coincide, no hace nada.
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ tipo: 'config', idioma: idioma }));
      }
    }
  }

  function conectar() {
    var esquema = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(esquema + '://' + location.host + '/ws' + location.search);

    ws.onopen = function () {
      ws.send(JSON.stringify({ tipo: 'config', idioma: selIdioma.value }));
    };

    ws.onmessage = function (evento) {
      var datos = JSON.parse(evento.data);
      if (datos.tipo === 'enlace') {
        if (datos.negocio) {
          ponerNegocio(datos.negocio, datos.saludo, datos.idioma);
        }
        if (datos.activo) {
          mostrarAviso('Enlazado a la llamada ' + datos.llamada +
            ': el asistente puede consultar y registrar de verdad, y la conversación queda guardada.');
        } else if (datos.llamada) {
          mostrarAviso('No se pudo enlazar con la llamada ' + datos.llamada +
            '. El asistente responde, pero sin consultar ni registrar nada.');
        }
      } else if (datos.tipo === 'usuario') {
        if (datos.texto) { burbuja('turno-usuario', 'Usted', datos.texto); }
      } else if (datos.tipo === 'audio') {
        burbuja('turno-agente', 'Asistente', datos.texto);
        encolarAudio(b64ABlob(datos.audio_b64, datos.mime));
      } else if (datos.tipo === 'estado') {
        if (datos.valor === 'pensando' && !reproduciendo) {
          ponerEstado('pensando', 'Pensando');
        }
      } else if (datos.tipo === 'fin') {
        turnoCerrado = true;
        chipMetricas(datos.metricas || {});
        if (datos.metricas && datos.metricas.vacio) {
          ponerEstado('escuchando', 'No se entendió nada, inténtelo de nuevo');
        }
        cerrarTurnoSiProcede();
      } else if (datos.tipo === 'error') {
        burbuja('turno-error', null, datos.mensaje);
        ponerEstado('error', 'Error');
        turnoCerrado = true;
      }
    };

    ws.onclose = function () {
      if (activo) { detener('Se perdió la conexión con el servidor'); }
    };

    ws.onerror = function () {
      mostrarAviso('No se pudo conectar con el servidor de voz.');
    };
  }

  function vigilarNivel() {
    var buffer = new Uint8Array(analizador.fftSize);
    analizador.getByteTimeDomainData(buffer);

    var suma = 0;
    for (var i = 0; i < buffer.length; i++) {
      var v = (buffer[i] - 128) / 128;
      suma += v * v;
    }
    var rms = Math.sqrt(suma / buffer.length);
    nivel.style.width = Math.min(100, rms * 400) + '%';

    var ahora = Date.now();
    if (rms > nivelMaximo) { nivelMaximo = rms; }
    if (nivelNum) {
      // El nivel y el umbral, a la vista. Es lo que convierte «no me responde» en
      // un diagnostico de un vistazo: si el numero no se mueve al hablar, el
      // microfono no esta entrando; si se mueve pero no llega al umbral, es
      // sensibilidad.
      nivelNum.textContent = rms.toFixed(3) + ' / ' + umbralVoz.toFixed(3);
      nivelNum.dataset.pasa = rms > umbralVoz ? 'si' : 'no';
    }

    if (ahora < calibrandoHasta) {
      muestrasRuido.push(rms);
      return;
    }
    if (muestrasRuido.length) {
      // La mediana, no la media: un portazo durante la calibracion subiria la media
      // y dejaria el umbral por encima de la voz durante toda la sesion.
      var ordenadas = muestrasRuido.slice().sort(function (a, b) { return a - b; });
      var ruido = ordenadas[Math.floor(ordenadas.length / 2)];
      muestrasRuido = [];
      umbralVoz = Math.min(UMBRAL_MAXIMO, Math.max(UMBRAL_MINIMO, ruido * MARGEN_SOBRE_RUIDO));
      ponerEstado('escuchando', 'Escuchando');
      if (nivelMaximo < 0.0005) {
        // Mediana exactamente cero es la firma de que no llega audio —un microfono
        // flojo daria una señal continua pequeña—, asi que es el boton de silencio,
        // el permiso o el dispositivo equivocado. Decirlo aqui ahorra buscar el
        // fallo en el servidor, donde no esta.
        mostrarAviso('El micrófono no está entrando: nivel cero durante la calibración. ' +
          'Revise que no esté silenciado y que el navegador use el micrófono correcto.');
      }
    }

    if (hablandoAgente || !turnoCerrado) { return; }

    if (rms > umbralVoz) {
      ultimoSonido = ahora;
      if (!grabando) {
        grabando = true;
        inicioHabla = ahora;
        trozos = [];
        grabadora.start();
        ponerEstado('escuchando', 'Le escucho…');
      }
    } else if (grabando && ahora - ultimoSonido > SILENCIO_MS) {
      grabando = false;
      if (ahora - inicioHabla < MIN_HABLA_MS) {
        grabadora.stop();
        trozos = [];
        return;
      }
      grabadora.stop();
    }
  }

  function tipoSoportado() {
    var candidatos = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
    for (var i = 0; i < candidatos.length; i++) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(candidatos[i])) {
        return candidatos[i];
      }
    }
    return '';
  }

  async function iniciar() {
    mostrarAviso('');

    if (!navigator.mediaDevices || !window.MediaRecorder) {
      mostrarAviso('Este navegador no permite capturar audio. Use Chrome, Edge o Firefox actualizados.');
      return;
    }

    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (ex) {
      mostrarAviso('No se pudo acceder al micrófono. Revise el permiso del navegador.');
      return;
    }

    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analizador = audioCtx.createAnalyser();
    analizador.fftSize = 1024;
    audioCtx.createMediaStreamSource(stream).connect(analizador);

    var mime = tipoSoportado();
    grabadora = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);

    grabadora.ondataavailable = function (evento) {
      if (evento.data && evento.data.size) { trozos.push(evento.data); }
    };

    grabadora.onstop = function () {
      if (!trozos.length || !ws || ws.readyState !== WebSocket.OPEN) { return; }
      var blob = new Blob(trozos, { type: grabadora.mimeType || 'audio/webm' });
      trozos = [];
      turnoCerrado = false;
      ponerEstado('pensando', 'Procesando');
      blob.arrayBuffer().then(function (buffer) { ws.send(buffer); });
    };

    conectar();

    activo = true;
    turnoCerrado = true;
    umbralVoz = UMBRAL_BASE;
    muestrasRuido = [];
    nivelMaximo = 0;
    calibrandoHasta = Date.now() + CALIBRACION_MS;
    ponerEstado('pensando', 'Calibrando el micrófono, no hable todavía…');
    boton.textContent = 'Terminar';
    boton.dataset.activo = 'si';
    selIdioma.disabled = true;
    temporizador = setInterval(vigilarNivel, 60);
  }

  function detener(motivo) {
    activo = false;
    clearInterval(temporizador);
    temporizador = null;

    if (grabando && grabadora && grabadora.state === 'recording') { grabadora.stop(); }
    grabando = false;
    cola = [];
    reproduciendo = false;
    hablandoAgente = false;

    if (ws && ws.readyState === WebSocket.OPEN) { ws.close(); }
    ws = null;

    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    if (audioCtx) { audioCtx.close(); audioCtx = null; }

    boton.textContent = 'Iniciar conversación';
    boton.dataset.activo = 'no';
    selIdioma.disabled = false;
    nivel.style.width = '0';
    ponerEstado('inactivo', motivo || 'Conversación terminada');
    if (motivo) { mostrarAviso(motivo); }
  }

  boton.addEventListener('click', function () {
    if (activo) { detener(''); } else { iniciar(); }
  });

  if (location.protocol !== 'https:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
    mostrarAviso('El micrófono sólo funciona sobre HTTPS o en localhost. Publique la demo con TLS antes de compartirla.');
  }

  fetch('/salud').then(function (r) { return r.json(); }).then(function (d) {
    if (!d.api_key) {
      mostrarAviso('El servidor no tiene configurada ANTHROPIC_API_KEY: el asistente no podrá responder.');
      return;
    }
    if (!d.listo) {
      mostrarAviso('El prompt de "' + d.persona + '" no está cargado. ' + (d.falta || ''));
      return;
    }
    if (d.horas != null && d.horas > 48) {
      mostrarAviso('El catálogo del asistente se exportó hace ' + Math.round(d.horas) + ' h; puede estar desactualizado.');
    }
  }).catch(function () {});
})();
