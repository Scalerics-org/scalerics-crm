# Fase 1 — Configurar el correo

Paso a paso de la Fase 1 (días 1-3). Todo pasa en paneles externos: Zoho, Resend y Cloudflare.

**El objetivo de la fase:** que `contacto@scalerics.com` reciba como siempre, y que los **dos
caminos de salida** — Juan a mano por Zoho, el agente por Resend — autentiquen los dos en PASS.

**Los MX de la raíz no se tocan en ningún paso.** Si en algún momento una guía te pide
cambiarlos, parate: no es esta arquitectura.

---

## Lo que ya está hecho

| Entregable | Archivo |
|---|---|
| Assets de logo | `assets/` — 4 archivos, todos bajo 20 KB |
| Generador de firmas | `firma-scalerics.html` |
| Esquema de la planilla (Fase 2) | `planilla-pipeline.xlsx` — hojas `Leads` (19 col) y `Log` (9) |

**El vector no hace falta.** El plan asumía que el logo salía del `og-image.png` de 82 px de
alto, pero `static/logo.png` en este repo es un PNG de **3802×748 con transparencia**. De ahí
salió el `logo-firma.png` de 492×96, que se muestra a 246×48 — o sea 2× real en retina.

| Archivo | Medidas | Peso |
|---|---|---|
| `logo-firma.png` | 492×96 | 3,4 KB |
| `logo-scalerics-horizontal.png` | 420×82 | 3,0 KB |
| `logo-scalerics-horizontal-blanco.png` | 420×82 | 8,5 KB |
| `logo-scalerics-isotipo.png` | 80×82 | 4,3 KB |

---

## 1. Zoho Mail Lite

Pasar de Free a **Mail Lite**, 1 usuario, **facturación anual** (USD 1/usuario/mes solo en anual;
mes a mes cuesta más y no tiene sentido para un buzón). Son USD 12 al año.

Lo que comprás con eso es concreto: **IMAP y alias**. Sin IMAP el agente no puede detectar
respuestas y le seguiría escribiendo a alguien que ya te contestó.

- [ ] Upgrade a Mail Lite, 1 usuario, anual
- [ ] **Activar IMAP** — Configuración → Correo → IMAP
- [ ] Generar una **contraseña de aplicación** para el servicio
- [ ] Crear los alias `juan@`, `hola@` y `dmarc@` — los tres caen en `contacto@`
- [ ] **No crear `no-reply@`**
- [ ] Crear la carpeta IMAP `Seguimiento`

> La contraseña de aplicación no es la contraseña de la cuenta. Es un token revocable: si el
> servicio se compromete, la revocás sin cambiar la clave de Juan ni echar a nadie de la casilla.
> Va al `.env`, nunca al código.

---

## 2. DKIM de Zoho

Firma lo que Juan manda **a mano desde el webmail**. Es el camino A.

- [ ] Panel de Zoho → Dominios → DKIM → generar con selector `zoho`
- [ ] Copiar el TXT que te da y publicarlo en Cloudflare (paso 4)
- [ ] Volver a Zoho y darle **Verificar**

---

## 3. Resend

Firma lo que manda **el agente**. Es el camino B.

- [ ] Crear la cuenta y agregar el dominio `scalerics.com`
- [ ] Resend te devuelve **tres registros**: el DKIM en `resend._domainkey`, y un TXT y un MX
      en el subdominio `send`
- [ ] Publicarlos en Cloudflare (paso 4), volver y darle **Verify**
- [ ] Guardar la API key en el `.env` del servicio

**El techo del plan gratis son 3.000 mails al mes y 100 por día.** Con seguimiento a leads
reales no vas a pasar de 20 o 30 diarios. El límite que te va a apretar antes es tu propia
reputación, no el del proveedor.

---

## 4. Cloudflare

