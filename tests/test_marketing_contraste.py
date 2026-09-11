"""El panel de Marketing se lee en los dos temas, y eso se calcula.

Existe porque al pasar el panel a los tokens del CRM el ojo decia que estaba
bien y la cuenta decia que no: los encabezados de tabla daban 3,62:1 en oscuro y
las citas de metricas 2,27:1. Las citas son el mecanismo de auditoria del
informe —son el id que permite ir a la tabla y comprobar cada afirmacion— asi
que ilegibles no cumplen ninguna funcion.

El contraste es aritmetica sobre dos colores. No se mira: se calcula.
"""

import re

import pytest

import dashboard

# Piso de la WCAG: 4,5:1 para texto normal, 3:1 para texto grande y para
# componentes que no son texto (un borde, el trazo de un grafico).
PISO_TEXTO = 4.5
PISO_COMPONENTE = 3.0


def _tokens(patron):
    m = re.search(patron, dashboard.DASHBOARD_HTML)
    assert m, f"no encontre el bloque de tokens {patron}"
    return {k: v.strip() for k, v in
            re.findall(r"(--[a-z-]+):\s*([^;]+);", m.group(1))}


TEMAS = {
    "oscuro": _tokens(r":root\{([^}]*)\}"),
    "claro": _tokens(r"body\.light\{([^}]*)\}"),
}


def _canal(v):
    v /= 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _luminancia(hexa):
    h = hexa.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (_canal(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(a, b):
    la, lb = _luminancia(a), _luminancia(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# (que es, token de tinta, token de fondo, piso)
PARES = [
    ("texto de tabla sobre la tarjeta", "--texto", "--superficie", PISO_TEXTO),
    ("cuerpo del informe", "--texto-tenue", "--superficie", PISO_TEXTO),
    ("encabezado de tabla y rotulos", "--rotulo", "--superficie", PISO_TEXTO),
    ("mensaje de vacio", "--texto-tenue", "--superficie", PISO_TEXTO),
    ("citas de metricas", "--texto-tenue", "--superficie", PISO_TEXTO),
    ("chip sobre su fondo", "--rotulo", "--hover", PISO_TEXTO),
    ("input del filtro", "--texto", "--fondo-hundido", PISO_TEXTO),
    ("aviso ambar", "--ambar", "--superficie", PISO_COMPONENTE),
    ("delta bueno", "--verde", "--superficie", PISO_COMPONENTE),
    ("delta malo", "--rojo", "--superficie", PISO_COMPONENTE),
    ("borde del hallazgo, que no es texto", "--rotulo", "--superficie",
     PISO_COMPONENTE),
    # Los embudos por campana viven en una tarjeta hundida adentro del bloque,
    # asi que su fondo NO es --superficie y hay que medirlos aparte: un par que
    # pasa sobre la superficie puede no pasar sobre el hundido.
    ("nombre de etapa en la tarjeta", "--texto-tenue", "--fondo-hundido",
     PISO_TEXTO),
    ("numero de etapa", "--texto", "--fondo-hundido", PISO_TEXTO),
    ("tasa de caida y subtitulo", "--rotulo", "--fondo-hundido", PISO_TEXTO),
]


@pytest.mark.parametrize("tema", sorted(TEMAS))
@pytest.mark.parametrize("etiqueta,tinta,fondo,piso", PARES)
def test_el_panel_se_lee(tema, etiqueta, tinta, fondo, piso):
    t = TEMAS[tema]
    c = contraste(t[tinta], t[fondo])
    assert c >= piso, (
        f"{etiqueta} en tema {tema}: {t[tinta]} sobre {t[fondo]} da {c:.2f}:1, "
        f"y el piso es {piso}:1")


def test_el_rotulo_ya_alcanza_para_texto():
    """La alarma que dejo G, y que salto como estaba previsto.

    Decia: `--rotulo` vale #64748b en oscuro, que sobre la superficie da 3,62:1,
    y "si alguna vez sube, este test se cae y se puede volver a usar para
    rotulos". Subio el 11/9: ahora vale #8190a6 en oscuro (5,31:1), junto con
    `--texto-debil`, que tenia el mismo problema.

    Por eso el panel usaba `--texto-tenue` en los rotulos. Volver a `--rotulo`
    queda a criterio de G: la segunda parte de este test (que en el panel solo
    este en el borde del hallazgo) sigue como estaba.
    """
    c = contraste(TEMAS["oscuro"]["--rotulo"], TEMAS["oscuro"]["--superficie"])
    assert c >= PISO_TEXTO, (
        f"--rotulo da {c:.2f}:1 en oscuro: volvio a no alcanzar para texto")

    # Con --rotulo ya legible, los rotulos de verdad volvieron a usarlo: es su
    # token por nombre y el que usa Finanzas para lo mismo, asi que un `th` se ve
    # igual en los dos paneles. La prosa —el cuerpo del informe, las notas, las
    # citas— se queda en --texto-tenue, que es mas suave a proposito.
    reglas = re.findall(r"^(\.sc-[^{]*)\{([^}]*)\}", dashboard.DASHBOARD_HTML, re.M)
    con_rotulo = {sel.strip() for sel, cuerpo in reglas if "--rotulo" in cuerpo}
    esperados = {".sc-hallazgo", ".sc-filtro label", ".sc-tabla th",
                 ".sc-bloque>.sc-sub", ".sc-chip",
                 # Los tres de los bloques por campana. Son rotulos: el
                 # subtitulo de cada tarjeta, la tasa de caida y la leyenda.
                 ".sc-embudo-sub", ".sc-etapa-tasa", ".sc-leyenda-item"}
    assert con_rotulo == esperados, (
        f"--rotulo esta en {sorted(con_rotulo)} y se esperaba {sorted(esperados)}")
