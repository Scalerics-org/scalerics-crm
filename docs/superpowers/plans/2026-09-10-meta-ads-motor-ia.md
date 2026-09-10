# Meta Ads: el motor de IA — Plan de implementación (Fase 3 de 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un modelo lea el dossier y escriba hallazgos con evidencia y
recomendaciones, **sin poder inventar un número** — y que eso sea una propiedad
verificable por código, no una promesa del prompt.

**Architecture:** El modelo recibe únicamente el dossier ya calculado. Devuelve
JSON con estructura fija donde cada hallazgo cita ids de métrica. Un validador
rechaza la respuesta si aparece un número que no está en el dossier, si cita un
id inexistente, o si habla de una muestra chica sin advertirlo. Si no valida, se
reintenta una vez; si vuelve a fallar, el snapshot queda con `status` de error y
**el panel muestra los gráficos igual**, porque nunca dependieron del informe.

**Tech Stack:** Python, SDK de Anthropic con import diferido, `claude-opus-5`.

**Spec:** `docs/superpowers/specs/2026-09-10-inteligencia-comercial-meta-design.md` (§10)

## Global Constraints

- **Nace apagado.** Bandera `RADIOGRAFIA_IA_ACTIVA`, default `false`. Con la
  bandera apagada, `POST /api/marketing/generar` calcula y guarda el dossier y
  deja `report_json` en `NULL` con `status='sin_ia'` — o sea, exactamente lo que
  hace hoy.
- **Ningún test toca la API.** Todos contra respuestas simuladas. Un test
  verifica sobre el fuente que el módulo no importa `anthropic` a nivel de
  archivo: son 19,6 MB y la máquina de Fly tiene 256 MB.
- **El modelo nunca ve filas.** Solo el dossier. Además de ser la garantía contra
  la invención, evita mandar datos personales de 238 personas a un servicio
  externo — por eso `por_segmento` ya deja afuera nombre, teléfono y mail.
- **No se publica un informe sin validar.** Nunca.
- **`claude-opus-5`** con `thinking: {"type": "adaptive"}`. El trabajo es
  razonamiento analítico y corre una vez por semana; degradar el modelo para
  ahorrar centavos no tiene sentido acá.
- **Costo medido, no estimado:** el dossier son ~31.000 tokens de entrada. Con
  Opus 5, ~USD 0,31 por corrida, ~USD 1,30 al mes.

---

### Task 1: El dossier que ve el modelo

Antes del prompt hay que decidir qué se le manda. El dossier completo tiene
métricas que no aportan al análisis y sí ocupan lugar.

**Files:**
- Create: `services/radiografia_ia.py`
- Create: `tests/test_radiografia_ia.py`

**Interfaces:**
- Produces:
  - `MODELO = "claude-opus-5"`
  - `ia_activa() -> bool`
  - `preparar(dossier: dict) -> dict` — el dossier podado
  - `indice_de_metricas(dossier: dict) -> dict[str, dict]`

- [ ] **Step 1: Tests**

