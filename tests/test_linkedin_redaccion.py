from unittest.mock import MagicMock, patch

from services.linkedin_posts import (
    contexto_educativo,
    contexto_manual,
    redactar,
)


VALIDO = (
    "Esta semana armamos la tienda online de una bloquera de Durazno. "
    "Tres dias de trabajo, catalogo con 40 productos y pago con Mercado Pago. "
    "Lo que mas costo no fue el codigo, fue ordenar los precios: tenian tres "
    "listas distintas segun el vendedor que atendiera. Eso pasa mas seguido "
    "de lo que parece, y es la parte que de verdad cambia el negocio."
)
INVALIDO = "Corto — con guion largo."


def _respuesta(texto):
    msg = MagicMock()
    bloque = MagicMock()
    bloque.type = "text"
    bloque.text = texto
    msg.content = [bloque]
    return msg


def test_redactar_devuelve_el_texto_cuando_valida():
    with patch("services.linkedin_posts.anthropic.Anthropic") as Cliente, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        Cliente.return_value.messages.create.return_value = _respuesta(VALIDO)
        resultado = redactar("contexto cualquiera", "concreto")

    assert resultado == VALIDO


def test_redactar_reintenta_una_vez_y_acepta_el_segundo():
    with patch("services.linkedin_posts.anthropic.Anthropic") as Cliente, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        Cliente.return_value.messages.create.side_effect = [
            _respuesta(INVALIDO), _respuesta(VALIDO),
        ]
        resultado = redactar("contexto cualquiera", "concreto")

    assert resultado == VALIDO
    assert Cliente.return_value.messages.create.call_count == 2


def test_redactar_devuelve_none_si_falla_dos_veces():
    with patch("services.linkedin_posts.anthropic.Anthropic") as Cliente, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        Cliente.return_value.messages.create.side_effect = [
            _respuesta(INVALIDO), _respuesta(INVALIDO),
        ]
        resultado = redactar("contexto cualquiera", "concreto")

    assert resultado is None
    assert Cliente.return_value.messages.create.call_count == 2


def test_redactar_devuelve_none_si_la_api_falla():
    with patch("services.linkedin_posts.anthropic.Anthropic") as Cliente, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        Cliente.return_value.messages.create.side_effect = RuntimeError("boom")
        assert redactar("contexto", "concreto") is None


def test_el_reintento_le_dice_al_modelo_que_rompio():
    with patch("services.linkedin_posts.anthropic.Anthropic") as Cliente, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        Cliente.return_value.messages.create.side_effect = [
            _respuesta(INVALIDO), _respuesta(VALIDO),
        ]
        redactar("contexto", "concreto")
        segunda = Cliente.return_value.messages.create.call_args_list[1]

    texto_enviado = str(segunda.kwargs["messages"])
    assert "guion largo" in texto_enviado


def test_contexto_educativo_incluye_titulo_y_angulo():
    ctx = contexto_educativo({
        "titulo": "Que es un CRM y por que tu Excel no lo es",
        "angulo": "el Excel no te avisa, no recuerda y no lo ve tu equipo",
    })
    assert "CRM" in ctx
    assert "Excel no te avisa" in ctx


def test_handler_arma_dos_educativos(tmp_path):
    """El mail programado es siempre dos educativos, con angulos distintos."""
    import sqlite3

    from database import init_db, seed_linkedin_temas
    from services.linkedin_posts import linkedin_job_handler

    db = str(tmp_path / "t.db")
    init_db(db)
    seed_linkedin_temas(db)

    with patch("services.linkedin_posts.redactar", return_value=VALIDO), \
         patch("services.linkedin_posts.frase_tarjeta", return_value="Una frase"):
        r = linkedin_job_handler(
            {"db_path": db, "lote": "l-test", "ahora": "2026-08-26T09:00:00"})

    assert [b["tipo"] for b in r["borradores"]] == ["educativo", "educativo"]
    assert r["borradores"][0]["fuente_id"] != r["borradores"][1]["fuente_id"]
    assert all(b["marcar_token"] for b in r["borradores"])

    conn = sqlite3.connect(db)
    angulos = [x[0] for x in conn.execute(
        "SELECT angulo FROM linkedin_posts WHERE lote = 'l-test'")]
    usados = conn.execute(
        "SELECT COUNT(*) FROM linkedin_temas WHERE usado_en IS NOT NULL").fetchone()[0]
    conn.close()
    assert sorted(angulos) == ["concreto", "implicancia"]
    assert usados == 2


