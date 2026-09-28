'use strict';

/**
 * Los pedidos al bot en el grupo de captación (pedido de Juan, 28/9).
 *
 * Cuando alguien lo nombra (@bot, "bot, …" o respondiendo a un mensaje suyo)
 * la IA interpreta qué le piden, el bot lo hace contra el CRM y contesta en el
 * grupo. Siempre contesta: si no entendió, dice qué sabe hacer.
 *
 * Puede consultar (resumen de actividad, a quién llamar hoy, qué se sabe de un
 * local) y hacer cosas chicas (agendar, anotar, cargar el dueño, marcar cliente
 * o "no le interesa", cargar visitas). No borra nada ni manda mails: eso queda
 * para el CRM, donde se ve lo que se está por hacer.
 */

const ACCIONES = ['resumen', 'llamar_hoy', 'info_local', 'agendar_reunion', 'agendar_llamada', 'anotar',
  'dueno', 'marcar_cliente', 'marcar_no_interesa', 'cargar_visitas', 'no_entiendo'];

function herramientaPedido(visitaSchema) {
  return {
    nombre: 'interpretar_pedido',
    descripcion: 'Qué le pidieron al bot, para hacerlo en el CRM.',
    parametros: {
      type: 'object',
      properties: {
        accion: {
          type: 'string',
          enum: ACCIONES,
          description: [
            'resumen: cuántas visitas, llamadas o reuniones hubo (hoy, ayer, la semana…).',
            'llamar_hoy: a quién hay que llamar hoy / qué está vencido.',
            'info_local: qué se sabe de un local.',
            'agendar_reunion / agendar_llamada: dejar una reunión o una llamada con fecha para un local.',
            'anotar: agregar una nota a un local.',
            'dueno: cargar el nombre o el celular del dueño o encargado de un local.',
            'marcar_cliente / marcar_no_interesa: cambiar el estado de un local.',
            'cargar_visitas: cargar uno o más locales visitados.',
            'no_entiendo: cualquier otra cosa.',
          ].join(' '),
        },
        local: { type: 'string', description: 'El nombre del local del que hablan, si hablan de uno.' },
        desde: { type: 'string', description: 'Para resumen: primer día, AAAA-MM-DD.' },
        hasta: { type: 'string', description: 'Para resumen: último día, AAAA-MM-DD.' },
        fecha: { type: 'string', description: 'Para agendar: AAAA-MM-DDTHH:MM en hora de Montevideo.' },
        nota: { type: 'string' },
        contacto: { type: 'string', description: 'Nombre del dueño o encargado.' },
        contacto_tel: { type: 'string', description: 'Celular del dueño o encargado.' },
        visitas: visitaSchema,
      },
      required: ['accion'],
    },
  };
}

const ESTADOS = {
  sin_contactar: 'Sin contactar', contactado: 'Contactado', reunion_agendada: 'Reunión agendada',
  reunion_hecha: 'Reunión hecha', piloto: 'Piloto', cerrado: 'Cliente', descartado: 'No le interesa',
};
const RESULTADOS = { visitado: 'visitados', interesado: 'interesados', reunion: 'reuniones',
  no_interesa: 'no les interesa', cliente: 'clientes' };

const AYUDA = 'Me podés pedir, nombrándome:\n'
  + '• cuántas visitas o llamadas hubo (hoy, la semana…)\n'
  + '• a quién hay que llamar hoy\n'
  + '• qué sabemos de un local\n'
  + '• agendar una reunión o una llamada\n'
  + '• anotar algo o cargar el dueño de un local\n'
  + '• marcar un local como cliente o "no le interesa"\n'
  + '• cargar visitas\n'
  + 'Y las visitas que anoten acá las cargo solo.';

const DIAS = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'];

