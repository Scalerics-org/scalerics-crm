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
    """
    assert oscuro["--texto-tenue"] == "#94a3b8"
    assert claro["--texto-tenue"] == "#475569"
    assert oscuro["--texto-apenas"] == "#475569"
    assert claro["--texto-apenas"] == "#94a3b8"


def test_los_dos_temas_son_de_verdad_distintos(oscuro, claro):
    """Con dos excepciones deliberadas.

    `--azul` es el azul de marca y no cambia entre temas. `--texto-debil` es el
    PIVOTE de la rampa de grises: el único que se lee bien sobre los dos fondos,
    y el dato lo confirma (#64748b -> #64748b, x7 en las reglas reales).
    """
    esperadas = {"--azul", "--texto-debil"}
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
