"""La red que le faltaba al CSS: que ningún token quede sin su valor claro.

El panel tiene 290 reglas `body.light` escritas a mano, sin nada que avise
cuando falta una. El resultado medido: de las 832 declaraciones con color de
las reglas oscuras, **595 no tienen contraparte clara**. No fue descuido de
nadie en particular — es lo que pasa cuando hay que acordarse de hacer el mismo
cambio dos veces, 290 veces.

Con tokens ese olvido se puede detectar, y estos tests lo detectan: un token
nuevo sin valor claro rompe el build en vez de aparecer como un rectángulo
oscuro en el tema claro tres semanas después.
"""

import re
from html.parser import HTMLParser

import pytest

import dashboard


def _bloque(selector: str) -> str:
    """El cuerpo del bloque `selector{...}` de tokens del dashboard."""
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", dashboard.DASHBOARD_HTML)
    assert m, f"no encontré el bloque {selector}"
    return m.group(1)


def _tokens(selector: str) -> dict:
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", _bloque(selector)))


@pytest.fixture(scope="module")
def oscuro():
    return _tokens(":root")


@pytest.fixture(scope="module")
def claro():
    return _tokens("body.light")


def test_hay_tokens_definidos(oscuro):
    assert len(oscuro) >= 10, "la capa de tokens tiene que existir"


def test_todo_token_tiene_su_valor_claro(oscuro, claro):
    """El olvido que este archivo existe para atajar."""
    faltan = sorted(set(oscuro) - set(claro))

    assert not faltan, (
        f"estos tokens no tienen valor en body.light: {faltan}. "
        "Un token sin par se ve igual en los dos temas, que es justo lo que "
        "hace que el tema claro se rompa de a poco.")


def test_no_sobra_ningun_token_en_el_tema_claro(oscuro, claro):
    """Al revés también: un token que solo existe en claro no lo usa nadie."""
    sobran = sorted(set(claro) - set(oscuro))

    assert not sobran, f"definidos solo en body.light: {sobran}"


def test_ningun_token_queda_vacio(oscuro, claro):
    for nombre, valores in (("oscuro", oscuro), ("claro", claro)):
        vacios = sorted(k for k, v in valores.items() if not v.strip())
        assert not vacios, f"tokens sin valor en el tema {nombre}: {vacios}"


def test_la_rampa_de_grises_se_invierte(oscuro, claro):
    """El dato que hace peligroso un buscar-y-reemplazar por hex.

    Medido sobre las reglas reales: #475569 se convierte en #94a3b8 y #94a3b8
    en #475569 — se cruzan. Si alguien "simplifica" los tokens igualando estos
    dos valores, el tema claro pierde la jerarquía entre texto tenue y texto
    apenas visible, y no lo nota ningún otro test.

    La otra mitad del cruce era `--texto-apenas` (#475569 -> #94a3b8), que se
    fundió en `--texto-debil` el 11/9. El cruce sigue estando en los pares de
    las reglas viejas: por eso migrar por hex sigue siendo peligroso.
    """
    assert oscuro["--texto-tenue"] == "#94a3b8"
    assert claro["--texto-tenue"] == "#475569"


def test_los_dos_temas_son_de_verdad_distintos(oscuro, claro):
    """Con una excepción deliberada.

    `--azul` es el azul de marca y no cambia entre temas.

    `--texto-debil` fue la otra hasta el 11/9: valía #64748b en los dos temas,
    el "pivote" de la rampa que parecía leerse bien sobre los dos fondos. Medido
    no era así: en oscuro daba 3,62:1 sobre la superficie, abajo del 4,5 del
    texto chico. Ahora vale #8190a6 en oscuro y deja de ser igual en los dos.
    """
    esperadas = {"--azul"}
    iguales = {k for k in oscuro if oscuro[k].strip() == claro.get(k, "").strip()}

    assert iguales == esperadas, (
        f"tokens con el mismo valor en los dos temas: {sorted(iguales)}. "
        "Si es a propósito, sumalo a esta lista con el motivo.")


def test_la_seccion_finanzas_usa_los_tokens(oscuro):
    """La superficie migrada. Si alguien la revierte a colores a mano, se ve acá."""
    reglas = re.findall(r"^\.fin-[^{\n]*\{[^}]*\}", dashboard.DASHBOARD_HTML, re.M)
    con_var = [r for r in reglas if "var(--" in r]

    assert len(con_var) >= 10, (
        f"solo {len(con_var)} de {len(reglas)} reglas .fin- usan tokens")


