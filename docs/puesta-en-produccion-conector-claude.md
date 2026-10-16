# Puesta en producción — conector del CRM para Claude

El CRM expone un servidor MCP en `https://scalerics-crm.fly.dev/mcp`. Cada
persona del equipo lo agrega en claude.ai y entra con su usuario del CRM.
Claude solo puede **leer** leads y pipeline; cada consulta queda registrada
con el nombre de quien la hizo.

## Qué tiene que estar configurado en Fly

Una sola variable, que ya existe para los mails: `CRM_URL`. Tiene que valer
exactamente `https://scalerics-crm.fly.dev`, sin barra final. Es la URL que
el CRM anuncia como emisor OAuth y como recurso protegido; si no coincide con
la que la gente escribe en claude.ai, claude.ai rechaza la conexión.

Comprobar:

```bash
flyctl secrets list -a scalerics-crm | grep CRM_URL
```

Si no está, `flyctl secrets set CRM_URL=https://scalerics-crm.fly.dev -a scalerics-crm`.

Después del deploy, estas dos URLs tienen que devolver JSON sin pedir login:

- https://scalerics-crm.fly.dev/.well-known/oauth-authorization-server
- https://scalerics-crm.fly.dev/.well-known/oauth-protected-resource/mcp

## Cómo se agrega en claude.ai (plan Team)

Solo un **Owner** de la organización puede agregar conectores personalizados.

1. Configuración → Conectores → **Agregar conector personalizado**.
2. Nombre: `CRM Scalerics`. URL: `https://scalerics-crm.fly.dev/mcp`.
3. Identidad OAuth: dejar la opción recomendada ("identidad publicada de
   Claude"). No hace falta client ID ni secret.
4. Guardar. Elegir "Iniciar sesión ahora".
5. Se abre el login del CRM. Entrar con el usuario propio. En la pantalla
   siguiente, **Autorizar**.

Una vez que el Owner lo agregó, cada miembro del equipo lo ve en Conectores y
conecta con su propio usuario del CRM: la autorización es por persona.

## Quién puede usarlo

Cualquier usuario del CRM, salvo el rol "Vendedor Fidelidad" (igual que en la
API web). No hace falta ningún panel en el rol.

## Qué puede hacer Claude

| Herramienta | Qué hace |
|---|---|
| `buscar_leads` | Busca por nombre, email o teléfono |
| `ver_lead` | Ficha completa, relevamiento, responsables, últimos eventos y llamadas |
| `pipeline` | Leads en seguimiento agrupados por etapa, filtrables por etapa y responsable |

Nada de escritura. Las finanzas, tareas y agenda no están expuestas.

## Revocar el acceso de alguien

Desde el CRM, con un usuario admin: **Usuarios** (el engranaje del menú, o
`/admin/users`). Cada persona muestra `Claude: N`, la cantidad de conexiones de
Claude vivas, y al lado el botón **Cortar acceso de Claude**. El botón revoca
todos sus tokens y borra los códigos pendientes; la acción queda en el registro
de actividad. Si la persona quiere volver a usarlo, Claude le pide que autorice
de nuevo. Con `N` en 0 el botón sigue activo: borra igual una autorización que
haya quedado a medias.

Cambiar la contraseña de alguien también le corta el acceso a Claude, y aunque
nadie haga nada, una conexión dura como mucho 90 días desde que se autorizó:
después Claude vuelve a pedir el consentimiento.

Alternativa, si el CRM no responde, desde la consola de Fly:

```bash
flyctl ssh console -a scalerics-crm -C "sqlite3 /data/leads.db \"UPDATE oauth_tokens SET revocado_en = datetime('now') WHERE user_id = (SELECT id FROM users WHERE email = 'persona@scalerics.com') AND revocado_en IS NULL\""
```

Si la persona se borra del CRM, sus tokens dejan de valer en el acto.

## Ver qué consultó Claude

```bash
flyctl ssh console -a scalerics-crm -C "sqlite3 -header /data/leads.db \"SELECT created_at, user_name, herramienta, argumentos, ok, ms FROM mcp_llamadas ORDER BY id DESC LIMIT 50\""
```

## Si algo falla

- "Couldn't reach the MCP server" al agregarlo: las URLs de `/.well-known/`
  no responden o `CRM_URL` no coincide.
- Pide login y después de autorizar vuelve a pedirlo: el reloj de la máquina o
  un `CRM_URL` con barra final. Mirar los logs de Fly en el momento del
  `POST /oauth/token`.
- Claude dice que no está conectado aunque autorizó: desconectar y volver a
  conectar desde Conectores. Si sigue, mirar `oauth_tokens` por `user_id`.
