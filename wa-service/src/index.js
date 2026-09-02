'use strict';

const { cargar } = require('./config');
const { construir } = require('./app');
const { avisarSiEstaRoto } = require('./agenda/calendly');
const { avisarSiFaltaClave } = require('./ia/modelo');
const { arrancarBackups } = require('./backup');

async function main() {
  const cfg = cargar();
  const { proveedor, scheduler, vigilanteReservas, cola, db, app, logger, ia } = construir(cfg);

  await proveedor.conectar();
  scheduler.arrancar();
  vigilanteReservas.arrancar();
  arrancarBackups({ db, cfg, logger });
  avisarSiFaltaClave({ cfg, cola, ia, logger });

  // Sin await: que el bot no espere a Calendly para levantarse.
  avisarSiEstaRoto({ cfg, cola, logger })
    .catch((e) => logger.warn({ err: String(e.message || e) }, 'chequeo de Calendly'));

  await app.listen({ port: cfg.PORT, host: cfg.HOST });
  logger.info({ puerto: cfg.PORT, proveedor: proveedor.nombre }, 'wa-service arriba');

  const apagar = async (senial) => {
    logger.info({ senial }, 'apagando');
    scheduler.parar();
    await app.close();
    await proveedor.desconectar();
    process.exit(0);
  };
  process.on('SIGTERM', () => apagar('SIGTERM'));
  process.on('SIGINT', () => apagar('SIGINT'));
}

main().catch((e) => {
  console.error('No se pudo arrancar el wa-service:', e.message);
  process.exit(1);
});