# ── sidebar y tablas ─────────────────────────────────────────────────────────
# La segunda superficie migrada. Es la que se ve en todas las pantallas: el
# sidebar está siempre, y `.table-row` la usan Cola, Seguimientos, Clientes,
# Meta Ads y Finanzas.

SIDEBAR_Y_TABLAS = [
    ".sidebar", ".sidebar-logo", ".sidebar-bottom",
    ".nav-item", ".nav-item:hover", ".nav-section-label",
    ".logout-btn", ".logout-btn:hover", ".theme-btn", ".theme-btn:hover",
    ".table-wrap", ".table-header", ".table-header span",
    ".table-row", ".table-row:hover",
]


def _regla(selector: str) -> str:
    # Acepta una lista que arranque con el selector: `.table-row:hover` va
    # combinado con su version `body.light` (ver el test del hover de fila).
    m = re.search(r"^" + re.escape(selector) + r"(?:,[^{]*)?\{([^}]*)\}",
                  dashboard.DASHBOARD_HTML, re.M)
    assert m, f"no encontré la regla {selector}"
    return m.group(1)


@pytest.mark.parametrize("selector", SIDEBAR_Y_TABLAS)
def test_el_sidebar_y_las_tablas_usan_los_tokens(selector):
    """Ningún color escrito a mano: si vuelve uno, el tema claro se despega."""
    cuerpo = _regla(selector)
    sueltos = re.findall(r"#[0-9a-fA-F]{3,8}\b", cuerpo)

    assert not sueltos, f"{selector} tiene colores a mano: {sueltos}"
    assert "var(--" in cuerpo, f"{selector} no usa ningún token"


@pytest.mark.parametrize("selector", SIDEBAR_Y_TABLAS)
def test_el_sidebar_y_las_tablas_no_tienen_regla_clara_propia(selector):
    """Los tokens ya dan el valor claro. Una regla `body.light` al lado es el
    mismo cambio escrito dos veces, que es lo que este archivo existe para
    dejar de hacer."""
    patron = r"^body\.light " + re.escape(selector) + r"\{"
    assert not re.search(patron, dashboard.DASHBOARD_HTML, re.M), (
        f"sobra `body.light {selector}`: los tokens ya lo cubren")


def test_las_clases_de_fila_viejas_no_estan():
    """`.row-sin-contactar`, `.row-agendo` y `.row-firmo` no las arma nadie:
    las filas usan `row-${crm}` con guion bajo, y ningún `crm_status` lleva
    guion medio. Tenían fondos oscuros sin par claro — si alguna vez volvían a
    matchear, pintaban una franja negra en el tema claro."""
    for vieja in (".row-sin-contactar", ".row-agendo", ".row-firmo"):
        assert vieja not in dashboard.DASHBOARD_HTML, f"volvió {vieja}"


# ── el hover ─────────────────────────────────────────────────────────────────

def _lab(hexa: str) -> tuple:
    h = hexa.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h

    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    x = (r * .4124 + g * .3576 + b * .1805) / .95047
    y = r * .2126 + g * .7152 + b * .0722
    z = (r * .0193 + g * .1192 + b * .9505) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > .008856 else 7.787 * t + 16 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def _delta_e(a: str, b: str) -> float:
    return sum((p - q) ** 2 for p, q in zip(_lab(a), _lab(b))) ** .5


@pytest.mark.parametrize("tema", ["oscuro", "claro"])
def test_el_hover_se_distingue_de_su_superficie(oscuro, claro, tema):
    """La trampa en la que casi se cae la migración.

    El token "más parecido" al hover de hoy era `--superficie-alta`, pero sobre
    la superficie nueva daba ΔE 3,6 en oscuro y 2,2 en claro: el hover dejaba
    de verse. Lo que importa no es cuánto se parece el hover al color de antes
    sino cuánto se distingue de lo que tiene abajo.

    4 queda justo debajo del hover del sidebar en claro (4,4), que es el más
    tenue de los que había y se veía bien.
    """
    valores = oscuro if tema == "oscuro" else claro
    distancia = _delta_e(valores["--hover"], valores["--superficie"])

    assert distancia >= 4, (
        f"--hover sobre --superficie en tema {tema}: ΔE {distancia:.1f}. "
        "Por debajo de ~4 el hover no se ve.")


