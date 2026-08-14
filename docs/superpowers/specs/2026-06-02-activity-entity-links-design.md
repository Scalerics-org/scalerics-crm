# Spec: Links clickeables a clientes en panel Actividad

**Fecha:** 2026-06-02
**Estado:** Aprobado

---

## Contexto

El panel Actividad muestra ítems como "Franco Ghizzo · actualizó notas de **Semáforo Automóviles**". El nombre del negocio es texto plano. El usuario quiere poder clickear el nombre para abrir el panel lateral del cliente directamente desde el feed de actividad, sin tener que buscarlo en otra pantalla.

La API `/api/activity` ya devuelve `entity_id`, `entity_type` y `entity_name` por cada ítem. La función `openClientPanel(id)` ya existe y abre el panel en el tab Info (donde está la nota).

---

## Alcance

**Solo frontend.** Sin cambios de backend ni de API.

Afecta: el helper `_actEntityLink` (nuevo) y los lambdas de `_actActionLabels` en `dashboard.py`.

---

## Diseño

### Helper `_actEntityLink(i)`

```js
function _actEntityLink(i) {
  if (!i.entity_name) return '';
  if (i.entity_type === 'lead' && i.entity_id) {
    return '<b><span class="act-entity-link" onclick="openClientPanel('
      + Number(i.entity_id) + ')">' + esc(i.entity_name) + '</span></b>';
  }
  return '<b>' + esc(i.entity_name) + '</b>';
}
```

### CSS

```css
.act-entity-link {
  cursor: pointer;
  color: #38bdf8;
  text-decoration: underline;
  text-decoration-color: rgba(56,189,248,0.35);
}
.act-entity-link:hover { color: #7dd3fc; }
```

### Actualización de `_actActionLabels`

Cada lambda que referencia `i.entity_name` con `<b>` pasa a usar `_actEntityLink(i)`. Ejemplo:

| Antes | Después |
|-------|---------|
| `i.entity_name ? ' de <b>'+esc(i.entity_name)+'</b>' : ''` | `i.entity_name ? ' de ' + _actEntityLink(i) : ''` |

Lambdas afectadas: `status_change`, `note_updated`, `attachment_added`, `call_logged`, `budget_generated`, `budget_sent`, `meeting_scheduled`.

Las lambdas `task_created`, `task_updated`, `task_deleted`, `lead_deleted` y `batch_status` no tienen leads clickeables (son entidades tipo task o acciones masivas) — se dejan como están.

### Comportamiento al clickear

`openClientPanel(id)` ya abre el panel en el tab **Info** por defecto. Las notas del cliente viven en ese tab. No se necesita lógica adicional de tab-switching.

---

## Archivos a modificar

| Archivo | Cambios |
|---------|---------|
| `dashboard.py` | Agregar CSS `.act-entity-link`. Agregar función JS `_actEntityLink(i)`. Actualizar lambdas en `_actActionLabels`. |

---

## Criterios de aceptación

- [ ] El nombre del negocio en actividad aparece en azul con subrayado
- [ ] Clickear el nombre abre el panel lateral del cliente en tab Info
- [ ] Hover cambia el tono de azul
- [ ] Ítems sin `entity_id` o con `entity_type !== 'lead'` siguen mostrando el nombre como texto plano en negrita
- [ ] No hay errores en consola al clickear
