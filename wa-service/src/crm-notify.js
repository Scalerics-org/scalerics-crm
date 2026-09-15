'use strict';

/**
 * Aviso al CRM cuando un lead califica: replica lo que hacia
 * bot/src/services/crm.js contra POST /api/bot/lead-qualified.
 *
 * Nunca lanza: que el CRM este caido no puede cortar la conversacion con el
 * lead, que es lo unico que no se puede recuperar despues.
 */
function crearNotificadorCRM({ cfg, repo, logger }) {
  const activo = Boolean(cfg.CRM_API_URL && cfg.CRM_ADMIN_TOKEN);

  return {
    activo,

    /**
     * Alguien escribio al WhatsApp: se le reporta al CRM, que manda el mail.
     *
     * El CRM decide SI corresponde avisar —el primer mensaje de la
     * conversacion, o el primero despues de 30 minutos de silencio— y lo
     * persiste. Aca no hay nada de esa logica a proposito: dos reglas para lo
     * mismo, en dos servicios, se separan; es la falla que se repitio varias
     * veces en este proyecto. El bot reporta y se calla.
     *
     * `direction: 'in'` siempre: el endpoint ignora cualquier otra cosa, asi
     * que un saliente que se cuele no puede disparar un mail ni correr la
     * ventana.
     *
     * No se espera la respuesta, y no puede tirar nunca: que el CRM este caido
     * no puede cortar la conversacion con el lead, que es lo unico que no se
     * recupera despues.
     */
    async mensajeEntrante({ telefono, nombre = '', texto = '' }) {
      if (!activo || !telefono) return false;

      try {
        const r = await fetch(`${cfg.CRM_API_URL.replace(/\/$/, '')}/api/bot/mensaje-entrante`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'x-admin-token': cfg.CRM_ADMIN_TOKEN },
          body: JSON.stringify({
            phone: telefono,
            name: nombre,
            text: String(texto || '').slice(0, 500),
            direction: 'in',
          }),
          signal: AbortSignal.timeout(5000),
        });
        if (!r.ok) {
          logger?.warn({ telefono, status: r.status }, 'el CRM rechazo el aviso de entrante');
          return false;
        }
        return true;
      } catch (e) {
        logger?.warn({ telefono, err: String(e.message || e) }, 'no se pudo avisar del entrante');
        return false;
      }
    },

    async leadCalifico(leadId) {
      if (!activo) return false;

      const l = repo.leadPorId(leadId);
      if (!l) return false;

      const payload = {
        phone: l.telefono,
        name: l.nombre,
        business_name: l.business_name,
        business_type: l.business_type,
        budget: l.budget,
        team_size: l.team_size,
        colors: l.colors,
        instagram_web: l.instagram_web,
        needs: l.needs,
        score: l.score,
        priority: l.priority,
        state: l.fsm_state,
        meeting_time: l.meeting_time,
        meeting_url: l.meeting_url,
      };

      try {
        const r = await fetch(`${cfg.CRM_API_URL.replace(/\/$/, '')}/api/bot/lead-qualified`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'x-admin-token': cfg.CRM_ADMIN_TOKEN },
          body: JSON.stringify(payload),
          signal: AbortSignal.timeout(5000),
        });
        if (!r.ok) {
          logger?.warn({ leadId, status: r.status }, 'el CRM rechazo el lead calificado');
          return false;
        }
        logger?.info({ leadId, estado: l.fsm_state }, 'lead calificado avisado al CRM');
        return true;
      } catch (e) {
        logger?.warn({ leadId, err: String(e.message || e) }, 'no se pudo avisar al CRM');
        return false;
      }
    },
  };
}

module.exports = { crearNotificadorCRM };
