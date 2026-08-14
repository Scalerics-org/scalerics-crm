# Scalerics CRM

CRM y pipeline de generación de leads de **Scalerics**. Junta tres cosas:

1. Un **panel web** (Flask) para gestionar leads, presupuestos, tareas, agenda y WhatsApp.
2. Un **pipeline por CLI** que scrapea Google Maps, genera pitches y demos con IA, y las deploya a Vercel.
3. Un **bot de WhatsApp** (Node, en `bot/`) que corre como servicio aparte.

Base de datos: **SQLite** (`leads.db` por defecto).

## Cómo correrlo

Requiere **Python 3.11+**.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # completar (ver abajo)
python server.py
```

El panel queda en <http://localhost:5000>. `server.py` crea la base y siembra
las plantillas de pitch solo (`init_db` + `seed_pitch_templates`), así que la
primera corrida no necesita ningún paso extra.

### Variables mínimas

Para levantar el panel en local alcanza con el primer bloque del `.env.example`:

| Variable | Para qué |
|----------|----------|
| `SECRET_KEY` | Firma de la sesión de Flask. En local cualquier string largo sirve; en producción tiene que ser secreto y estable. |
| `DB_PATH` | Ruta de la SQLite. Por defecto `leads.db` en la raíz. |
| `REGISTER_CODE` | Código de invitación para `/register`. |

> ⚠️ **Sin `REGISTER_CODE` el registro queda abierto.** El endpoint `/register`
> está exento del login, así que si la variable está vacía cualquiera se puede
> crear una cuenta. Ponerle valor siempre que el CRM sea alcanzable desde fuera.

> ⚠️ `ADMIN_TOKEN` saltea la sesión en cualquier request a `/api/` vía el header
> `x-admin-token`. Tratarlo como credencial de admin.

El resto del `.env.example` habilita funciones opcionales: Resend (mails),
Anthropic (demos y presupuestos con IA), Vercel (deploy de demos), Google
Calendar y Gmail (OAuth), Calendly (webhook), Recall (transcripción de
reuniones) y el bot de WhatsApp.

Algunas dependencias necesitan un paso extra:

- **Playwright** (scraping): `playwright install chromium`
- **WeasyPrint** (PDFs de presupuestos): necesita GTK/Pango a nivel sistema en Windows.

## Pipeline por CLI

```bash
python main.py <subcomando>
```

| Subcomando | Qué hace |
|------------|----------|
| `scrape --query "restaurante Montevideo" [--max 100] [--no-verify-web]` | Raspa Google Maps |
| `scrape-multi --query "bloquera" [--max-per-dept 20] [--workers 3]` | Scrape en paralelo para los 19 departamentos |
| `generate-pitches` | Genera el texto de pitch de WhatsApp por negocio |
| `generate-demos` | Genera las páginas demo con IA |
| `deploy` | Sube las demos a Vercel |
| `run-all --query "..."` | Pipeline completo |
| `dashboard` | Abre el panel en el browser |

> `main.py` **exige `ANTHROPIC_API_KEY`** y aborta si no está. El pipeline lo
> corre Juan; el panel (`server.py`) no tiene esa restricción.

Los logs van a stdout y a `pipeline.log`.

## Setup de OAuth

```bash
python setup_calendar.py    # Google Calendar
python setup_gmail.py       # Gmail
```

Ambos scripts escriben solos `GCAL_TOKEN_RENEWED_AT` / `GMAIL_TOKEN_RENEWED_AT`
en el `.env`, que alimentan el aviso de vencimiento en el panel de tokens.

## Bot de WhatsApp

Servicio aparte, en `bot/`. Requiere **Node 20+**, Postgres y Redis.

```bash
cd bot
npm install
npm run dev        # o npm start
```

Se conecta al CRM vía `BOT_API_URL` y `WA_ACCESS_TOKEN`.

## Estructura

```
server.py            # Entrypoint del panel (lo que usa gunicorn)
dashboard.py         # App Flask: create_app() y run()
main.py              # CLI del pipeline
database.py          # Esquema e init de la SQLite
routes/              # Blueprints: leads, demos, budgets, tasks, calendar,
                     #   calendly, pipeline, tokens, wa
services/            # budget_ai, demo_service, email_service, job_service
scraper.py           # Scraping de Google Maps (Playwright)
demo_generator.py    # Generación de demos con IA
pitch_generator.py   # Generación de pitches
deployer.py          # Deploy de demos a Vercel
bot/                 # Bot de WhatsApp (Node, servicio aparte)
```

## Deploy

`Procfile`, `fly.toml` y `railway.toml` están en el repo.

```
web: gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --threads 4
```

**Un solo worker a propósito** — la SQLite vive en el disco de la instancia. Si
se escala a más workers o más instancias hay que mover la base a Postgres primero.

## Tests

```bash
pytest
```
