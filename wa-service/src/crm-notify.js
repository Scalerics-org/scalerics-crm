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