```python
"""El motor de IA: que ve el modelo y que se le permite decir.

Ningun test toca la API. Todos contra respuestas simuladas.
"""

import io
import json

import pytest

from services.radiografia_ia import (MODELO, ia_activa, indice_de_metricas,
                                     preparar)


def test_el_modulo_no_importa_anthropic_a_nivel_de_archivo():
    """Son 19,6 MB y la maquina de Fly tiene 256 MB. El import va adentro de
    la funcion que lo usa, como en services/budget_ai.py."""
    fuente = io.open("services/radiografia_ia.py", encoding="utf-8").read()
    cabeza = fuente.split("def ")[0]
    assert "import anthropic" not in cabeza


def test_el_modelo_es_opus_5():
    assert MODELO == "claude-opus-5"


def test_nace_apagado(monkeypatch):
    monkeypatch.delenv("RADIOGRAFIA_IA_ACTIVA", raising=False)
    assert ia_activa() is False


def test_se_prende_con_la_bandera(monkeypatch):
    monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", "true")
    assert ia_activa() is True


def test_una_bandera_ambigua_no_prende(monkeypatch):
    """Prender esto cuesta plata: solo un si explicito."""
    for valor in ("", "0", "no", "False", "quizas"):
        monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", valor)
        assert ia_activa() is False, valor


def test_el_indice_encuentra_toda_metrica_del_dossier():
    d = {"campanas": [{"campana": "x", "metricas": [
             {"id": "a", "valor": 1}, {"id": "b", "valor": 2}]}],
         "segmentos": [{"pregunta": "p", "valores": [
             {"valor_declarado": "v", "metricas": [{"id": "c", "valor": 3}]}]}],
         "tiempos": [{"id": "d", "valor": 4}]}
    assert set(indice_de_metricas(d)) == {"a", "b", "c", "d"}


def test_preparar_no_manda_datos_personales():
    """El dossier ya no los trae, pero si alguien los agrega manana esto lo
    frena antes de que salgan hacia un servicio externo."""
    d = {"campanas": [{"campana": "x", "metricas": [
        {"id": "a", "valor": 1, "etiqueta": "Gasto"}]}],
        "leads": [{"nombre": "Ana", "telefono": "+59899"}]}
    salida = json.dumps(preparar(d), ensure_ascii=False)
    assert "Ana" not in salida
    assert "+59899" not in salida


def test_preparar_conserva_lo_que_el_informe_necesita_citar():
    d = {"periodo": {"desde": "2026-03-01", "hasta": "2026-09-30"},
         "campanas": [{"campana": "x", "metricas": [
             {"id": "a", "valor": 0.2, "etiqueta": "Tasa", "n": 50,
              "ic95": [0.1, 0.3], "muestra_chica": False, "fuente": "crm",
              "delta_periodo_anterior": 0.05}]}]}
    m = preparar(d)["campanas"][0]["metricas"][0]
    assert {"id", "etiqueta", "valor", "n", "ic95", "muestra_chica",
            "fuente", "delta_periodo_anterior"} <= set(m)
```

- [ ] **Step 2: Correr y ver fallar**
- [ ] **Step 3: Implementar**
- [ ] **Step 4: Correr los tests**
- [ ] **Step 5: Commit**

---

### Task 2: El validador anti-invención

La pieza que hace que «no inventa» sea verificable y no una promesa.

**Files:**
- Modify: `services/radiografia_ia.py`
- Modify: `tests/test_radiografia_ia.py`

**Interfaces:**
- Produces: `validar(informe: dict, dossier: dict) -> list[str]` — lista de
  problemas; vacía significa que pasa

Las cuatro reglas del spec §10.1:

1. Todo número del texto tiene que existir en el dossier (con tolerancia de
   redondeo).
2. `metricas_citadas` no puede estar vacío ni citar un id inexistente.
3. Un hallazgo que cite una métrica con `muestra_chica` tiene que traer
   `advertencia_muestra: true`.
4. Prohibido afirmar causalidad entre métricas. Esta es advertencia, no fallo.

- [ ] **Step 1: Tests**

