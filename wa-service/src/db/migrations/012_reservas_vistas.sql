-- Reservas del calendario que ya se registraron.
--
-- Sin esto, el vigilante puede volver a registrar la misma reunion y volver a
-- avisarle al equipo. Paso: un lead con DOS reuniones agendadas hacia que las
-- dos se pisaran entre si —cada una veia el id de la otra y se registraba de
-- nuevo— y salio un aviso cada cinco minutos durante horas.
--
-- La clave es el evento MAS su hora de inicio: si la reunion se mueve, es una
-- reserva nueva y hay que avisar.
CREATE TABLE IF NOT EXISTS reservas_vistas (
  clave    TEXT PRIMARY KEY,
  vista_at TEXT NOT NULL DEFAULT (datetime('now'))
);