def test_el_hover_sube_en_oscuro_no_baja():
    """En oscuro el hover aclara. `--fondo-hundido` también se distinguía de
    la superficie, pero oscureciendo: el hover del sidebar iba a hundirse
    mientras el de las filas subía."""
    osc = _tokens(":root")
    assert _lab(osc["--hover"])[0] > _lab(osc["--superficie"])[0]


def test_hay_un_texto_mas_fuerte_que_el_texto():
    """El nav activo, los h1 de página y los números de los KPIs van en #fff
    en oscuro, por encima de `--texto`. Si se los baja a `--texto` pierden el
    énfasis: el ítem activo del sidebar queda igual que el hover."""
    osc, cla = _tokens(":root"), _tokens("body.light")
    assert osc["--texto-fuerte"] == "#fff"
    assert cla["--texto-fuerte"] == "#0f172a"


def test_el_hover_de_fila_le_gana_a_los_tintes_de_estado_en_claro():
    """La regla clara que NO se puede borrar, y no es por el color.

    En claro, los tintes de estado (`body.light .row-contactado`, especificidad
    0,2,1) le ganan a un `.table-row:hover` pelado (0,2,0). Antes ganaba el
    hover porque `body.light .table-row:hover` sumaba el `body.light` (0,3,1).
    Si se borra esa regla porque "el token ya da el color", el hover desaparece
    justo en las filas de color, en tema claro, y ningún otro test lo ve.

    Va como un solo selector combinado para que el valor exista una sola vez.
    """
    assert re.search(
        r"^\.table-row:hover,body\.light \.table-row:hover\{background:var\(--hover\)\}",
        dashboard.DASHBOARD_HTML, re.M)


def test_las_tablas_del_celular_tambien_usan_tokens():
    """El bug que el sidebar no tenía y las tablas sí.

    Adentro de `@media(max-width:768px)` las filas se vuelven tarjetas con
    colores fijos y `!important`: `background:#111827!important`. Un
    `!important` le gana a cualquier `body.light` que no lo sea, así que en el
    celular, en tema claro, las tarjetas eran oscuras sobre la página clara.
    No lo había roto ninguna migración: estaba así desde antes.

    El `!important` se queda (sirve para pisar el layout de escritorio); lo
    que no puede ir es un color fijo adentro, porque ese no cambia con el tema.

    Los regex van sin barras invertidas a propósito: una `\b` escrita desde
    un script llegó una vez como un carácter de retroceso, y el test pasaba
    sin probar nada.
    """
    css = chr(10).join(re.findall(r"<style[^>]*>(.*?)</style>",
                                  dashboard.DASHBOARD_HTML, re.S))
    inicio = css.index("@media(max-width:768px){")
    prof, i = 0, inicio
    while True:
        prof += {"{": 1, "}": -1}.get(css[i], 0)
        i += 1
        if prof == 0 and css[i - 1] == "}":
            break
    bloque = css[inicio:i]

    hex_fijo = "#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])"
    fijos = [sel.strip()[:60]
             for sel, cuerpo in re.findall("([^{}]+)[{]([^{}]*)[}]", bloque)
             if ".table-row" in sel and re.search(hex_fijo, cuerpo)]

    assert ".table-row" in bloque, "no encontré las reglas de tabla del celular"
    assert not fijos, f"reglas de tabla del celular con color fijo: {fijos}"


# ── cabecera de página y tarjetas de KPI ─────────────────────────────────────
# Tercera superficie. Igual que el sidebar, está en todas las pantallas.

CABECERA = [".page-header h1", ".panel-head h1", ".page-date",
            ".stat-card", ".stat-label", ".stat-val", ".stat-val.yellow"]


@pytest.mark.parametrize("selector", CABECERA)
def test_la_cabecera_usa_los_tokens(selector):
    cuerpo = _regla(selector)
    sueltos = re.findall("#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])", cuerpo)

    assert not sueltos, f"{selector} tiene colores a mano: {sueltos}"
    assert "var(--" in cuerpo, f"{selector} no usa ningún token"


@pytest.mark.parametrize("selector", CABECERA)
def test_la_cabecera_no_tiene_regla_clara_propia(selector):
    patron = "^body[.]light " + re.escape(selector) + "[{]"
    assert not re.search(patron, dashboard.DASHBOARD_HTML, re.M), (
        f"sobra `body.light {selector}`: los tokens ya lo cubren")


