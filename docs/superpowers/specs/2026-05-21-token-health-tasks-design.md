# Token Health Panel en Tareas

**Fecha:** 2026-05-21  
**Estado:** Aprobado

## Objetivo

Mostrar en la sección de tareas del dashboard cuánto tiempo falta para que venzan los tokens críticos del sistema (Gmail, Google Calendar, WhatsApp).

## Tokens monitoreados

| Token | Regla de expiración | Fuente del timestamp |
|---|---|---|
| `GMAIL_REFRESH_TOKEN` | 7 días desde renovación (Google testing mode) | `GMAIL_TOKEN_RENEWED_AT` en env |
| `GCAL_REFRESH_TOKEN` | 7 días desde renovación (Google testing mode) | `GCAL_TOKEN_RENEWED_AT` en env |
| `WA_ACCESS_TOKEN` | Fecha en el campo `exp` del JWT | Decodificar el token directamente |
| `VERCEL_TOKEN` | No vence | Mostrar "Permanente" |

## Cómo se guardan los timestamps

`setup_gmail.py` y `setup_calendar.py` ya escriben al `.env`. Se les agrega:
1. Escribir `GMAIL_TOKEN_RENEWED_AT` / `GCAL_TOKEN_RENEWED_AT` (ISO 8601) al `.env`
2. Llamar `railway variables set` via subprocess para sincronizar con Railway

## API

### `GET /api/tokens/status`

Devuelve array de tokens con su estado:

```json
[
  {
    "name": "Gmail",
    "key": "GMAIL_REFRESH_TOKEN",
    "status": "ok",
    "expires_at": "2026-05-28T17:00:00",
    "days_left": 6,
    "label": "Vence en 6 días"
  },
  ...
]
```

`status` puede ser: `ok` (> 3 días), `warning` (1–3 días), `danger` (< 24h o vencido), `permanent` (no vence), `unknown` (sin timestamp).

## UI

Panel fijo encima de la lista de tareas, visible siempre en la pestaña Tareas.

- Título: "Estado del sistema"
- Una card horizontal por token
- Badge de color según status: verde / amarillo / rojo / gris
- Texto: "Vence en X días" / "Vence en Xh" / "Vencido" / "Permanente"

## Archivos modificados

1. `setup_gmail.py` — agregar guardado de `GMAIL_TOKEN_RENEWED_AT`
2. `setup_calendar.py` — agregar guardado de `GCAL_TOKEN_RENEWED_AT`
3. `routes/tokens.py` — nuevo blueprint con `GET /api/tokens/status`
4. `dashboard.py` — registrar blueprint + HTML/JS del panel de tokens en la pestaña de tareas
