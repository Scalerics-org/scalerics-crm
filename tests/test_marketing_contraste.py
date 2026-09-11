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
    ("encabezado de tabla y rotulos", "--texto-tenue", "--superficie", PISO_TEXTO),
    ("mensaje de vacio", "--texto-tenue", "--superficie", PISO_TEXTO),
    ("citas de metricas", "--texto-tenue", "--superficie", PISO_TEXTO),
    ("chip sobre su fondo", "--texto-tenue", "--hover", PISO_TEXTO),
    ("input del filtro", "--texto", "--fondo-hundido", PISO_TEXTO),
    ("aviso ambar", "--ambar", "--superficie", PISO_COMPONENTE),
    ("delta bueno", "--verde", "--superficie", PISO_COMPONENTE),
    ("delta malo", "--rojo", "--superficie", PISO_COMPONENTE),
    ("borde del hallazgo, que no es texto", "--rotulo", "--superficie",
     PISO_COMPONENTE),
]


@pytest.mark.parametrize("tema", sorted(TEMAS))
@pytest.mark.parametrize("etiqueta,tinta,fondo,piso", PARES)
def test_el_panel_se_lee(tema, etiqueta, tinta, fondo, piso):
    t = TEMAS[tema]
    c = contraste(t[tinta], t[fondo])
    assert c >= piso, (
        f"{etiqueta} en tema {tema}: {t[tinta]} sobre {t[fondo]} da {c:.2f}:1, "
        f"y el piso es {piso}:1")


def test_el_rotulo_no_alcanza_para_texto_y_por_eso_no_se_usa_para_texto():
    """El motivo por el que el panel usa `--texto-tenue` y no `--rotulo`.

    `--rotulo` vale #64748b en oscuro, que sobre la superficie da 3,62:1: pasa
    el piso de componente pero no el de texto. Si alguna vez sube, este test se
    cae y se puede volver a usar para rotulos, que es lo que su nombre sugiere.
    Mientras tanto, el unico `--rotulo` del panel es un borde.
    """
    c = contraste(TEMAS["oscuro"]["--rotulo"], TEMAS["oscuro"]["--superficie"])
    assert c < PISO_TEXTO, (
        f"--rotulo ahora da {c:.2f}:1 en oscuro y ya sirve para texto: "
        "se puede simplificar el panel volviendo a usarlo en los rotulos")

    reglas = re.findall(r"^(\.sc-[^{]*)\{([^}]*)\}", dashboard.DASHBOARD_HTML, re.M)
    con_rotulo = [sel.strip() for sel, cuerpo in reglas if "--rotulo" in cuerpo]
    assert con_rotulo == [".sc-hallazgo"], (
        f"--rotulo aparece en {con_rotulo}; solo puede estar en el borde del "
        "hallazgo, que no es texto")
