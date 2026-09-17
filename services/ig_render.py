"""Las imagenes de Instagram, dibujadas con Pillow segun el manual de marca.

Fly no tiene navegador (Playwright esta instalado sin Chromium), asi que las
piezas se dibujan a mano. Manual de marca de Scalerics: DM Sans, fondo oscuro
#0F2430, claro #EFEFEF, degradado A #1897D3 -> #052670, degradado B
#80CD2A -> #299849.

**La grilla manda.** Los fondos copian las piezas que armo el de marketing
(medidas el 17/9/2026 sobre las publicadas): verde oliva con mucha textura y
gris oscuro con textura, con o sin un recuadro casi negro adentro, y un negro
con brillo verdoso. Se suman los azules de la marca (las piezas de mayo y el
degradado A del manual), tambien con o sin recuadro, y el claro del manual
(#EFEFEF) con texto #0F2430, que la grilla usa de golpe de contraste. Texto en mayusculas centrado, palabras clave en verde y el
logo abajo al centro. Lo que rota de pieza en pieza es el fondo, segun el plan
del mes (`services/instagram.plan_del_mes`). Juan (17/9): "asi todo azul es
monotono".

Una diapositiva es un dict con `titulo` y, opcionales, `etiqueta` (arriba, en
chico), `texto` (debajo) y `cta` (boton al pie). Las palabras entre asteriscos
del titulo (`*asi*`) van en color de acento.
"""

import io
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FUENTE = os.path.join(_RAIZ, "static", "fonts", "DMSans.ttf")
_LOGO = os.path.join(_RAIZ, "static", "logo.png")

OSCURO = (15, 36, 48)
CLARO = (239, 239, 239)
AZUL_1, AZUL_2 = (24, 151, 211), (5, 38, 112)
VERDE_1, VERDE_2 = (128, 205, 42), (41, 152, 73)
GRIS = (170, 184, 192)

TAMANOS = {"feed": (1080, 1350), "historia": (1080, 1920)}

_BOTON = (VERDE_1, VERDE_2)
# "verde" era el azul noche de la primera version: el nombre queda para las
# filas guardadas y ahora es el verde con textura.
ESTILOS = {
    "verde": {"nombre": "Verde con textura", "fondo": "oliva", "tarjeta": False},
    "marco": {"nombre": "Verde con recuadro", "fondo": "oliva", "tarjeta": True},
    "grafito": {"nombre": "Gris con textura", "fondo": "gris", "tarjeta": False},
    "grafito_marco": {"nombre": "Gris con recuadro", "fondo": "gris", "tarjeta": True},
    "bruma": {"nombre": "Negro con brillo verde", "fondo": "bruma", "tarjeta": False},
    "azul": {"nombre": "Azul con textura", "fondo": "azul", "tarjeta": False},
    "azul_marco": {"nombre": "Azul con recuadro", "fondo": "azul", "tarjeta": True},
    "bruma_azul": {"nombre": "Negro con brillo azul", "fondo": "bruma_azul", "tarjeta": False},
    "blanco": {"nombre": "Blanco", "fondo": "blanco", "tarjeta": False, "claro": True},
    "blanco_marco": {"nombre": "Blanco con recuadro", "fondo": "gris_claro", "tarjeta": True,
                     "claro": True},
    "degradado": {"nombre": "Azul brillante", "fondo": "degradado", "tarjeta": False},
}
ESTILOS_FEED = ("verde", "marco", "grafito", "grafito_marco", "bruma",
                "azul", "azul_marco", "bruma_azul", "blanco", "blanco_marco")
ESTILOS_HISTORIA = ("verde", "grafito", "bruma", "azul", "bruma_azul", "blanco", "degradado")
VERDE_OSCURO = (41, 152, 73)
TEXTO_SUAVE_CLARO = (70, 88, 100)
TARJETA = (24, 26, 25)


def nombre_estilo(estilo: str) -> str:
    return (ESTILOS.get(estilo) or {}).get("nombre", estilo)


