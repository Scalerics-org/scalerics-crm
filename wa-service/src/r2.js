'use strict';

const crypto = require('node:crypto');

/**
 * Sube los respaldos a Cloudflare R2.
 *
 * Es el unico lugar donde los respaldos quedan afuera de Fly. Los snapshots del
 * volumen y las copias en /data protegen de un disco roto o de un bug, pero las
 * tres cosas viven en la misma cuenta: si se pierde el acceso, se pierde todo
 * junto. Ya paso una vez con otra app.
 *
 * Se firma a mano en vez de traer el SDK de AWS. Son sesenta lineas contra un
 * paquete de varios megas del que este servicio usaria una sola operacion, un
 * PUT. R2 habla S3, asi que esto mismo sirve para cualquier otro proveedor
 * cambiando el endpoint.
 */

const REGION = 'auto';
const SERVICIO = 's3';

const sha256 = (d) => crypto.createHash('sha256').update(d).digest('hex');
const hmac = (clave, dato) => crypto.createHmac('sha256', clave).update(dato).digest();

/** La clave de firma se deriva por fecha, region y servicio. */
function claveDeFirma(secreto, fecha) {
  return hmac(hmac(hmac(hmac(`AWS4${secreto}`, fecha), REGION), SERVICIO), 'aws4_request');
}

function crearR2({ cfg, logger = null, fetch: _fetch = globalThis.fetch } = {}) {
  const activo = Boolean(
    cfg.R2_ACCOUNT_ID && cfg.R2_BUCKET && cfg.R2_ACCESS_KEY_ID && cfg.R2_SECRET_ACCESS_KEY
  );
  const host = `${cfg.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`;

  return {
    activo,

    /**
     * @param {string} nombre  como queda en el bucket
     * @param {Buffer} cuerpo
     * @returns {Promise<{ok: boolean, estado?: number, error?: string}>}
     */
    async subir(nombre, cuerpo) {
      if (!activo) return { ok: false, error: 'sin credenciales' };

      const clave = `${cfg.R2_PREFIX ? `${cfg.R2_PREFIX.replace(/\/+$/, '')}/` : ''}${nombre}`;
      const ruta = `/${cfg.R2_BUCKET}/${clave.split('/').map(encodeURIComponent).join('/')}`;

      const ahora = new Date().toISOString().replace(/[:-]|\.\d{3}/g, '');
      const dia = ahora.slice(0, 8);
      const hashCuerpo = sha256(cuerpo);

      const canonica = [
        'PUT',
        ruta,
        '',
        `host:${host}`,
        `x-amz-content-sha256:${hashCuerpo}`,
        `x-amz-date:${ahora}`,
        '',
        'host;x-amz-content-sha256;x-amz-date',
        hashCuerpo,
      ].join('\n');

      const alcance = `${dia}/${REGION}/${SERVICIO}/aws4_request`;
      const aFirmar = ['AWS4-HMAC-SHA256', ahora, alcance, sha256(canonica)].join('\n');
      const firma = crypto
        .createHmac('sha256', claveDeFirma(cfg.R2_SECRET_ACCESS_KEY, dia))
        .update(aFirmar)
        .digest('hex');

      try {
        const r = await _fetch(`https://${host}${ruta}`, {
          method: 'PUT',
          headers: {
            Host: host,
            'x-amz-date': ahora,
            'x-amz-content-sha256': hashCuerpo,
            'Content-Length': String(cuerpo.length),
            Authorization: `AWS4-HMAC-SHA256 Credential=${cfg.R2_ACCESS_KEY_ID}/${alcance}, `
              + 'SignedHeaders=host;x-amz-content-sha256;x-amz-date, '
              + `Signature=${firma}`,
          },
          body: cuerpo,
        });

        if (!r.ok) {
          // El cuerpo del error de S3 dice cual de las credenciales esta mal,
          // y sin eso depurar esto es adivinar.
          const detalle = await r.text().catch(() => '');
          logger?.error({ estado: r.status, detalle: detalle.slice(0, 300) }, 'R2 rechazo el respaldo');
          return { ok: false, estado: r.status, error: detalle.slice(0, 300) };
        }

        logger?.info({ clave, kb: Math.round(cuerpo.length / 1024) }, 'respaldo subido a R2');
        return { ok: true, estado: r.status };
      } catch (e) {
        logger?.error({ err: String(e.message || e) }, 'no se pudo subir el respaldo a R2');
        return { ok: false, error: String(e.message || e) };
      }
    },
  };
}

module.exports = { crearR2 };
