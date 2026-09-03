# Reporte de evals — bot de WhatsApp Scalerics

Modelo: `anthropic:claude-haiku-4-5-20251001`  ·  Casos: 1  ·  CAL_LINK: https://calendly.com/scalerics/consultoriagratuita

**0/1 casos sin fallas graves.** 2 fallas graves en total.

| Caso | Graves | Leves | Cierre | Link |
|---|---|---|---|---|
| ya-te-lo-dije | 2 | — | turno 2 | — |

## ya-te-lo-dije
_paso de verdad: el lead dio el tipo de proyecto en el primer mensaje y el rubro en el segundo, y el bot pregunto tres veces lo mismo hasta que le contestaron "ya te lo dije"_

- **grave** `dato_mal`: team_size quedó null, se esperaba 2
- **grave** `campo_inexistente`: intentó guardar "team_size"

<details><summary>conversación</summary>

**[descubrimiento] lead:** buenass, estoy buscando para hacer una pagina web
**bot:** Buenas. Perfecto, eso lo hacemos. Primero contame: ¿cómo se llama tu negocio?
`guardó: {"business_type":1}`

**[descubrimiento] lead:** Tengo una panaderia, se llama PanesAhora
**bot:** Con eso tenemos todo. Te hago una llamada de 30 minutos para entender mejor qué necesitás y te preparo un prototipo para que veas cómo quedaría. ¿Cuándo te viene bien?
`guardó: {"business_name":"PanesAhora","rubro":"panadería","business_type":1}`

**[cierre] lead:** somos 3 con mi hermano y mi vieja
**bot:** Perfecto. Te propongo que coordinemos la llamada para esta semana o la próxima, ¿te viene bien algún día en particular?
`guardó: {"business_type":1,"team_size":2}`

</details>

Lead final: `{"business_type":1,"business_name":"PanesAhora","rubro":"panadería"}`