```python
def _dossier():
    return {"campanas": [{"campana": "UY", "metricas": [
        {"id": "campana.uy.cpl", "valor": 9.38, "etiqueta": "CPL", "n": None,
         "muestra_chica": False, "fuente": "derivada"},
        {"id": "campana.uy.tasa_demo", "valor": 0.229, "etiqueta": "Tasa demo",
         "n": 83, "muestra_chica": False, "fuente": "crm"},
        {"id": "campana.arg.tasa_demo", "valor": 0.2, "etiqueta": "Tasa demo ARG",
         "n": 5, "muestra_chica": True, "fuente": "crm"}]}]}


def _hallazgo(**kw):
    base = {"titulo": "t", "tipo": "contexto", "cuerpo": "c",
            "metricas_citadas": ["campana.uy.cpl"], "confianza": "alta",
            "recomendacion": "r", "advertencia_muestra": False}
    base.update(kw)
    return {"resumen": "r", "hallazgos": [base], "cambios_desde_la_ultima": []}


def test_un_informe_limpio_pasa():
    assert validar(_hallazgo(cuerpo="El CPL fue de 9,38."), _dossier()) == []


def test_un_numero_inventado_se_rechaza():
    """La regla que sostiene todo el diseno."""
    problemas = validar(_hallazgo(cuerpo="El CPL fue de 47,20."), _dossier())
    assert any("47" in p for p in problemas)


def test_un_id_inexistente_se_rechaza():
    problemas = validar(_hallazgo(metricas_citadas=["campana.inventada.cpl"]),
                        _dossier())
    assert problemas


def test_no_citar_ninguna_metrica_se_rechaza():
    assert validar(_hallazgo(metricas_citadas=[]), _dossier())


def test_muestra_chica_sin_advertencia_se_rechaza():
    """Que la senal sea debil se dice, no se omite."""
    problemas = validar(
        _hallazgo(metricas_citadas=["campana.arg.tasa_demo"],
                  advertencia_muestra=False), _dossier())
    assert problemas


def test_muestra_chica_con_advertencia_pasa():
    assert validar(_hallazgo(metricas_citadas=["campana.arg.tasa_demo"],
                             advertencia_muestra=True), _dossier()) == []


def test_los_numeros_del_periodo_no_cuentan_como_inventados():
    """Un ano o una fecha no es una metrica."""
    d = _dossier()
    d["periodo"] = {"desde": "2026-03-01", "hasta": "2026-09-30"}
    assert validar(_hallazgo(cuerpo="Entre marzo y septiembre de 2026."), d) == []


def test_un_porcentaje_escrito_como_porcentaje_se_reconoce():
    """El dossier guarda 0,229 y el informe escribe 22,9%."""
    assert validar(_hallazgo(metricas_citadas=["campana.uy.tasa_demo"],
                             cuerpo="La tasa de demo fue 22,9%."),
                   _dossier()) == []


def test_la_causalidad_avisa_pero_no_rechaza():
    problemas = validar(
        _hallazgo(cuerpo="El CPL bajo porque se cambio el creativo."),
        _dossier())
    assert any(p.startswith("aviso:") for p in problemas)
    assert not [p for p in problemas if not p.startswith("aviso:")]
```

- [ ] **Step 2: Correr y ver fallar**
- [ ] **Step 3: Implementar el validador**

Detalles que importan al implementar:

- Los números se extraen con una regex que entienda el formato es-UY
  (`1.234,56`) y también el simple (`9.38`).
- Un número que aparece en `periodo`, o que es un año de cuatro dígitos, no
  cuenta: no es una métrica.
- Un porcentaje del texto se compara contra `valor * 100` además de contra
  `valor`, porque el dossier guarda proporciones.
- La tolerancia de redondeo es de una décima sobre el valor formateado.

- [ ] **Step 4: Correr los tests**
- [ ] **Step 5: Commit**

---

### Task 3: La llamada, con reintento

**Files:**
- Modify: `services/radiografia_ia.py`
- Modify: `tests/test_radiografia_ia.py`

**Interfaces:**
- Produces: `redactar(dossier: dict, llamar=None) -> dict` →
  `{"informe": dict|None, "status": str, "error": str|None, "tokens_in": int, "tokens_out": int}`

`llamar` existe para los tests: recibe el prompt y devuelve la respuesta cruda.
En producción se usa el default, que va a la API.

- [ ] **Step 1: Tests**