def estilos_para(formato: str) -> tuple:
    return ESTILOS_HISTORIA if formato == "historia" else ESTILOS_FEED


@lru_cache(maxsize=32)
def _fuente(peso: str, tamano: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(_FUENTE, tamano)
    f.set_variation_by_name(peso)
    return f


@lru_cache(maxsize=4)
def _logo(alto: int, negativo: bool = True) -> Image.Image:
    """Negativo: la palabra en #EFEFEF, el isotipo con sus colores. Si no, el
    logo tal cual, para fondos claros."""
    logo = Image.open(_LOGO).convert("RGBA")
    ancho = round(logo.width * alto / logo.height)
    logo = logo.resize((ancho, alto), Image.LANCZOS)
    if not negativo:
        return logo
    px = logo.load()
    for x in range(logo.width):
        for y in range(logo.height):
            r, g, b, a = px[x, y]
            if a and max(r, g, b) < 90 and max(r, g, b) - min(r, g, b) < 45:
                px[x, y] = (*CLARO, a)
    return logo


def _degradado(tamano, c1, c2) -> Image.Image:
    ancho, alto = tamano
    chico = Image.new("RGB", (64, 64))
    px = chico.load()
    for x in range(64):
        for y in range(64):
            t = (x + y) / 126
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))
    return chico.resize((ancho, alto), Image.BILINEAR)


