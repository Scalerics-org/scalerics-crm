'use strict';

const { listar: listarBackups, hacerBackup } = require('../backup');

const Fastify = require('fastify');
const { z } = require('zod');
const rutasCrm = require('./routes/crm');

const altaLeadSchema = z.object({
  external_id: z.union([z.string(), z.number()]).optional().transform((v) => (v == null ? undefined : String(v))),
  nombre: z.string().trim().min(1, 'nombre requerido'),
  rubro: z.string().trim().optional(),
  telefono: z.string().trim().min(1, 'telefono requerido'),
  necesidad: z.string().trim().optional(),
  origen: z.string().trim().optional().default('form'),
});

const reunionSchema = z.object({
  telefono: z.string().trim().min(1, 'telefono requerido'),
  // ISO 8601. Es la hora de la reunion, no la de la reserva.
  meeting_time: z.string().trim().min(1, 'meeting_time requerido')
    .refine((v) => !Number.isNaN(new Date(v).getTime()), 'meeting_time no es una fecha valida'),
  meeting_url: z.string().trim().optional(),
});

const envioSchema = z.object({
  telefono: z.string().trim().min(1),
  text: z.string().trim().min(1),
  skip_delay: z.boolean().optional().default(false),
});

function crearServidor({ cfg, repo, cola, proveedor, servicioLeads, scheduler, embudo = null, logger }) {
  const app = Fastify({ logger: false });

  // x-api-key en todo menos /health y /api/*. El servicio no se expone a
  // internet: solo red interna o 127.0.0.1.
  //
  // /api/* es el contrato heredado que consume el panel WA del CRM y va con
  // x-admin-token: lo valida su propio hook en routes/crm.js.
  app.addHook('onRequest', async (req, reply) => {
    if (req.url === '/health' || req.url.startsWith('/health?')) return;
    if (req.url.startsWith('/api/')) return;
    // El QR se escanea desde un browser, que no puede mandar headers: se acepta
    // la clave por query. El servicio no esta expuesto a internet.
    if (req.url.startsWith('/session/qr') && req.query?.key === cfg.WA_API_KEY) return;
    if (req.headers['x-api-key'] !== cfg.WA_API_KEY) {
      return reply.code(401).send({ ok: false, error: 'no autorizado' });
    }
  });

  rutasCrm.registrar(app, { cfg, repo, cola, embudo, logger });

  app.get('/health', async () => {
    // Salientes aceptados por el proveedor hace mas de 5 minutos que siguen sin
    // acuse de entrega. Si esto crece, los mensajes estan quedando en
    // "Esperando este mensaje" del lado del destinatario.
    // El formato lo normaliza el repo: aca se pasa una fecha y ya.
    const hace5min = new Date(Date.now() - 5 * 60_000).toISOString();
    return {
      ok: true,
      provider: proveedor.nombre,
      connected: proveedor.estado().conectado,
      queue_depth: cola.pendientes(),
      queue_paused: cola.pausada(),
      undelivered: repo.sinConfirmar(hace5min),
      db: 'ok',
    };
  });

  app.post('/leads', async (req, reply) => {
    const parsed = altaLeadSchema.safeParse(req.body ?? {});
    if (!parsed.success) {
      return reply.code(400).send({
        ok: false,
        error: parsed.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('; '),
      });
    }

    const { lead, yaExistia, welcomeEnSegundos } = await servicioLeads.alta(parsed.data);

    if (yaExistia) {
      return reply.code(200).send({ ok: true, lead_id: lead.id, status: 'ya_existia' });
    }
    return reply.code(202).send({
      ok: true,
      lead_id: lead.id,
      status: lead.status === 'failed' ? 'telefono_invalido' : 'queued',
      welcome_eta_seconds: welcomeEnSegundos,
    });
  });

  app.post('/messages/send', async (req, reply) => {
    const parsed = envioSchema.safeParse(req.body ?? {});
    if (!parsed.success) {
      return reply.code(400).send({ ok: false, error: 'telefono y text requeridos' });
    }
    const { normalizar } = require('../telefono');
    const to = normalizar(parsed.data.telefono, cfg.DEFAULT_COUNTRY_CODE);
    if (!to) return reply.code(400).send({ ok: false, error: 'telefono invalido' });

    const lead = repo.leadPorTelefono(to);
    cola.encolar({
      to,
      texto: parsed.data.text,
      kind: 'manual',
      leadId: lead ? lead.id : null,
      delayMs: parsed.data.skip_delay ? 0 : undefined,
    });
    return reply.code(202).send({ ok: true });
  });

  /**
   * El CRM avisa que el lead agendo en Calendly (lo sabe por su webhook).
   * Cancela el follow-up y programa los recordatorios.
   */
  app.post('/meetings', async (req, reply) => {
    const parsed = reunionSchema.safeParse(req.body ?? {});
    if (!parsed.success) {
      return reply.code(400).send({
        ok: false,
        error: parsed.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('; '),
      });
    }
    const { normalizar } = require('../telefono');
    const tel = normalizar(parsed.data.telefono, cfg.DEFAULT_COUNTRY_CODE);
    if (!tel) return reply.code(400).send({ ok: false, error: 'telefono invalido' });

    const lead = repo.leadPorTelefono(tel);
    if (!lead) return reply.code(404).send({ ok: false, error: 'no hay lead con ese telefono' });

    const resultado = servicioLeads.registrarReunion(lead.id, parsed.data);
    return reply.code(200).send({ ok: true, lead_id: lead.id, ...resultado });
  });

  /**
   * Reinicia las claves de cifrado con un destinatario. Se usa cuando le queda
   * un mensaje en "Esperando este mensaje": el proximo envio renegocia.
   */
  app.post('/session/reset-cifrado', async (req, reply) => {
    if (!proveedor.reiniciarCifrado) {
      return reply.code(400).send({ ok: false, error: 'el proveedor no maneja cifrado' });
    }
    const { normalizar } = require('../telefono');
    const tel = normalizar((req.body || {}).telefono, cfg.DEFAULT_COUNTRY_CODE);
    if (!tel) return reply.code(400).send({ ok: false, error: 'telefono invalido' });

    const borradas = proveedor.reiniciarCifrado(tel);
    return { ok: true, telefono: tel, sesiones_borradas: borradas };
  });

  /**
   * Vuelve un lead al principio. Le borra lo que el embudo averiguo y marca
   * desde donde cuenta la conversacion nueva: los mensajes viejos siguen
   * visibles en el panel del CRM, pero la IA ya no los lee — si no, arrancaria
   * de cero con el estado y retomaria una charla que para el lead ya termino.
   */
  app.post('/leads/:telefono/reiniciar', async (req, reply) => {
    const { normalizar } = require('../telefono');
    const tel = normalizar(req.params.telefono, cfg.DEFAULT_COUNTRY_CODE);
    if (!tel) return reply.code(400).send({ ok: false, error: 'telefono invalido' });

    const lead = repo.leadPorTelefono(tel);
    if (!lead) return reply.code(404).send({ ok: false, error: 'no hay lead con ese telefono' });

    const fresco = repo.reiniciarLead(lead.id, new Date().toISOString());
    logger?.info({ leadId: lead.id, telefono: tel }, 'lead reiniciado');
    return { ok: true, lead_id: lead.id, estado: fresco.fsm_state };
  });

  app.get('/session/status', async () => proveedor.estado());

  /**
   * QR para vincular el numero. PNG por defecto para poder abrirlo del celular;
   * ?format=json devuelve el string crudo.
   */
  app.get('/session/qr', async (req, reply) => {
    const crudo = proveedor.qrCrudo?.();
    if (!crudo) {
      return reply.code(404).send({
        ok: false,
        error: proveedor.estado().conectado
          ? 'ya esta vinculado, no hay QR pendiente'
          : 'todavia no hay QR: esperar unos segundos a que el proveedor conecte',
      });
    }
    if (req.query.format === 'json') return { ok: true, qr: crudo };

    const png = await require('qrcode').toBuffer(crudo, { width: 512, margin: 2 });
    if (req.query.format === 'png') {
      return reply.type('image/png').header('Cache-Control', 'no-store').send(png);
    }

    // Por defecto una pagina que se refresca sola: el QR de WhatsApp caduca a
    // los ~20 segundos, y abrir una imagen fija lleva a escanear uno vencido.
    const clave = encodeURIComponent(req.query.key || '');
    return reply.type('text/html').header('Cache-Control', 'no-store').send(`<!doctype html>
<meta charset="utf-8"><title>Vincular WhatsApp — Scalerics</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{font-family:system-ui,sans-serif;background:#0a0f1a;color:#e2e8f0;
       min-height:100vh;margin:0;display:flex;flex-direction:column;
       align-items:center;justify-content:center;gap:18px;padding:24px}
  img{background:#fff;padding:12px;border-radius:12px;width:min(320px,80vw)}
  p{max-width:380px;text-align:center;color:#94a3b8;font-size:.9rem;line-height:1.5;margin:0}
  b{color:#e2e8f0}
</style>
<img id="qr" src="/session/qr?format=png&key=${clave}" alt="Código QR">
<p>En el teléfono del <b>número secundario</b>: WhatsApp → Ajustes →
   <b>Dispositivos vinculados</b> → Vincular un dispositivo.</p>
<p id="e" style="color:#64748b">El código se renueva solo cada 15 segundos.</p>
<script>
  setInterval(async () => {
    const r = await fetch('/session/status', { headers: { 'x-api-key': decodeURIComponent('${clave}') } });
    const s = await r.json().catch(() => ({}));
    if (s.conectado) { document.body.innerHTML =
      '<p style="font-size:1.4rem;color:#34d399">✅ Vinculado como ' + (s.telefono || '') + '</p>'; return; }
    document.getElementById('qr').src = '/session/qr?format=png&key=${clave}&t=' + Date.now();
  }, 15000);
</script>`);
  });

  app.post('/session/logout', async (req, reply) => {
    if ((req.body || {}).confirm !== true) {
      return reply.code(400).send({
        ok: false,
        error: 'mandar {"confirm": true}: esto cierra la sesion y obliga a re-escanear el QR',
      });
    }
    await proveedor.cerrarSesion?.();
    return { ok: true };
  });

  // Util para operar: dispara los jobs vencidos sin esperar al intervalo.
  app.post('/jobs/run', async () => ({ ok: true, procesados: await scheduler.correrVencidos() }));

  /** Que respaldos hay. Sirve para saber si de verdad se estan haciendo. */
  app.get('/backups', async () => ({ ok: true, backups: listarBackups(cfg) }));

  /** Uno ahora, sin esperar al que toca. */
  app.post('/backups', async (req, reply) => {
    const r = hacerBackup({ db: repo.db, cfg, logger });
    if (!r) return reply.code(500).send({ ok: false, error: 'no se pudo respaldar' });
    return { ok: true, archivo: require('node:path').basename(r.archivo), kb: Math.round(r.bytes / 1024) };
  });

  return app;
}

module.exports = { crearServidor, altaLeadSchema };
