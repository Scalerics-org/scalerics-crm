"""Colores en Flujos y Horarios (pedido de Juan, 15/9).

"El color de flujos debe ser estratégico y arriba aparezcan roles asociados al
color. Project manager en naranja y 'se hace la demo' se pinta en naranja
también el fondo. 'Se genera el lead' en rojo y Líder marketing digital en
rojo como referencia."

Flujos: un color por rol, definido en `services/flujos.ROL_ESTILOS`, con un
token pleno y un tinte por tema. Horarios: cada persona con el color que ya
tiene en Daily.
"""

import json
import re

import pytest

from services.flujos import ROL_ESTILOS, ROLES, estilos_roles, validar_paso
from tests.test_equipo import HTML, SRC, _correr_js, _entre, app, cli, sin_node  # noqa: F401
from tests.test_tokens_css import _contraste, _delta_e, _tokens

ESPERADO = {"Marketing": "rojo", "Project manager": "naranja", "Comercial": "verde",
            "Desarrollo": "azul", "Administración": "violeta", "Soporte": "teal"}
COLORES = sorted(set(ESPERADO.values()))


@pytest.fixture(scope="module")
def oscuro():
    return _tokens(":root")


@pytest.fixture(scope="module")
def claro():
    return _tokens("body.light")


def _regla(selector):
    m = re.search(r"^" + re.escape(selector) + r"\{([^}]*)\}", HTML, re.M)
    assert m, f"no está la regla {selector}"
    return m.group(1)


# ── Flujos: el mapa ──────────────────────────────────────────────────────────

def test_el_mapa_de_colores_por_rol_esta_completo_y_no_repite():
    assert list(ROL_ESTILOS) == list(ROLES), "cada rol de la lista cerrada tiene su estilo"
    assert {r: e["color"] for r, e in ROL_ESTILOS.items()} == ESPERADO
    colores = [e["color"] for e in ROL_ESTILOS.values()]
    assert len(set(colores)) == len(colores), "dos roles con el mismo color"
    assert [e["rol"] for e in estilos_roles()] == list(ROLES)


def test_marketing_se_muestra_como_lider_marketing_digital(cli):
    assert ROL_ESTILOS["Marketing"]["etiqueta"] == "Líder marketing digital"
    assert all(e["etiqueta"] == r for r, e in ROL_ESTILOS.items() if r != "Marketing")
    campos, error = validar_paso({"titulo": "x", "rol": "Marketing"})
    assert error is None and campos["rol"] == "Marketing", "en la base sigue siendo Marketing"

    d = cli.get("/api/flujos").get_json()
    assert d["estilos"] == estilos_roles()
    assert {"rol": "Marketing", "color": "rojo", "etiqueta": "Líder marketing digital"} in d["estilos"]
    lead = next(f for f in d["flujos"] if f["nombre"] == "De lead a cobro")
    assert lead["pasos"][0]["rol"] == "Marketing"


# ── Flujos: tokens y CSS ─────────────────────────────────────────────────────

@pytest.mark.parametrize("color", COLORES)
def test_cada_color_tiene_sus_tokens_y_su_clase(oscuro, claro, color):
    for tema in (oscuro, claro):
        assert f"--rol-{color}" in tema and f"--rol-{color}-tinte" in tema
    assert f".eq-rol-{color}{{--rol-c:var(--rol-{color});--rol-t:var(--rol-{color}-tinte)}}" in HTML


