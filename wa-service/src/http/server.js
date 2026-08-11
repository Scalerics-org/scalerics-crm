'use strict';

const Fastify = require('fastify');
const { z } = require('zod');

const altaLeadSchema = z.object({
  external_id: z.union([z.string(), z.number()]).optional().transform((v) => (v == null ? undefined : String(v))),
  nombre: z.string().trim().min(1, 'nombre requerido'),
  rubro: z.string().trim().optional(),
  telefono: z.string().trim().min(1, 'telefono requerido'),
  necesidad: z.string().trim().optional(),
  origen: z.string().trim().optional().default('form'),
});

const envioSchema = z.object({
  telefono: z.string().trim().min(1),
  text: z.string().trim().min(1),
  skip_delay: z.boolean().optional().default(false),
});

function crearServidor({ cfg, repo, cola, proveedor, servicioLeads, scheduler, logger }) {
  const app = Fastify({ logger: false });

  // x-api-key en todo menos /health. El servicio no se expone a internet:
  // solo red interna o 127.0.0.1.
  app.addHook('onRequest', async (req, reply) => {
    if (req.url === '/health' || req.url.startsWith('/health?')) return;
    if (req.headers['x-api-key'] !== cfg.WA_API_KEY) {
      return reply.code(401).send({ ok: false, error: 'no autorizado' });
    }
  });

  app.get('/health', async () => ({
    ok: true,
    provider: proveedor.nombre,
    connected: proveedor.estado().conectado,
    queue_depth: cola.pendientes(),
    db: 'ok',
  }));

  app.post('/leads', async (req, reply) => {
    const parsed = altaLeadSchema.safeParse(req.body ?? {});
    if (!parsed.success) {
      return reply.code(400).send({
        ok: false,
        error: parsed.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('; '),
      });
    }

    const { lead, yaExistia, welcomeEnSegundos } = servicioLeads.alta(parsed.data);

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

  app.get('/session/status', async () => proveedor.estado());

  // Util para operar: dispara los jobs vencidos sin esperar al intervalo.
  app.post('/jobs/run', async () => ({ ok: true, procesados: scheduler.correrVencidos() }));

  return app;
}

module.exports = { crearServidor, altaLeadSchema };