def test_no_quedan_important_en_la_cabecera():
    """La escalada que venía del rediseño de mayo.

    Tres KPIs de Métricas tenían `style="color:#f59e0b"`. El inline le gana a
    cualquier regla clara, y el ámbar sobre blanco da 2,15:1, ilegible. Para
    taparlo alguien forzó `body.light .stat-val{color:#0f172a !important}`; y
    como ese `!important` le ganaba también al verde y al azul, esos tuvieron
    que llevar el suyo. Resultado: en claro los tres KPIs perdían el ámbar.

    Con la clase `.yellow` y `--ambar` (#b45309 en claro, 5,02:1) no hace falta
    ningún `!important`. Si vuelve uno, es que volvió un inline.
    """
    importantes = re.findall(
        "body[.]light [^{]*(?:page-header|panel-head|page-date|stat-)[^{]*[{][^}]*!important",
        dashboard.DASHBOARD_HTML)
    assert not importantes, f"volvieron los !important: {importantes}"


def test_los_kpis_ambar_de_metricas_usan_la_clase():
    html = dashboard.DASHBOARD_HTML
    for id_ in ("m-meetings", "m-meeting-rate", "mm-week"):
        assert f'class="stat-val yellow" id="{id_}"' in html, id_
    assert 'style="color:#f59e0b"' not in html, "volvió el ámbar inline"


def test_el_verde_y_el_azul_siguen_teniendo_su_version_clara():
    """Son colores semánticos, no de la rampa de grises: no van a tokens en
    esta pasada. Pierden el `!important` pero no su regla clara."""
    html = dashboard.DASHBOARD_HTML
    assert html.count("body.light .stat-val.green{color:#16a34a}") == 1
    assert html.count("body.light .stat-val.blue{color:#0088cc}") == 1


# ── Métricas: las tarjetas y lo que tienen adentro ───────────────────────────
# Cuarta superficie. Las .m-card eran oscuras en tema claro desde siempre, y no
# porque faltara la regla clara: estaba escrita para `.metrics-card`, una clase
# que no existe. Nunca matcheó.

METRICAS = [".m-card", ".m-card-title", ".bar-label", ".bar-track",
            ".bar-val", ".funnel-label", ".month-tick"]


@pytest.mark.parametrize("selector", METRICAS)
def test_metricas_usa_los_tokens(selector):
    cuerpo = _regla(selector)
    sueltos = re.findall("#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])", cuerpo)

    assert not sueltos, f"{selector} tiene colores a mano: {sueltos}"
    assert "var(--" in cuerpo, f"{selector} no usa ningún token"


@pytest.mark.parametrize("selector", METRICAS)
def test_metricas_no_tiene_regla_clara_propia(selector):
    patron = "^body[.]light " + re.escape(selector) + "[{]"
    assert not re.search(patron, dashboard.DASHBOARD_HTML, re.M), (
        f"sobra `body.light {selector}`: los tokens ya lo cubren")


def test_no_quedan_reglas_claras_para_clases_que_no_existen():
    """Las cinco clases de estas reglas claras no las usaba nadie: el markup
    dice `.m-card` y `.m-card-title`. Una regla que no matchea no da error,
    simplemente no hace nada — por eso esto estuvo roto sin que nadie lo viera."""
    html = dashboard.DASHBOARD_HTML
    for muerta in ("metrics-card", "metrics-section-title", "funnel-val"):
        assert muerta not in html, f"volvió .{muerta}"


def test_los_mensajes_vacios_de_metricas_usan_token():
    html = dashboard.DASHBOARD_HTML
    assert 'style="color:#475569;font-size:.8rem"' not in html
    assert html.count('style="color:var(--texto-debil);font-size:.8rem"') == 5


