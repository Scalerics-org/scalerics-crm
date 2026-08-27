"""La tarjeta lleva la frase mas fuerte del post, no el titulo del tema.

El titulo ya esta arriba en el mail y en LinkedIn va a estar en el texto: la
imagen tiene que aportar la linea que hace parar el scroll.
"""
from unittest.mock import MagicMock, patch

import pytest

from services.linkedin_posts import frase_tarjeta


POST = (
    "Alguien escribe el nombre de tu empresa en Google. Aparece una ficha de "
    "Maps sin horarios y un perfil de Facebook de 2019. Nada mas.\n\n"
    "Esa persona no piensa \"que raro, no tienen web\". Piensa que cerraste.\n\n"
    "El costo no se ve porque nadie llama para avisarte que decidio no llamarte."
)


def _respuesta(texto, stop_reason="end_turn"):
    msg = MagicMock()
    bloque = MagicMock()
    bloque.type = "text"
    bloque.text = texto
    msg.content = [bloque]
    msg.stop_reason = stop_reason
    return msg


def _con_respuesta(*textos):
    cliente = patch("services.linkedin_posts.anthropic.Anthropic")
    entorno = patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    return cliente, entorno, textos


def test_devuelve_la_frase(monkeypatch):
    with patch("services.linkedin_posts.anthropic.Anthropic") as C, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        C.return_value.messages.create.return_value = _respuesta("Piensa que cerraste")
        assert frase_tarjeta(POST) == "Piensa que cerraste"


def test_le_saca_las_comillas_y_el_punto_final():
    with patch("services.linkedin_posts.anthropic.Anthropic") as C, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        C.return_value.messages.create.return_value = _respuesta('  "Piensa que cerraste."  ')
        assert frase_tarjeta(POST) == "Piensa que cerraste"


def test_rechaza_una_frase_demasiado_larga():
    with patch("services.linkedin_posts.anthropic.Anthropic") as C, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        C.return_value.messages.create.return_value = _respuesta("palabra " * 30)
        assert frase_tarjeta(POST) is None


def test_rechaza_una_frase_vacia():
    with patch("services.linkedin_posts.anthropic.Anthropic") as C, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        C.return_value.messages.create.return_value = _respuesta("   ")
        assert frase_tarjeta(POST) is None


def test_rechaza_emoji_y_guion_largo():
    for malo in ("Piensa que cerraste \U0001F680", "Piensa — que cerraste"):
        with patch("services.linkedin_posts.anthropic.Anthropic") as C, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            C.return_value.messages.create.return_value = _respuesta(malo)
            assert frase_tarjeta(POST) is None, malo


def test_si_la_api_falla_devuelve_none():
    with patch("services.linkedin_posts.anthropic.Anthropic") as C, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        C.return_value.messages.create.side_effect = RuntimeError("boom")
        assert frase_tarjeta(POST) is None


def test_sin_api_key_devuelve_none():
    with patch.dict("os.environ", {}, clear=True):
        assert frase_tarjeta(POST) is None


# ── Integracion con el borrador ──────────────────────────────────────────────

VALIDO = (
    "Alguien escribe el nombre de tu empresa en Google y no aparece nada. "
    "Esa persona no piensa que sea raro, piensa que cerraste. El costo no se "
    "ve porque nadie llama para avisarte que decidio no llamarte, y por eso "
    "cuesta tanto notar que el problema existe adentro de la operacion."
)


@pytest.fixture
def db_path(tmp_path):
    from database import init_db, seed_linkedin_temas
    p = str(tmp_path / "t.db")
    init_db(p)
    seed_linkedin_temas(p)
    return p


def test_el_educativo_usa_la_frase_en_la_tarjeta(db_path):
    import json

    from services.linkedin_posts import linkedin_job_handler

    with patch("services.linkedin_posts.redactar", return_value=VALIDO), \
         patch("services.linkedin_posts.frase_tarjeta", return_value="Piensa que cerraste"):
        r = linkedin_job_handler(
            {"db_path": db_path, "lote": "l", "ahora": "2026-08-26T09:00:00"})

    for b in r["borradores"]:
        assert b["imagen_spec"]["frase"] == "Piensa que cerraste"


def test_si_no_sale_la_frase_cae_al_titulo_del_tema(db_path):
    from services.linkedin_posts import linkedin_job_handler

    with patch("services.linkedin_posts.redactar", return_value=VALIDO), \
         patch("services.linkedin_posts.frase_tarjeta", return_value=None):
        r = linkedin_job_handler(
            {"db_path": db_path, "lote": "l", "ahora": "2026-08-26T09:00:00"})

    # El titulo del tema es el respaldo: la tarjeta nunca sale vacia.
    for b in r["borradores"]:
        assert b["imagen_spec"]["frase"]
        assert b["imagen_tipo"] == "tarjeta"


def test_el_manual_sin_url_tambien_lleva_tarjeta(db_path):
    from services.linkedin_posts import linkedin_job_handler

    with patch("services.linkedin_posts.redactar", return_value=VALIDO), \
         patch("services.linkedin_posts.frase_tarjeta", return_value="Piensa que cerraste"):
        r = linkedin_job_handler({
            "db_path": db_path, "lote": "lm", "ahora": "2026-08-26T09:00:00",
            "contexto_manual": "Salio la tienda de Biciconde.",
        })

    b = r["borradores"][0]
    assert b["imagen_tipo"] == "tarjeta"
    assert b["imagen_spec"]["frase"] == "Piensa que cerraste"


def test_el_manual_con_url_sigue_usando_screenshot(db_path):
    from services.linkedin_posts import linkedin_job_handler

    with patch("services.linkedin_posts.redactar", return_value=VALIDO), \
         patch("services.linkedin_posts.frase_tarjeta", return_value="Piensa que cerraste"):
        r = linkedin_job_handler({
            "db_path": db_path, "lote": "lm2", "ahora": "2026-08-26T09:00:00",
            "contexto_manual": "Salio la tienda de Biciconde.",
            "imagen_url": "https://biciconde.uy",
        })

    b = r["borradores"][0]
    assert b["imagen_tipo"] == "screenshot"
    assert b["imagen_spec"]["url"] == "https://biciconde.uy"


def test_descarta_una_frase_cortada_por_max_tokens():
    """Opus 5 piensa por defecto: con poco presupuesto la frase vuelve cortada
    por la mitad y el texto truncado igual pasa el chequeo de largo."""
    with patch("services.linkedin_posts.anthropic.Anthropic") as C,          patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        C.return_value.messages.create.return_value = _respuesta(
            "El sistema dice 14 unidades. En", stop_reason="max_tokens")
        assert frase_tarjeta(POST) is None


def test_la_llamada_va_con_aire_y_effort_bajo():
    with patch("services.linkedin_posts.anthropic.Anthropic") as C,          patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        C.return_value.messages.create.return_value = _respuesta("Una frase corta")
        frase_tarjeta(POST)
        kwargs = C.return_value.messages.create.call_args.kwargs

    assert kwargs["max_tokens"] >= 2000
    assert kwargs["output_config"]["effort"] == "low"