@pytest.mark.parametrize("tema", ["oscuro", "claro"])
def test_los_colores_de_rol_se_leen_en_los_dos_temas(oscuro, claro, tema):
    t = oscuro if tema == "oscuro" else claro
    for color in COLORES:
        pleno, tinte = t[f"--rol-{color}"], t[f"--rol-{color}-tinte"]
        for texto, fondo, que in ((pleno, tinte, "el rol sobre su tinte"),
                                  (t["--texto-fuerte"], tinte, "el título sobre el tinte"),
                                  (t["--texto-tenue"], tinte, "el detalle sobre el tinte"),
                                  (pleno, t["--superficie"], "el rol sobre la superficie")):
            c = _contraste(texto, fondo)
            assert c >= 4.5, f"{color}: {que} en {tema} da {c:.2f}:1"
        assert _delta_e(tinte, t["--superficie"]) >= 4, f"el tinte {color} no se despega en {tema}"
    plenos = [t[f"--rol-{c}"] for c in COLORES]
    tintes = [t[f"--rol-{c}-tinte"] for c in COLORES]
    assert min(_delta_e(a, b) for i, a in enumerate(plenos) for b in plenos[i + 1:]) >= 10
    assert min(_delta_e(a, b) for i, a in enumerate(tintes) for b in tintes[i + 1:]) >= 4


def test_las_tarjetas_de_paso_toman_el_color_de_su_rol():
    paso = _regla(".eq-paso")
    assert "background:var(--rol-t," in paso and "border-left:4px solid var(--rol-c," in paso
    assert "var(--rol-c" in _regla(".eq-paso-rol") and "var(--rol-c" in _regla(".eq-paso::before")
    assert not re.search(r"^\.eq-paso-destacado[^{]*\{[^}]*verde", HTML, re.M), "el destacado ya no es verde"
    for sel in (".eq-paso", ".eq-paso::before", ".eq-paso-rol", ".eq-rol-chip", ".eq-rol-punto",
                ".eq-paso-recurrente", ".eq-flujo-leyenda"):
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", _regla(sel)), sel
    for sel in (".eq-paso-num", ".eq-paso-detalle", ".eq-paso-cobros"):
        assert "var(--texto-tenue)" in _regla(sel), f"{sel}: --texto-debil no llega a 4,5 sobre los tintes claros"


# ── Flujos: se pinta ─────────────────────────────────────────────────────────