def test_no_hay_comas_nuevas_que_corten_el_body_light():
    """`body.light .a,.b{...}` no es "estas dos, en claro": la coma corta el
    selector y `.b` queda sin scope, aplicado en los dos temas.

    Así estaba `body.light .funnel-val,.bar-val{color:#475569}`: en oscuro,
    los números de las barras salían en #475569 en vez del #64748b de su
    regla, porque esta venía después. Y `.body.light` (con punto) es una
    clase que no existe: esa parte no matchea nunca.

    Las de CONOCIDAS son de otras superficies y quedan anotadas para que se
    borren de acá cuando se migren. Una nueva rompe el build.
    """
    CONOCIDAS = {
        "body.light .biz-name,.biz-name",
        "body.light .biz-name a,.biz-name a",
        "body.light .wa-lead-item:hover,.body.light .wa-lead-item.active",
    }
    css = chr(10).join(re.findall(r"<style[^>]*>(.*?)</style>",
                                  dashboard.DASHBOARD_HTML, re.S))
    cortadas = set()
    for crudo in re.findall("([^{}]+)[{]", css):
        sel = " ".join(crudo.split()).split("*/")[-1].strip()
        partes = [x.strip() for x in sel.split(",")]
        if (len(partes) > 1 and partes[0].startswith("body.light")
                and any(not x.startswith("body.light") for x in partes[1:])):
            cortadas.add(sel)

    nuevas = cortadas - CONOCIDAS
    assert not nuevas, f"comas que cortan el body.light: {sorted(nuevas)}"


# ── panel de cliente ─────────────────────────────────────────────────────────
# Quinta superficie, la más rota: 51 propiedades con color sin par claro. En
# tema claro, el selector de estado y el área de notas eran cajas negras y el
# borde de la cabecera seguía oscuro.

PANEL = [".client-panel", ".cp-header", ".cp-title", ".cp-sub", ".cp-status-sel",
         ".cp-tabs", ".cp-tab", ".cp-tab.active", ".cp-close", ".cp-section-title",
         ".cp-field-label", ".cp-field-val", ".cp-meeting-card", ".cp-meeting-title",
         ".cp-meeting-meta", ".cp-summary-box", ".cp-transcript-area", ".cp-req-area",
         ".cp-event-label", ".cp-event-meta", ".cp-event-note", ".cp-wa-msg.in",
         ".cp-btn-ghost"]


@pytest.mark.parametrize("selector", PANEL)
def test_el_panel_de_cliente_usa_los_tokens(selector):
    cuerpo = _regla(selector)
    sueltos = re.findall("#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])", cuerpo)

    assert not sueltos, f"{selector} tiene colores a mano: {sueltos}"
    assert "var(--" in cuerpo, f"{selector} no usa ningún token"


@pytest.mark.parametrize("selector", PANEL)
def test_el_panel_de_cliente_no_tiene_regla_clara_propia(selector):
    patron = "^body[.]light " + re.escape(selector) + "[{]"
    assert not re.search(patron, dashboard.DASHBOARD_HTML, re.M), (
        f"sobra `body.light {selector}`: los tokens ya lo cubren")


def test_el_bloque_de_eventos_no_esta_pegado_dos_veces():
    """La segunda copia empezaba con la regla de layout de `.cp-event-row`
    prefijada con `body.light`: el copy-paste le había puesto el tema claro a
    una regla que es de los dos."""
    html = dashboard.DASHBOARD_HTML
    assert html.count(".cp-event-label{") == 1
    assert "body.light .cp-event-row{display:flex" not in html


def test_el_js_del_panel_no_pinta_fondos_oscuros_inline():
    """El CSS no alcanza si el JS arma el panel con `style="background:#0a0f1a"`:
    el inline le gana a cualquier regla y no cambia con el tema. Eran 43
    líneas con colores escritos, entre ellas un modal entero."""
    oscuros = ("#111827", "#0a0f1a", "#0f172a", "color:#e2e8f0")
    dentro, culpables, nombre = False, [], ""
    for linea in dashboard.DASHBOARD_HTML.split(chr(10)):
        m = re.match("(async )?function ([A-Za-z_]+)[(]", linea)
        if m:
            nombre = m.group(2)
            dentro = nombre.startswith("_cp") or nombre == "openClientPanel"
        if dentro and 'style="' in linea:
            for attr in re.findall('style="[^"]*"', linea):
                if any(o in attr for o in oscuros):
                    culpables.append(f"{nombre}: {attr[:60]}")
    assert not culpables, f"colores oscuros inline en el panel: {culpables}"


def test_el_relleno_se_distingue_de_la_superficie(oscuro, claro):
    """`--relleno` vale lo mismo que `--hover` pero es otro rol: chips, botones
    fantasma, la burbuja entrante de WhatsApp. Si mañana cambia el hover, las
    burbujas no tienen por qué cambiar. Tiene que verse sobre el panel."""
    for valores in (oscuro, claro):
        assert _delta_e(valores["--relleno"], valores["--superficie"]) >= 4