```python
def test_sin_la_bandera_no_llama(monkeypatch):
    monkeypatch.delenv("RADIOGRAFIA_IA_ACTIVA", raising=False)
    llamadas = []
    r = redactar(_dossier(), llamar=lambda *a, **k: llamadas.append(1))
    assert llamadas == []
    assert r["status"] == "sin_ia"
    assert r["informe"] is None


def test_un_informe_valido_se_devuelve(monkeypatch):
    monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", "true")
    r = redactar(_dossier(),
                 llamar=lambda *a, **k: (_hallazgo(cuerpo="CPL 9,38."), 100, 50))
    assert r["status"] == "ok"
    assert r["informe"]["hallazgos"]
    assert r["tokens_in"] == 100


def test_un_numero_inventado_dispara_un_reintento(monkeypatch):
    """Y el reintento recibe el error concreto, no un 'proba de nuevo'."""
    monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", "true")
    intentos = []

    def _llamar(prompt, correccion=None):
        intentos.append(correccion)
        if len(intentos) == 1:
            return (_hallazgo(cuerpo="CPL 47,20."), 10, 10)
        return (_hallazgo(cuerpo="CPL 9,38."), 10, 10)

    r = redactar(_dossier(), llamar=_llamar)
    assert len(intentos) == 2
    assert intentos[0] is None
    assert "47" in (intentos[1] or ""), "el reintento tiene que decir que fallo"
    assert r["status"] == "ok"


def test_dos_fallos_seguidos_no_publican_nada(monkeypatch):
    """Nunca se publica un informe sin validar."""
    monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", "true")
    r = redactar(_dossier(),
                 llamar=lambda *a, **k: (_hallazgo(cuerpo="CPL 47,20."), 10, 10))
    assert r["status"] == "error_validacion"
    assert r["informe"] is None
    assert "47" in r["error"]


def test_un_error_de_la_api_no_tumba_la_corrida(monkeypatch):
    monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", "true")

    def _explota(*a, **k):
        raise RuntimeError("503 del proveedor")

    r = redactar(_dossier(), llamar=_explota)
    assert r["status"] == "error_ia"
    assert r["informe"] is None
```

- [ ] **Step 2: Correr y ver fallar**
- [ ] **Step 3: Implementar**
- [ ] **Step 4: Correr los tests**
- [ ] **Step 5: Commit**

---

### Task 4: Encadenarlo y el cron semanal

**Files:**
- Modify: `routes/marketing.py`
- Modify: `services/radiografia.py` — `guardar_snapshot` acepta el informe
- Create: `.github/workflows/radiografia.yml`
- Modify: `tests/test_marketing_rutas.py`

- [ ] **Step 1: Tests**

```python
def test_generar_sin_ia_sigue_guardando_el_dossier(app, cliente, monkeypatch):
    """El panel nunca dependio del informe: los graficos salen del dossier."""
    monkeypatch.delenv("RADIOGRAFIA_IA_ACTIVA", raising=False)
    r = cliente.post("/api/marketing/generar", headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json()["status"] == "sin_ia"


def test_el_workflow_del_cron_nace_con_el_schedule_comentado():
    """No se prende solo: se descomenta el dia que se decida gastar."""
    import io
    y = io.open(".github/workflows/radiografia.yml", encoding="utf-8").read()
    assert "workflow_dispatch" in y
    for linea in y.splitlines():
        limpia = linea.strip()
        if limpia.startswith("- cron:"):
            raise AssertionError("el schedule esta activo: " + limpia)
```

- [ ] **Step 2: Correr y ver fallar**
- [ ] **Step 3: Implementar**

El workflow copia el patrón probado de `linkedin.yml`: `concurrency` para que dos
corridas no se solapen, espera a que el CRM conteste antes de pegarle,
`--retry-all-errors` en los 5xx pero no en los 4xx, y aviso por mail si falla.
El `schedule` va **comentado**.

- [ ] **Step 4: Correr los tests**
- [ ] **Step 5: Suite completa**
- [ ] **Step 6: Commit y anotar en `COORDINACION.md`**

---

## Cuando termine esta fase

El módulo queda completo y apagado. Para prenderlo hacen falta tres cosas, en
este orden:

1. Que haya presupuesto de API.
2. `flyctl secrets set RADIOGRAFIA_IA_ACTIVA=true`.
3. Descomentar el `schedule` en el workflow.

Y antes de todo eso, lo de la Fase 1: sin `META_ADS_TOKEN` el informe va a poder
hablar de tasas de interés y de demo, pero no de plata.