@sin_node
def test_el_flujo_se_pinta_con_el_color_de_cada_rol(cli, tmp_path):
    antes = cli.get("/api/flujos").get_json()["flujos"]
    cobranza = next(f for f in antes if f["nombre"] == "Cobranza")
    r = cli.post(f"/api/flujos/{cobranza['id']}/pasos",
                 json={"titulo": "Se revisa lo pendiente", "rol": "Administración"})
    assert r.status_code == 201
    datos = cli.get("/api/flujos").get_json()
    por_nombre = {f["nombre"]: f for f in datos["flujos"]}
    lead = por_nombre["De lead a cobro"]
    genera = next(p for p in lead["pasos"] if p["titulo"] == "Se genera el lead")

    prueba = """
(async () => {
  await eqCargarFlujos();
  const s = {lead: _el('eq-flujos-pasos').innerHTML};
  eqFlujoElegir(__COBRANZA__);
  s.cobranza = _el('eq-flujos-pasos').innerHTML;
  eqFlujoElegir(__VACIO__);
  s.vacio = _el('eq-flujos-pasos').innerHTML;
  eqPasoAbrir(__LEAD__, __GENERA__);
  s.opciones = _el('eq-paso-rol').innerHTML;
  s.muestra = _el('eq-paso-rol-muestra').innerHTML;
  _el('eq-paso-rol').value = 'Project manager';
  eqPasoRolMuestra();
  s.muestraPm = _el('eq-paso-rol-muestra').innerHTML;
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__COBRANZA__", str(cobranza["id"])).replace(
        "__VACIO__", str(por_nombre["Arranque de proyecto"]["id"])).replace(
        "__LEAD__", str(lead["id"])).replace("__GENERA__", str(genera["id"]))
    s = _correr_js(tmp_path, {"/api/flujos": datos}, prueba)

    html = s["lead"]
    leyenda = html[:html.index('<ol class="eq-pasos">')]
    assert leyenda.startswith('<div class="eq-flujo-leyenda"')
    chips = re.findall(r'<span class="eq-rol-chip eq-rol-(\w+)"><i class="eq-rol-punto" aria-hidden="true"></i>'
                       r'([^<]+)</span>', leyenda)
    assert chips == [("rojo", "Líder marketing digital"), ("verde", "Comercial"), ("naranja", "Project manager"),
                     ("azul", "Desarrollo"), ("violeta", "Administración"), ("teal", "Soporte")]

    def li(titulo):
        return next(x for x in html.split("<li ")[1:] if f'<span class="eq-paso-titulo">{titulo}</span>' in x)

    assert li("Se genera el lead").startswith('class="eq-paso eq-rol-rojo')
    assert '<span class="eq-paso-rol">Líder marketing digital</span>' in li("Se genera el lead")
    for titulo in ("Se prepara la demo", "Se hace la demo"):
        assert li(titulo).startswith('class="eq-paso eq-rol-naranja'), titulo
        assert '<span class="eq-paso-rol">Project manager</span>' in li(titulo)
    mantiene = li("Se mantiene")
    assert mantiene.startswith('class="eq-paso eq-rol-teal eq-paso-destacado')
    assert '<span class="eq-paso-recurrente">Ingreso recurrente</span>' in mantiene
    assert html.count("Ingreso recurrente") == 1
    assert ">Marketing<" not in html, "Marketing se muestra con su etiqueta"

    cob = s["cobranza"]
    assert re.findall(r'class="eq-rol-chip eq-rol-(\w+)"', cob[:cob.index("<ol")]) == ["violeta"], \
        "la leyenda muestra solo los roles del flujo elegido"
    assert "eq-flujo-leyenda" not in s["vacio"] and "todavía no se cargaron" in s["vacio"]

    assert '<option value="Marketing" selected>Líder marketing digital</option>' in s["opciones"]
    assert "eq-rol-rojo" in s["muestra"] and "Líder marketing digital" in s["muestra"]
    assert "eq-rol-naranja" in s["muestraPm"] and "Project manager" in s["muestraPm"]


# ── Horarios: el color de Daily ──────────────────────────────────────────────

def test_horarios_manda_el_lugar_de_cada_persona_en_daily(cli):
    horarios = cli.get("/api/horarios").get_json()
    r = cli.get("/api/daily/personas?seccion=programador")
    assert r.status_code == 200
    ids_daily = [p["id"] for p in r.get_json()["personas"]]
    assert horarios["personas"]
    for p in horarios["personas"]:
        esperado = ids_daily.index(p["id"]) if p["id"] in ids_daily else None
        assert p["orden_daily"] == esperado, p["nombre"]
    assert any(p["orden_daily"] is not None for p in horarios["personas"]), "Juan y Gonzalo están en Daily"


def test_los_colores_de_horarios_son_los_de_daily():
    """Horarios no tiene un mapa propio: usa DY_COLORES y, por cada lugar, el
    mismo token que el ícono de Daily."""
    n = int(re.search(r"const DY_COLORES = (\d+);", HTML).group(1))
    for i in range(n):
        dy = re.search(r"^\.dy-nav-icono\.dy-color-" + str(i) + r"\{stroke:var\((--[\w-]+)\)\}", HTML, re.M)
        hr = re.search(r"^\.hr-color-" + str(i) + r"\{--hr-c:var\((--[\w-]+)\);--hr-t:var\((--[\w-]+)\)\}",
                       HTML, re.M)
        assert dy and hr, i
        assert hr.group(1) == dy.group(1), f"el color {i} de Horarios no es el de Daily"
    assert not re.search(r"^\.hr-color-" + str(n) + r"\{", HTML, re.M)
    js = _entre(SRC, "// ========== Horarios ==========", "// ========== FIN Horarios ==========")
    assert "DY_COLORES" in js and "HR_COLORES" not in js


def test_horarios_tiene_pastillas_chips_y_encabezados_alternados():
    assert re.search(r"^\.hr-pastilla\{[^}]*background:var\(--hr-t,[^}]*border:1px solid var\(--hr-c,", HTML, re.M)
    assert re.search(r"^\.hr-total-chip\{[^}]*var\(--hr-c", HTML, re.M)
    assert re.search(r"^\.hr-tabla th\.hr-col-dia:nth-child\(even\)\{background:var\(--relleno\)", HTML, re.M)
    assert re.search(r"^\.hr-tarjeta\{[^}]*border-top:3px solid var\(--hr-c,", HTML, re.M)
    assert re.search(r"^\.hr-celda-libre\{[^}]*opacity:", HTML, re.M)


@sin_node
def test_horarios_pinta_a_cada_persona_con_su_color_de_daily(cli, tmp_path):
    horarios = cli.get("/api/horarios").get_json()
    daily = cli.get("/api/daily/personas?seccion=programador").get_json()["personas"]
    prueba = """