# ── contraste del texto ──────────────────────────────────────────────────────
# WCAG AA pide 4,5:1 para texto de tamaño normal, y casi todo el texto
# secundario del CRM es chico (0,65 a 0,8rem). Lo señaló la sesión de
# marketing con `--rotulo`; el problema era el mismo en `--texto-debil`.

def _luminancia(hexa: str) -> float:
    h = hexa.strip().lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h

    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contraste(a: str, b: str) -> float:
    x, y = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


TEXTO = ["--texto-fuerte", "--texto", "--texto-tenue", "--texto-debil", "--rotulo"]


@pytest.mark.parametrize("token", TEXTO)
def test_el_texto_llega_a_4_5_en_oscuro_sobre_todas_las_superficies(oscuro, token):
    """`--texto-debil` y `--rotulo` valían #64748b: 3,62:1 sobre la superficie y
    3,07:1 sobre el relleno de chips. #8190a6 es el gris más oscuro de la misma
    familia que llega a 4,5 sobre las cuatro.

    `--texto-apenas` ya no existe: daba 2,27:1 en oscuro y 2,56:1 en claro, y
    se fundió en `--texto-debil` el 11/9 (ver el test de abajo).
    """
    for superficie in ("--superficie", "--fondo", "--fondo-hundido", "--relleno"):
        c = _contraste(oscuro[token], oscuro[superficie])
        assert c >= 4.5, f"{token} sobre {superficie} en oscuro: {c:.2f}:1"


@pytest.mark.parametrize("token", TEXTO)
def test_el_texto_llega_a_4_5_en_claro(claro, token):
    """En claro, sobre las cuatro superficies. `--texto-debil` daba 4,34 sobre
    los tintes grises (#f1f5f9, donde van los encabezados de tabla): pasó de
    #64748b a #627188 (ΔE 1,2, no se nota) y ahora da 4,52."""
    for superficie in ("--superficie", "--fondo", "--fondo-hundido", "--relleno"):
        c = _contraste(claro[token], claro[superficie])
        assert c >= 4.5, f"{token} sobre {superficie} en claro: {c:.2f}:1"


def test_texto_apenas_se_fundio_en_texto_debil(oscuro, claro):
    """Un tercer gris "más apagado que el débil" no puede pasar 4,5:1 donde se
    usaba: en oscuro el mínimo es prácticamente `--texto-debil`, y en claro,
    sobre el gris de los encabezados de tabla, tendría que ser MÁS oscuro que
    él — la jerarquía quedaba al revés. Los rótulos en mayúscula ya se
    distinguen por tipografía (mayúscula, negrita, espaciado, tamaño).

    Si vuelve `--texto-apenas`, vuelve un texto que no se lee.
    """
    assert "--texto-apenas" not in oscuro and "--texto-apenas" not in claro
    assert "var(--texto-apenas)" not in dashboard.DASHBOARD_HTML
    assert claro["--texto-debil"] == "#627188"


# ── modales y botones ────────────────────────────────────────────────────────
# Sexta superficie. `.btn-primary` y `.btn-ghost` solo existían en el HTML de la
# página de administración (adentro de create_app): en el dashboard los botones
# de Finanzas se dibujaban como botones del sistema — gris, borde outset, sin
# radio ni padding — en los dos temas.

MODAL = [".modal", ".modal h3", ".modal p", ".modal textarea",
         ".modal textarea::placeholder", ".modal input[type=text]", ".modal select",
         ".modal input::placeholder", ".modal input:focus", ".modal-label",
         ".btn-cancel"]


@pytest.mark.parametrize("selector", MODAL)
def test_los_modales_usan_los_tokens(selector):
    cuerpo = _regla(selector)
    sueltos = re.findall("#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])", cuerpo)

    assert not sueltos, f"{selector} tiene colores a mano: {sueltos}"
    assert "var(--" in cuerpo, f"{selector} no usa ningún token"


def _css_del_dashboard() -> str:
    return chr(10).join(re.findall(r"<style[^>]*>(.*?)</style>",
                                   dashboard.DASHBOARD_HTML, re.S))


