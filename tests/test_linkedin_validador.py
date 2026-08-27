from services.linkedin_posts import validar_borrador


BUENO = (
    "Esta semana armamos la tienda online de una bloquera de Durazno. "
    "Tres dias de trabajo, catalogo con 40 productos y pago con Mercado Pago. "
    "Lo que mas costo no fue el codigo, fue ordenar los precios: tenian tres "
    "listas distintas segun el vendedor que atendiera. Eso pasa mas seguido "
    "de lo que parece, y es la parte que de verdad cambia el negocio. "
    "#Uruguay #PyMEs"
)


def test_un_texto_bien_escrito_pasa():
    assert validar_borrador(BUENO) == []


def test_rechaza_el_guion_largo():
    violaciones = validar_borrador(BUENO.replace("codigo, fue", "codigo — fue"))
    assert any("guion largo" in v for v in violaciones)


def test_rechaza_el_guion_medio():
    violaciones = validar_borrador(BUENO.replace("codigo, fue", "codigo – fue"))
    assert any("guion medio" in v for v in violaciones)


def test_rechaza_emojis():
    violaciones = validar_borrador(BUENO + " \U0001F680")
    assert any("emoji" in v for v in violaciones)


def test_rechaza_mas_de_dos_hashtags():
    violaciones = validar_borrador(BUENO + " #Software #Tecnologia")
    assert any("hashtag" in v for v in violaciones)


def test_rechaza_texto_largo():
    violaciones = validar_borrador("a" * 1301)
    assert any("largo" in v for v in violaciones)


def test_rechaza_texto_corto():
    violaciones = validar_borrador("Muy corto.")
    assert any("corto" in v for v in violaciones)


def test_acumula_varias_violaciones():
    violaciones = validar_borrador("Corto — \U0001F600")
    assert len(violaciones) >= 3


def test_rechaza_la_primera_persona_del_singular():
    """La frase real que salio en el primer mail: los posts los publica la
    pagina de empresa, no Juan."""
    texto = BUENO.replace(
        "Lo que mas costo no fue el codigo,",
        "Lo que me interesa de esto no es la ficha, no fue el codigo,")
    violaciones = validar_borrador(texto)
    assert any("primera persona" in v for v in violaciones), violaciones


def test_rechaza_yo_y_los_posesivos():
    for marcador in ("yo creo que", "mi cliente pidio", "mis clientes saben",
                     "vino conmigo a la reunion"):
        texto = BUENO.replace("Tres dias de trabajo,", marcador + ",")
        assert any("primera persona" in v for v in validar_borrador(texto)), marcador


def test_el_plural_y_lo_impersonal_pasan():
    texto = BUENO.replace("Lo que mas costo", "Lo que mas nos costo")
    assert validar_borrador(texto) == []


def test_no_confunde_palabras_que_contienen_los_marcadores():
    """'mismo', 'mientras', 'mercado', 'yogur' contienen los marcadores como
    subcadena; el limite de palabra tiene que salvarlos."""
    texto = (
        "Mientras el mismo producto tenga tres precios en tres lugares, el "
        "mercado no te va a acompanar. Lo vimos en un almacen que vende yogur "
        "artesanal: el mismo pote costaba distinto segun quien atendiera el "
        "mostrador. Meter orden ahi no es un proyecto de software, es escribir "
        "una lista de precios y que todos miren la misma. Despues si, el "
        "sistema la muestra sola y nadie tiene que acordarse de nada."
    )
    assert validar_borrador(texto) == [], validar_borrador(texto)