| Tipo | Nombre | Valor | Nota |
|---|---|---|---|
| MX | `@` | `mx.zoho.com` / `mx2.zoho.com` / `mx3.zoho.com` | **Ya está. No tocar** |
| TXT | `@` | `v=spf1 include:zohomail.com ~all` | **Ya está. Uno solo, nunca dos** |
| TXT | `zoho._domainkey` | (lo da Zoho) | Camino A |
| TXT | `resend._domainkey` | (lo da Resend) | Camino B |
| TXT | `send` | (lo da Resend) | SPF del subdominio de retorno |
| MX | `send` | (lo da Resend) | Por donde vuelven los rebotes |
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:dmarc@scalerics.com; pct=100` | Reportes |

### Los cuatro errores que rompen esto

**1. Dos registros SPF en la raíz.** SPF admite **uno solo**. Si agregás el de Resend al lado
del de Zoho, fallan los dos y **todo tu correo empieza a caer en spam**. El de Resend va en
`send`, nunca en `@`. Este es el error caro.

**2. Escribir el nombre completo.** Cloudflare **agrega el dominio solo**. Si en el campo
Nombre escribís `send.scalerics.com`, te queda `send.scalerics.com.scalerics.com`. Poné
`send` y `resend._domainkey`, a secas.

**3. Dos registros DMARC.** Ya tenés uno. **Editalo**, no agregues otro — con dos, DMARC no
evalúa ninguno.

**4. La nube naranja.** Cualquier registro de correo va en **DNS only** (nube gris). Proxear
correo no funciona.

---

## 5. Verificar los tres caminos

Esperá 10-15 minutos y confirmá que los registros salieron:

```powershell
Resolve-DnsName scalerics.com -Type MX; Resolve-DnsName scalerics.com -Type TXT; Resolve-DnsName _dmarc.scalerics.com -Type TXT; Resolve-DnsName send.scalerics.com -Type TXT; Resolve-DnsName resend._domainkey.scalerics.com -Type TXT
```

Buscá: los MX siguen en Zoho, **un solo** `v=spf1` en la raíz, y **un solo** `v=DMARC1`.

Ahora los tres caminos, **cada uno por separado**:

| | Camino A | Camino B | Camino C |
|---|---|---|---|
| Qué prueba | Juan manda a mano | El agente manda | Entra correo |
| Cómo | Mail desde el webmail de Zoho | Un envío de prueba por la API de Resend | Mandarte uno a `contacto@` y otro a `hola@` |
| Firma DKIM | `zoho._domainkey` | `resend._domainkey` | — |

- [ ] **A y B a [mail-tester.com](https://www.mail-tester.com) — no salgas con menos de 9/10 en ninguno**
- [ ] A y B a tu Gmail → "Mostrar original" → SPF, DKIM y DMARC los tres en **PASS**
- [ ] C: llegan los dos, y el de `hola@` cae en `contacto@`

Si B da menos de 9, el motivo casi siempre es el `List-Unsubscribe` faltante o el dominio sin
verificar del todo en Resend. **No sigas hasta que A y B den 9+.** Todo lo que viene después
asume que estos dos autentican.

---

## 6. El logo y las firmas

El `src` de la firma tiene que ser público y estable. **La imagen no viaja en el mail**: se
descarga de tu servidor cada vez que alguien lo abre. Si la movés, cada mail ya enviado queda
con un cuadrado roto.

- [ ] Subir `assets/logo-firma.png` a `https://scalerics.com/logo-firma.png`
- [ ] **Abrirlo en una ventana de incógnito.** Si no carga ahí, no carga en el mail de nadie
- [ ] Abrir `firma-scalerics.html`, completar los datos de cada uno y copiar

Dos usos distintos del generador:

| Botón | Para qué | Quién |
|---|---|---|
| Copiar para pegar en Zoho Mail | Firma del webmail (Configuración → Firmas) | Los 5 |
| Copiar HTML crudo | La constante `FIRMA_HTML` del servicio | **Solo Juan** |

**Me falta el teléfono de Juan y su link de Calendly** para dejar su firma armada. El resto
(nombre, cargo, web, ubicación) ya está cargado como valor por defecto en el generador.

> El modelo nunca escribe la firma. Se concatena aparte, así el teléfono y el cargo están
> siempre bien sin depender de que el modelo los recuerde.

---

## 7. Calentamiento

**Esta es la ruta crítica de todo el proyecto.** Son cuatro semanas de calendario que no se
aceleran con plata ni con esfuerzo. Arrancalo el mismo día que A y B den PASS.

| Semana | Máximo diario desde `contacto@` |
|---|---|
| 1 | 5-10, a conocidos que **te respondan** |
| 2 | 15-20 |
| 3 | 30-40 |
| 4+ | Hasta 100 |

Que te respondan no es un detalle: una respuesta es la señal más fuerte que tienen Gmail y
Outlook para decidir que sos una persona y no una campaña.

Corre en paralelo a las fases 2 y 3 — no las bloquea, pero **sí bloquea el envío en volumen**.
El dry-run de la Fase 3 no manda nada, así que las dos cosas conviven sin problema.

---

## Checklist

```
[x] Assets de logo
[x] Generador de firmas
[x] Esquema de la planilla (se usa en Fase 2)
[ ] 1. Zoho Mail Lite anual + IMAP + contraseña de app + 3 alias + carpeta Seguimiento
[ ] 2. DKIM de Zoho publicado y verificado
[ ] 3. Resend: dominio agregado, API key en .env
[ ] 4. Cloudflare: 3 registros de Resend + DMARC actualizado. SPF y MX de la raíz intactos
[ ] 5. Camino A 9+/10 · Camino B 9+/10 · Camino C entra a contacto@ y hola@
[ ] 6. logo-firma.png público y verificado en incógnito + firmas generadas
        (bloqueado: falta teléfono de Juan y link de Calendly)
[ ] 7. Calentamiento arrancado — 4 semanas, en paralelo a Fase 2 y 3
```

**La Fase 2 arranca apenas estén los pasos 1 y 3.** No esperes a terminar el calentamiento
para empezar a construir el servicio.