def test_el_dashboard_define_sus_propios_botones():
    """El bug de fondo: las clases se usaban en el dashboard pero solo estaban
    definidas en otro documento HTML. Si se borran de acá, los botones vuelven
    a ser los del sistema y ningún otro test lo ve."""
    css = _css_del_dashboard()
    assert ".btn-primary{background:var(--azul);color:#fff" in css
    assert ".btn-ghost{background:var(--relleno);color:var(--texto-tenue)}" in css
    compartida = re.search(r"^\.btn-primary,\.btn-ghost\{([^}]*)\}", css, re.M)
    assert compartida, "falta la regla de tamaño compartida"
    assert "padding:9px 18px" in compartida.group(1)
    assert "border-radius:8px" in compartida.group(1)


def test_el_texto_del_boton_primario_es_blanco_en_los_dos_temas():
    """Va sobre el azul de marca. `--texto-fuerte` sería azul marino en claro,
    sobre azul: ilegible."""
    for regla in (".btn-primary{background:var(--azul);color:#fff",
                  ".btn-confirm{background:var(--azul);border:none;color:#fff"):
        assert regla in _css_del_dashboard(), regla


def test_los_botones_de_solo_icono_son_compactos():
    """El lápiz y el tacho de las listas de Finanzas viven en columnas de 76px:
    con el padding de un botón de texto se desbordaban."""
    html = dashboard.DASHBOARD_HTML
    iconos = re.findall(
        r'<button class="(btn-(?:ghost|primary)[^"]*)"[^>]*>\s*<i data-lucide="[^"]*"[^>]*></i>\s*</button>',
        html)
    assert iconos, "no encontré botones de solo ícono"
    sin_compacto = [c for c in iconos if "btn-icono" not in c]
    assert not sin_compacto, f"botones de solo ícono sin btn-icono: {sin_compacto}"


def test_no_quedan_reglas_claras_duplicadas_en_los_modales():
    """Había cuatro pares repetidos, uno con `!important`, y `.modal label` dos
    veces con valores distintos. Queda solo el filtro del ícono nativo del
    selector de fecha, que es propio de cada tema y no un color."""
    claras = re.findall(r"^(body[.]light [.]modal[^{]*)[{]", dashboard.DASHBOARD_HTML, re.M)
    assert claras == ["body.light .modal input[type=datetime-local]::-webkit-calendar-picker-indicator"], claras


# ── selects fuera de los modales ─────────────────────────────────────────────
# Séptima superficie. El selector de período de Finanzas ("Mes actual") no tenía
# clase ni contenedor que lo estilizara: se dibujaba como el select del sistema
# —blanco, borde gris— en los dos temas. Y la barra de acciones en lote no tenía
# versión clara: quedaba oscura, con "N seleccionados" en azul marino encima.

SELECTS = [".filter-select", ".filter-select option", ".filter-select:hover",
           ".resp-sel", ".resp-sel:hover", ".resp-sel.vacante",
           ".batch-bar", ".batch-count", ".batch-sel",
           ".batch-cancel", ".batch-cancel:hover"]


@pytest.mark.parametrize("selector", SELECTS)
def test_los_selects_usan_los_tokens(selector):
    cuerpo = _regla(selector)
    sueltos = re.findall("#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])", cuerpo)

    assert not sueltos, f"{selector} tiene colores a mano: {sueltos}"
    assert "var(--" in cuerpo, f"{selector} no usa ningún token"


@pytest.mark.parametrize("selector", SELECTS)
def test_los_selects_no_tienen_regla_clara_propia(selector):
    patron = r"^body\.light " + re.escape(selector) + r"\{"
    assert not re.search(patron, dashboard.DASHBOARD_HTML, re.M), (
        f"sobra `body.light {selector}`: los tokens ya lo cubren")


_VACIOS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
           "meta", "source", "track", "wbr"}


class _Selects(HTMLParser):
    """Cada <select> del documento, con las clases de sus ancestros. El JS de
    los <script> no se parsea como HTML: los selects que arman las plantillas
    (`.resp-sel`) no entran, y esos llevan clase siempre."""

    def __init__(self):
        super().__init__()
        self.pila, self.selects = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        clases = (a.get("class") or "").split()
        if tag == "select":
            ancestros = {c for _, cs in self.pila for c in cs}
            self.selects.append((a.get("id") or "?", set(clases),
                                 a.get("style") or "", ancestros))
        if tag not in _VACIOS:
            self.pila.append((tag, clases))

    def handle_endtag(self, tag):
        for i in range(len(self.pila) - 1, -1, -1):
            if self.pila[i][0] == tag:
                del self.pila[i:]
                return


