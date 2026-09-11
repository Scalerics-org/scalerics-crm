# Las claves de Anthropic: una por proyecto

**Estado al 11/9/2026: hay UNA sola clave repartida en todos lados.** Por eso el
panel de gasto de la consola es un número solo y no se puede saber quién gastó
qué. Esto documenta dónde vive hoy y cómo separarla.

## Dónde está la misma clave hoy

Verificado el 11/9/2026 comparando hashes, sin leer los valores. Las tres copias
locales y las dos apps de Fly tienen **exactamente la misma clave** (termina en
`...KwAA`, digest de Fly `33f4e3f92c18200d`).

| Dónde | Qué es | De quién es el gasto |
|---|---|---|
| Fly `scalerics-crm` | el CRM: presupuestos con IA, resumen de reuniones, demos | Scalerics |
| Fly `scalerics-wa` | el bot de WhatsApp | Scalerics |
| `crm-jose/.env` | **la bloquera La Cadena** — `src/bot/aiResponder.js` contesta a los clientes de Jose con `claude-haiku-4-5` | **un cliente** |
| `crm-limpio/.env` | desarrollo local | Scalerics |
| `respaldo-lead-gen-uy/.env` | respaldo viejo | nadie, debería salir |

El caso que importa es el tercero: **la operación de un cliente corre con la
clave de Scalerics.** Eso no es solo un problema de contabilidad; si esa clave se
filtra desde el lado del cliente, el que paga es Scalerics.

## Cómo separarlo

No alcanza con crear dos claves sueltas: dos claves del mismo workspace suman en
el mismo total. Lo que separa el gasto de verdad son los **workspaces** de
`console.anthropic.com`, porque cada uno tiene su propio reporte de uso y, lo más
importante, **su propio límite de gasto**.

1. **Crear un workspace por proyecto**: `Scalerics` y `Bloquera La Cadena`.
2. **Ponerle límite de gasto a cada uno.** Es lo único que convierte una sorpresa
   en un tope. Al de la bloquera, un límite bajo: es un bot de consultas.
3. **Una clave por workspace**, y cargarla donde corresponda:
   - Scalerics → `python scripts/cargar_clave_anthropic.py` (carga las dos apps
     de Fly de una)
   - Bloquera → en el `.env` de `crm-jose`, donde sea que corra hoy
4. **Revocar la clave vieja** recién cuando las dos estén andando. Mientras siga
   viva, sigue habiendo gasto que no se puede atribuir.

## Qué se sabe del gasto de un día suelto

Nada, y ese es el punto. El 10/9/2026 hubo USD 2,10 y no se puede decir de quién
fue. Lo que **sí** se pudo descartar con evidencia:

- **No fue el módulo de marketing**: la tabla `radiografias` tenía 0 filas, o sea
  que nunca llamó a la API. Nace apagado y siguió apagado.
- **No fue Claude Code**: en esa máquina está autenticado por suscripción
  (`billingType: stripe_subscription`), no con API key. Ojo con un matiz:
  `hasExtraUsageEnabled` está en `true`, así que el excedente de la suscripción
  se cobra aparte y eso no se distingue desde afuera de la consola.

Los candidatos que quedan son los dos bots y la generación de presupuestos y
demos. Cuál de ellos fue solo se va a poder responder **después** de separar.