function diaCorto(txt) {
  const m = String(txt || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return '';
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return `${DIAS[d.getDay()]} ${Number(m[3])}/${Number(m[2])}`;
}

function fechaHora(txt) {
  const h = String(txt || '').slice(11, 16);
  return `${diaCorto(txt)}${h ? ` ${h}` : ''}`;
}

function porPersona(obj) {
  return Object.entries(obj || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join(', ');
}

function normal(t) {
  return String(t || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ').trim().replace(/^(la|el|los|las) /, '');
}

function textoResumen(d, hoy) {
  const titulo = d.desde === d.hasta
    ? (d.desde === hoy ? 'Hoy' : diaCorto(d.desde))
    : `Del ${diaCorto(d.desde)} al ${diaCorto(d.hasta)}`;
  const v = d.visitas;
  const lineas = [`${titulo}: ${v.total} ${v.total === 1 ? 'visita' : 'visitas'}${v.total ? ` (${porPersona(v.por_persona)})` : ''}.`];
  const res = Object.entries(v.por_resultado || {}).map(([k, n]) => `${n} ${RESULTADOS[k] || k}`);
  if (res.length) lineas.push(res.join(', ') + '.');
  lineas.push(`Llamadas: ${d.llamadas.total}${d.llamadas.total ? ` (${porPersona(d.llamadas.por_persona)})` : ''}.`);
  if (d.reuniones.length) {
    lineas.push('Reuniones agendadas: ' + d.reuniones.map((r) => `${r.nombre}${r.fecha_reunion ? ` (${fechaHora(r.fecha_reunion)})` : ''}`).join(', ') + '.');
  }
  if (d.mails) lineas.push(`Mails: ${d.mails}.`);
  return lineas.join('\n');
}

function textoLlamarHoy(items) {
  const lista = (xs) => xs.slice(0, 10).map((p) => `${p.nombre}${p.cuando ? ` (${p.grupo === 0 ? diaCorto(p.cuando) : String(p.cuando).slice(11, 16)})` : ''}`).join(', ')
    + (xs.length > 10 ? ` y ${xs.length - 10} más` : '');
  const venc = items.filter((p) => p.grupo === 0);
  const hoy = items.filter((p) => p.grupo === 1);
  if (!venc.length && !hoy.length) return 'Hoy no hay nadie para llamar ni nada vencido. 👌';
  const lineas = [];
  if (venc.length) lineas.push(`Vencidas (${venc.length}): ${lista(venc)}`);
  if (hoy.length) lineas.push(`Para hoy (${hoy.length}): ${lista(hoy)}`);
  return lineas.join('\n');
}

function textoLocal(p) {
  const lineas = [`*${p.nombre}* · ${[p.barrio, p.tipo].filter(Boolean).join(' · ')} · ${p.target}% target`];
  const dueno = [p.contacto, p.contacto_tel].filter(Boolean).join(' · ');
  lineas.push(dueno ? `Dueño: ${dueno}` : `Teléfono: ${p.telefono || 'sin teléfono'}`);
  let estado = ESTADOS[p.estado] || p.estado;
  if (p.estado === 'reunion_agendada' && p.fecha_reunion) estado += ` · reunión ${fechaHora(p.fecha_reunion)}`;
  else if (p.proxima_llamada && !['cerrado', 'descartado'].includes(p.estado)) estado += ` · llamar ${fechaHora(p.proxima_llamada)}`;
  lineas.push(estado);
  if (p.ultima_visita) lineas.push(`Última visita: ${diaCorto(p.ultima_visita.hecha_en)}`);
  const nota = p.ultima_nota || (p.notas && !String(p.notas).startsWith('Traído de Google Maps') ? p.notas : '');
  if (nota) lineas.push(`Nota: «${String(nota).split('\n')[0]}»`);
  return lineas.join('\n');
}

function crearPedidos({ crm, modelo, sistema, visitaSchema, cargarVisita, resumenVisita, ahora }) {
  const HERRAMIENTA = herramientaPedido(visitaSchema);

  /** Un solo local por nombre, o un texto para contestar si no se pudo. */
  async function buscarLocal(nombre) {
    if (!nombre) return { error: '¿De qué local? Decime el nombre.' };
    const d = await crm('GET', `/api/fidelidad/lista?limite=5&q=${encodeURIComponent(nombre)}`);
    const items = d.items || [];
    if (!items.length) return { error: `No encontré «${nombre}» en el CRM.` };
    const exacto = items.filter((p) => normal(p.nombre) === normal(nombre));
    if (items.length === 1 || exacto.length === 1) return { local: exacto[0] || items[0] };
    return { error: `Encontré varios: ${items.map((p) => `${p.nombre}${p.barrio ? ` (${p.barrio})` : ''}`).join(', ')}. ¿Cuál?` };
  }

  async function atender(m) {
    const hoy = ahora().toLocaleDateString('en-CA', { timeZone: 'America/Montevideo' });
    const r = await modelo.pedir({
      system: sistema() + '\nAhora te están pidiendo algo a vos (te nombraron). Interpretá qué quieren.',
      mensajes: [{ role: 'user', content: `${m.nombre || 'Alguien del equipo'} escribió:\n${m.texto}` }],
      herramienta: HERRAMIENTA,
      maxTokens: 900,
    });
    const a = r?.argumentos;
    if (!a) return 'Ahora no puedo pensar (falló la IA). Probá de nuevo en un rato.';
    const autor = m.nombre || '';

    switch (a.accion) {
      case 'resumen': {
        const q = new URLSearchParams({ desde: a.desde || hoy, hasta: a.hasta || a.desde || hoy });
        return textoResumen(await crm('GET', `/api/fidelidad/resumen?${q}`), hoy);
      }
      case 'llamar_hoy':
        return textoLlamarHoy((await crm('GET', '/api/fidelidad/lista?limite=60')).items || []);
      case 'info_local': {
        const b = await buscarLocal(a.local);
        return b.error || textoLocal(b.local);
      }
      case 'agendar_reunion':
      case 'agendar_llamada': {
        const b = await buscarLocal(a.local);
        if (b.error) return b.error;
        if (!a.fecha) return `¿Para cuándo la ${a.accion === 'agendar_reunion' ? 'reunión' : 'llamada'} con ${b.local.nombre}? Decime día y hora.`;
        const tipo = a.accion === 'agendar_reunion' ? 'reunion' : 'llamada';
        const d = await crm('POST', `/api/fidelidad/prospectos/${b.local.id}/agendar`, { tipo, fecha: a.fecha, autor });
        const p = d.prospecto;
        return `✓ ${p.nombre} · ${tipo === 'reunion' ? `reunión ${fechaHora(p.fecha_reunion)}` : `llamar ${fechaHora(p.proxima_llamada)}`}`;
      }
      case 'anotar': {
        const b = await buscarLocal(a.local);
        if (b.error) return b.error;
        if (!a.nota) return `¿Qué le anoto a ${b.local.nombre}?`;
        await crm('POST', `/api/fidelidad/prospectos/${b.local.id}/nota`, { nota: a.nota, autor });
        return `✓ Anotado en ${b.local.nombre}: «${a.nota}»`;
      }
      case 'dueno': {
        const b = await buscarLocal(a.local);
        if (b.error) return b.error;
        const cambios = Object.fromEntries(Object.entries({ contacto: a.contacto, contacto_tel: a.contacto_tel }).filter(([, v]) => v));
        if (!Object.keys(cambios).length) return `¿Cómo se llama el dueño de ${b.local.nombre}, o cuál es su celular?`;
        await crm('PUT', `/api/fidelidad/prospectos/${b.local.id}`, cambios);
        return `✓ ${b.local.nombre} · dueño: ${[cambios.contacto, cambios.contacto_tel].filter(Boolean).join(' · ')}`;
      }
      case 'marcar_cliente':
      case 'marcar_no_interesa': {
        const b = await buscarLocal(a.local);
        if (b.error) return b.error;
        const estado = a.accion === 'marcar_cliente' ? 'cerrado' : 'descartado';
        await crm('POST', `/api/fidelidad/prospectos/${b.local.id}/estado`, { estado, motivo: 'No le interesa' });
        return a.accion === 'marcar_cliente' ? `✓ ${b.local.nombre} es cliente 🎉` : `✓ ${b.local.nombre} → No le interesa`;
      }
      case 'cargar_visitas': {
        const visitas = Array.isArray(a.visitas) ? a.visitas.slice(0, 6) : [];
        if (!visitas.length) return '¿Qué local cargo? Decime el nombre y cómo salió.';
        const partes = [];
        for (const v of visitas) {
          try { partes.push(resumenVisita(await cargarVisita(v, autor))); } catch (e) { partes.push(`No pude cargar *${v.nombre}*: ${e.message || e}`); }
        }
        return '✓ Cargado en el CRM\n' + partes.join('\n\n');
      }
      default:
        return `No entendí qué necesitás. ${AYUDA}`;
    }
  }

  return { atender };
}

module.exports = { crearPedidos, textoResumen, textoLlamarHoy, textoLocal, normal, AYUDA };