def test_handler_manual_hace_un_solo_post(tmp_path):
    from database import init_db, seed_linkedin_temas
    from services.linkedin_posts import linkedin_job_handler

    db = str(tmp_path / "t.db")
    init_db(db)
    seed_linkedin_temas(db)

    # frase_tarjeta se mockea aparte: si no, este test sale a la API de verdad.
    with patch("services.linkedin_posts.redactar", return_value=VALIDO) as red,          patch("services.linkedin_posts.frase_tarjeta", return_value="Una frase"):
        r = linkedin_job_handler({
            "db_path": db, "lote": "l-man", "ahora": "2026-08-26T09:00:00",
            "contexto_manual": "Entregamos el sistema de stock de Lima Hnos.",
        })

    assert [b["tipo"] for b in r["borradores"]] == ["manual"]
    assert "Lima Hnos" in red.call_args.args[0]
    assert "Pedido a mano" in r["borradores"][0]["fuente_desc"]
    assert r["borradores"][0]["imagen_tipo"] == "tarjeta"


def test_handler_manual_con_url_usa_screenshot(tmp_path):
    from database import init_db
    from services.linkedin_posts import linkedin_job_handler

    db = str(tmp_path / "t.db")
    init_db(db)

    with patch("services.linkedin_posts.redactar", return_value=VALIDO), \
         patch("services.linkedin_posts.frase_tarjeta", return_value="Una frase"):
        r = linkedin_job_handler({
            "db_path": db, "lote": "l-man2", "ahora": "2026-08-26T09:00:00",
            "contexto_manual": "Salio la tienda de Biciconde.",
            "imagen_url": "https://biciconde.uy",
        })

    b = r["borradores"][0]
    assert b["imagen_tipo"] == "screenshot"
    assert b["imagen_spec"]["url"] == "https://biciconde.uy"


def test_handler_manual_no_toca_los_temas(tmp_path):
    """Un post a mano no puede quemar un tema educativo."""
    import sqlite3

    from database import init_db, seed_linkedin_temas
    from services.linkedin_posts import linkedin_job_handler

    db = str(tmp_path / "t.db")
    init_db(db)
    seed_linkedin_temas(db)

    with patch("services.linkedin_posts.redactar", return_value=VALIDO), \
         patch("services.linkedin_posts.frase_tarjeta", return_value="Una frase"):
        linkedin_job_handler({
            "db_path": db, "lote": "l-man3", "ahora": "2026-08-26T09:00:00",
            "contexto_manual": "Algo real que paso.",
        })

    conn = sqlite3.connect(db)
    usados = conn.execute(
        "SELECT COUNT(*) FROM linkedin_temas WHERE usado_en IS NOT NULL").fetchone()[0]
    conn.close()
    assert usados == 0


def test_handler_manda_mail_de_fallo_si_no_valida_ninguno(tmp_path):
    from database import init_db, seed_linkedin_temas
    from services.linkedin_posts import linkedin_job_handler

    db = str(tmp_path / "t.db")
    init_db(db)
    seed_linkedin_temas(db)

    with patch("services.linkedin_posts.redactar", return_value=None),          patch("services.linkedin_posts.send_linkedin_failure") as fallo:
        r = linkedin_job_handler(
            {"db_path": db, "lote": "l-test", "ahora": "2026-08-26T09:00:00"})

    assert r["borradores"] == []
    assert fallo.called


def test_contexto_manual_no_deja_inventar():
    from services.linkedin_posts import contexto_manual
    ctx = contexto_manual("  Entregamos el CRM de Jose.  ")
    assert "Entregamos el CRM de Jose." in ctx
    assert "No agregues ningún dato" in ctx


def test_el_prompt_no_encasilla_por_tamano():
    """El encuadre de PyME salio de un supuesto del prompt, no del modelo."""
    from services.linkedin_posts import SYSTEM_PROMPT, contexto_educativo
    assert "digitaliza PyMEs" not in SYSTEM_PROMPT
    assert "#PyMEs" in SYSTEM_PROMPT          # aparece como prohibicion
    assert "Encasillar al lector por tamaño" in SYSTEM_PROMPT
    assert "PyMEs uruguayas" not in contexto_educativo({"titulo": "T", "angulo": "A"})


def test_el_prompt_esta_escrito_con_tildes():
    """El modelo copia la ortografia del prompt: cuando el prompt no tenia
    tildes, los posts salian sin tildes."""
    from services.linkedin_posts import SYSTEM_PROMPT
    assert "Ortografía correcta" in SYSTEM_PROMPT
    for palabra in ("página", "gestión", "español", "máximo"):
        assert palabra in SYSTEM_PROMPT.lower(), palabra