def test_ningun_select_queda_con_el_estilo_del_sistema():
    """Un select está cubierto si alguna de sus clases tiene regla, si su
    estilo inline usa tokens, o si vive adentro de un contenedor con regla
    `.contenedor select` (los modales, los filtros de marketing). Si no, el
    navegador lo dibuja blanco con borde gris en los dos temas, como pasaba
    con el de período de Finanzas."""
    css = _css_del_dashboard()
    selectores = " ".join(re.findall(r"([^{}]+)[{]", css))
    con_regla = set(re.findall(r"[.]([a-zA-Z][\w-]*)", selectores))
    contenedores = set(re.findall(r"[.]([a-zA-Z][\w-]*) select(?![\w-])", selectores))

    p = _Selects()
    p.feed(dashboard.DASHBOARD_HTML)

    assert len(p.selects) >= 10, f"el parser vio pocos selects: {len(p.selects)}"
    sueltos = [ident for ident, clases, estilo, ancestros in p.selects
               if not clases & con_regla
               and "var(--" not in estilo
               and not ancestros & contenedores]
    assert not sueltos, f"selects con el estilo del sistema: {sueltos}"


def _token_de(selector: str, propiedad: str) -> str:
    m = re.search("(?:^|;)" + propiedad + "[:]var[(](--[a-z-]+)[)]", _regla(selector))
    assert m, f"{selector} no toma `{propiedad}` de un token"
    return m.group(1)


@pytest.mark.parametrize("tema", ["oscuro", "claro"])
def test_la_barra_de_lote_se_lee_en_los_dos_temas(oscuro, claro, tema):
    """En claro la barra quedaba oscura (#1e293b) con "N seleccionados" en
    #0f172a encima: 1,22:1. "Cancelar" (#475569) daba 1,93:1 en los dos."""
    t = oscuro if tema == "oscuro" else claro
    barra = t[_token_de(".batch-bar", "background")]
    for selector in (".batch-count", ".batch-cancel"):
        c = _contraste(t[_token_de(selector, "color")], barra)
        assert c >= 4.5, f"{selector} sobre la barra en {tema}: {c:.2f}:1"
    campo = t[_token_de(".batch-sel", "background")]
    assert _delta_e(campo, barra) >= 4, f"el select no se despega de la barra en {tema}"


def test_el_responsable_vacante_se_distingue_en_los_dos_temas(oscuro, claro):
    """En claro, `body.light .resp-sel` (0,2,1) le ganaba a `.resp-sel.vacante`
    (0,2,0): un cliente sin responsable se veía igual que uno asignado. En
    oscuro, #64748b daba 3,75:1."""
    lleno = _token_de(".resp-sel", "color")
    vacante = _token_de(".resp-sel.vacante", "color")
    fondo = _token_de(".resp-sel", "background")

    assert vacante != lleno
    for t in (oscuro, claro):
        assert _contraste(t[vacante], t[fondo]) >= 4.5


def test_los_selects_sin_contorno_tienen_foco():
    """`outline:none` sin un `:focus` propio deja al que navega con teclado sin
    saber dónde está parado. Les pasaba a `.filter-select` y a `.batch-sel`."""
    css = _css_del_dashboard()
    sin_contorno = re.findall(r"^[.]([\w-]*-sel(?:ect)?)[{][^}]*outline:none", css, re.M)

    assert sin_contorno, "no encontré selects con outline:none"
    for clase in sin_contorno:
        assert re.search("[.]" + clase + "[:]focus(?![\\w-])", css), (
            f".{clase} tiene outline:none y ningún :focus")


def test_no_quedan_clases_de_select_sin_uso():
    """`.status-sel` y `.user-select` no las usaba nadie: reglas con colores a
    mano que cualquiera migrando por hex iba a tocar de gusto."""
    css = _css_del_dashboard()
    resto = re.sub(r"<style[^>]*>.*?</style>", "", dashboard.DASHBOARD_HTML, flags=re.S)
    clases = set(re.findall(r"[.]([a-z][\w-]*-sel(?:ect)?)(?![\w-])", css))

    assert clases, "no encontré clases de select en el CSS"
    sin_uso = sorted(c for c in clases
                     if not re.search("(?<![\\w-])" + c + "(?![\\w:-])", resto))
    assert not sin_uso, f"clases de select sin uso: {sin_uso}"