def _brillo(base: Image.Image, color, fuerza: int) -> Image.Image:
    """Una luz suave arriba a la izquierda."""
    ancho, alto = base.size
    luz = Image.new("L", (ancho // 8, alto // 8), 0)
    ImageDraw.Draw(luz).ellipse(
        (-ancho // 16, -alto // 20, ancho // 8 + ancho // 16, alto // 16 + alto // 20), fill=fuerza)
    luz = luz.filter(ImageFilter.GaussianBlur(ancho // 40)).resize(base.size, Image.BILINEAR)
    return Image.composite(Image.new("RGB", base.size, color), base, luz)


def _con_grano(base: Image.Image, sigma: float) -> Image.Image:
    """Grano monocromo sumado, centrado: oscurece y aclara por igual."""
    from PIL import ImageChops
    ruido = Image.effect_noise(base.size, sigma).convert("RGB")
    return ImageChops.add(base, ruido, 1.0, -128)


@lru_cache(maxsize=12)
def _fondo(tipo: str, tamano) -> Image.Image:
    ancho, alto = tamano
    if tipo == "degradado":
        return _degradado(tamano, AZUL_1, AZUL_2)
    if tipo == "oliva":
        base = _brillo(Image.new("RGB", tamano, (84, 94, 76)), (108, 118, 90), 70)
        puntos = Image.new("L", tamano, 0)
        d = ImageDraw.Draw(puntos)
        for y in range(0, alto, 8):
            for x in range(4 if y % 16 else 0, ancho, 8):
                d.ellipse((x - 1.4, y - 1.4, x + 1.4, y + 1.4), fill=150)
        base = Image.composite(Image.new("RGB", tamano, (58, 66, 54)), base, puntos)
        return _con_grano(base, 26)
    if tipo == "gris":
        base = _brillo(Image.new("RGB", tamano, (44, 43, 44)), (62, 61, 62), 60)
        return _con_grano(base, 30)
    if tipo in ("bruma", "bruma_azul"):
        color = (46, 74, 62) if tipo == "bruma" else (20, 72, 118)
        base = Image.new("RGB", tamano, (10, 12, 14))
        luz = Image.new("L", (ancho // 8, alto // 8), 0)
        ImageDraw.Draw(luz).ellipse((-ancho // 16, -alto // 10, ancho // 8 + ancho // 16, alto // 18),
                                    fill=150)
        luz = luz.filter(ImageFilter.GaussianBlur(ancho // 45)).resize(tamano, Image.BILINEAR)
        base = Image.composite(Image.new("RGB", tamano, color), base, luz)
        return _con_grano(base, 14)
    if tipo == "blanco":
        return _con_grano(Image.new("RGB", tamano, CLARO), 6)
    if tipo == "gris_claro":
        return _con_grano(Image.new("RGB", tamano, (214, 216, 214)), 10)
    if tipo == "azul":
        base = _degradado(tamano, (14, 44, 96), (5, 14, 34))
        base = _brillo(base, (24, 80, 140), 60)
        return _con_grano(base, 16)
    return _con_grano(Image.new("RGB", tamano, OSCURO), 14)


@lru_cache(maxsize=4)
def _tarjeta(tamano, claro: bool = False) -> tuple:
    """El recuadro (casi negro, o blanco en los claros) con grano fino y su mascara."""
    ancho, alto = tamano
    tarjeta = _con_grano(Image.new("RGB", tamano, (250, 250, 249) if claro else TARJETA), 4 if claro else 8)
    mascara = Image.new("L", tamano, 0)
    ImageDraw.Draw(mascara).rounded_rectangle((0, 0, ancho - 1, alto - 1), radius=4, fill=255)
    return tarjeta, mascara


def _palabras(texto: str, mayusculas: bool):
    """[(palabra, resaltada)] a partir de los asteriscos."""
    salida, resaltado = [], False
    for crudo in (texto or "").split():
        letras, marcada = [], False
        for ch in crudo:
            if ch == "*":
                resaltado = not resaltado
                continue
            letras.append(ch)
            if resaltado and ch.isalnum():
                marcada = True
        limpio = "".join(letras)
        if mayusculas:
            limpio = limpio.upper()
        if limpio:
            salida.append((limpio, marcada))
    return salida


def _ancho(linea, f):
    return f.getlength(" ".join(w for w, _ in linea))


def _partir(palabras, fuente, ancho_max):
    lineas, actual = [], []
    for p in palabras:
        if actual and _ancho(actual + [p], fuente) > ancho_max:
            lineas.append(actual)
            actual = [p]
        else:
            actual.append(p)
    if actual:
        lineas.append(actual)
    return lineas


def _ajustar(texto, peso, grande, chico, ancho_max, max_lineas, mayusculas=False):
    palabras = _palabras(texto, mayusculas)
    for tam in range(grande, chico - 1, -4):
        f = _fuente(peso, tam)
        lineas = _partir(palabras, f, ancho_max)
        if len(lineas) <= max_lineas and all(_ancho(l, f) <= ancho_max for l in lineas):
            return f, lineas
    f = _fuente(peso, chico)
    return f, _partir(palabras, f, ancho_max)


def _interlineado(f, titulo):
    return round(f.size * (1.08 if titulo else 1.32))


def _centrado(d, lineas, f, centro, y, color, acento, titulo):
    for linea in lineas:
        x = centro - _ancho(linea, f) / 2
        for i, (palabra, resaltada) in enumerate(linea):
            txt = palabra + (" " if i < len(linea) - 1 else "")
            d.text((x, y), txt, font=f, fill=acento if resaltada else color)
            x += f.getlength(txt)
        y += _interlineado(f, titulo)
    return y


def dibujar(slide: dict, estilo: str = "verde", formato: str = "feed",
            numero: int | None = None, total: int | None = None) -> bytes:
    """Una diapositiva a JPEG."""
    if estilo not in estilos_para(formato):
        estilo = "verde"
    claro = ESTILOS[estilo].get("claro", False)
    e = {**ESTILOS[estilo], "acento": VERDE_OSCURO if claro else VERDE_1, "boton": _BOTON}
    color_titulo = OSCURO if claro else CLARO
    color_texto = TEXTO_SUAVE_CLARO if claro else (205, 214, 219)
    ancho, alto = TAMANOS["historia" if formato == "historia" else "feed"]
    img = _fondo(e["fondo"], (ancho, alto)).copy()
    d = ImageDraw.Draw(img)
    centro = ancho // 2
    util = ancho - 2 * 110
    arriba = 250 if formato == "historia" else 80
    abajo = alto - (340 if formato == "historia" else 70)
    borde = 80
    if e["tarjeta"]:
        # Un recuadro casi negro adentro, con el contenido y el logo dentro.
        m = 52
        tarjeta, mascara = _tarjeta((ancho - 2 * m, alto - 2 * m), claro)
        img.paste(tarjeta, (m, m), mascara)
        util = ancho - 2 * 145
        arriba, abajo, borde = m + 40, alto - m - 36, m + 36

    # Logo abajo al centro, como en el feed actual.
    logo = _logo(50, not claro)
    img.paste(logo, (centro - logo.width // 2, abajo - logo.height), logo)

    f_chica = _fuente("Medium", 30)
    if total and total > 1:
        txt = f"{numero}/{total}"
        d.text((ancho - borde - f_chica.getlength(txt), arriba), txt, font=f_chica,
               fill=TEXTO_SUAVE_CLARO if claro else GRIS)
        if numero < total:
            txt = "DESLIZÁ →"
            d.text((ancho - borde - f_chica.getlength(txt), abajo - 38), txt,
                   font=f_chica, fill=e["acento"])

    es_portada = numero in (None, 1)
    max_lineas = 6 if formato == "historia" else 5
    f_tit, l_tit = _ajustar(slide.get("titulo", ""), "Black",
                            124 if es_portada else 96, 56, util, max_lineas, True)
    partes = []
    if slide.get("etiqueta"):
        partes.append(("etiqueta", _fuente("Bold", 34),
                       [[(slide["etiqueta"].upper(), False)]]))
    partes.append(("titulo", f_tit, l_tit))
    if slide.get("texto"):
        f_tx, l_tx = _ajustar(slide["texto"], "Medium", 44, 30, util - 60,
                              10 if formato == "historia" else 7)
        partes.append(("texto", f_tx, l_tx))
    if slide.get("cta"):
        partes.append(("cta", _fuente("Bold", 40), None))

    separacion = {"etiqueta": 26, "titulo": 44, "texto": 0, "cta": 0}

    def alto_de(tipo, f, lineas):
        if tipo == "cta":
            return 56 + 100
        return len(lineas) * _interlineado(f, tipo == "titulo") + separacion[tipo]

    total_alto = sum(alto_de(*p) for p in partes)
    zona_arriba, zona_abajo = arriba + 70, abajo - 110
    y = zona_arriba + max(0, (zona_abajo - zona_arriba - total_alto) // 2)

    for tipo, f, lineas in partes:
        if tipo == "cta":
            y += 56
            txt = slide["cta"]
            w = round(f.getlength(txt)) + 110
            boton = _degradado((w, 100), *e["boton"])
            mascara = Image.new("L", (w, 100), 0)
            ImageDraw.Draw(mascara).rounded_rectangle((0, 0, w - 1, 99), radius=50, fill=255)
            img.paste(boton, (centro - w // 2, y), mascara)
            d.text((centro, y + 50), txt, font=f, fill=OSCURO, anchor="mm")
            y += 100
            continue
        color = {"etiqueta": e["acento"], "titulo": color_titulo, "texto": color_texto}[tipo]
        if e["fondo"] == "degradado" and tipo == "texto":
            color = CLARO
        y = _centrado(d, lineas, f, centro, y, color, e["acento"], tipo == "titulo")
        y += separacion[tipo]

    salida = io.BytesIO()
    img.save(salida, "JPEG", quality=92, optimize=True)
    return salida.getvalue()


def dibujar_publicacion(formato: str, slides: list[dict], estilo: str) -> list[bytes]:
    """Todas las imagenes de una publicacion, en orden."""
    if formato == "historia":
        return [dibujar(slides[0], estilo, "historia")]
    if formato == "imagen":
        return [dibujar(slides[0], estilo, "feed")]
    total = len(slides)
    return [dibujar(s, estilo, "feed", i + 1, total) for i, s in enumerate(slides)]