(async () => {
  DY_EST.programador.personas = __DAILY__;
  await hrCargar();
  const s = {html: _el('hr-contenido').innerHTML, pares: []};
  (__HORARIOS__).personas.forEach(p => s.pares.push([p.nombre_corto, hrColor(p), dyColor('programador', p.id), p.orden_daily]));
  s.fuera = [hrColor({id: 9, orden_daily: null}), hrColor({id: 9, orden_daily: null}), hrColor({id: 10})];
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__DAILY__", json.dumps(daily, ensure_ascii=False)).replace(
        "__HORARIOS__", json.dumps(horarios, ensure_ascii=False))
    s = _correr_js(tmp_path, {"/api/horarios": horarios}, prueba)

    en_daily = [par for par in s["pares"] if par[3] is not None]
    assert en_daily
    for nombre, hr, dy, _orden in en_daily:
        assert hr == dy, f"{nombre}: Horarios {hr}, Daily {dy}"
    assert len({hr for _n, hr, _d, _o in s["pares"]}) == len(s["pares"]), "dos personas con el mismo color"
    assert s["fuera"] == [1, 1, 2], "quien no está en Daily toma un color estable"

    html = s["html"]
    for nombre, hr, _dy, _orden in s["pares"]:
        assert (f'<tr class="hr-fila hr-color-{hr}"><th scope="row" class="hr-persona">'
                f'<span class="hr-punto" aria-hidden="true"></span>{nombre}</th>') in html
        assert f'<article class="hr-tarjeta hr-color-{hr}">' in html
    assert '<span class="hr-tramo-txt hr-pastilla">14:20–18:30</span>' in html
    assert '<td class="hr-total"><span class="hr-total-chip">20 h 10 min</span></td>' in html
    assert '<span class="hr-total-chip">20 h</span> por semana' in html


# ── las trampas de siempre ───────────────────────────────────────────────────

def test_nada_que_jinja_o_python_interpreten_en_lo_que_cambio():
    partes = {
        "css de flujos": _entre(SRC, "/* Flujos: lista vertical", "@media(max-width:480px)"),
        "js de flujos": _entre(SRC, "// ── flujos ──", "// ========== Simulador financiero =========="),
        "css de horarios": _entre(SRC, "/* ── Horarios", "/* ── Plantillas"),
        "js de horarios": _entre(SRC, "// ========== Horarios ==========", "// ========== FIN Horarios =========="),
    }
    for nombre, texto in partes.items():
        for trampa in ("{#", "{{", "{%"):
            assert trampa not in texto, f"{trampa!r} en el {nombre}"
        assert chr(92) not in texto, f"un backslash en el {nombre}"


def test_la_pagina_abre(cli):
    r = cli.get("/")
    assert r.status_code == 200
    pagina = r.get_data(as_text=True)
    assert pagina.count('id="eq-paso-rol-muestra"') == 1 and 'id="hr-contenido"' in pagina
