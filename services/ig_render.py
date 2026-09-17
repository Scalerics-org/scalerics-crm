"""Las imagenes de Instagram, dibujadas con Pillow segun el manual de marca.

Fly no tiene navegador (Playwright esta instalado sin Chromium), asi que las
piezas se dibujan a mano. Manual de marca de Scalerics: DM Sans, fondo oscuro
#0F2430, claro #EFEFEF, degradado A #1897D3 -> #052670, degradado B
#80CD2A -> #299849.

**La grilla manda.** El feed de @scalerics_ (analizado el 17/9/2026) es fondo
oscuro con textura, titulo en mayusculas centrado, palabras clave en verde y el
logo chico abajo al centro. Todas las piezas de feed salen de esa familia: lo
unico que cambia es el color de acento. Los fondos claros o de color van solo
en historias, que no aparecen en la grilla.

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

# Feed: siempre fondo oscuro; cambia el acento. Historias: ademas degradado.
ESTILOS = {
    "verde": {"fondo": "oscuro", "acento": VERDE_1, "boton": (VERDE_1, VERDE_2)},
    "azul": {"fondo": "oscuro", "acento": AZUL_1, "boton": (AZUL_1, AZUL_2)},
    "degradado": {"fondo": "degradado", "acento": VERDE_1, "boton": (VERDE_1, VERDE_2)},
}
# El azul existe pero el feed va en verde: es la linea que ya tiene la grilla.
ESTILOS_FEED = ("verde",)
ESTILOS_HISTORIA = ("verde", "degradado")


def estilos_para(formato: str) -> tuple:
    return ESTILOS_HISTORIA if formato == "historia" else ESTILOS_FEED


@lru_cache(maxsize=32)
def _fuente(peso: str, tamano: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(_FUENTE, tamano)
    f.set_variation_by_name(peso)
    return f


@lru_cache(maxsize=4)
def _logo(alto: int) -> Image.Image:
    """Version negativa: la palabra en #EFEFEF, el isotipo con sus colores."""
    logo = Image.open(_LOGO).convert("RGBA")
    ancho = round(logo.width * alto / logo.height)
    logo = logo.resize((ancho, alto), Image.LANCZOS)
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


@lru_cache(maxsize=4)
def _fondo_oscuro(tamano) -> Image.Image:
    """#0F2430 con un brillo suave arriba y grano, como el feed actual."""
    ancho, alto = tamano
    base = Image.new("RGB", tamano, OSCURO)
    brillo = Image.new("L", (ancho // 8, alto // 8), 0)
    ImageDraw.Draw(brillo).ellipse(
        (-ancho // 16, -alto // 20, ancho // 8 + ancho // 16, alto // 16 + alto // 20), fill=90)
    brillo = brillo.filter(ImageFilter.GaussianBlur(ancho // 40)).resize(tamano, Image.BILINEAR)
    base = Image.composite(Image.new("RGB", tamano, (38, 70, 88)), base, brillo)
    grano = Image.effect_noise(tamano, 22).convert("RGB")
    return Image.blend(base, grano, 0.09)


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
    e = ESTILOS.get(estilo) or ESTILOS["verde"]
    if formato != "historia" and e["fondo"] != "oscuro":
        e = ESTILOS["verde"]
    ancho, alto = TAMANOS["historia" if formato == "historia" else "feed"]
    if e["fondo"] == "degradado":
        img = _degradado((ancho, alto), AZUL_1, AZUL_2)
    else:
        img = _fondo_oscuro((ancho, alto)).copy()
    d = ImageDraw.Draw(img)
    centro = ancho // 2
    util = ancho - 2 * 110
    arriba = 250 if formato == "historia" else 80
    abajo = alto - (340 if formato == "historia" else 70)

    # Logo abajo al centro, como en el feed actual.
    logo = _logo(50)
    img.paste(logo, (centro - logo.width // 2, abajo - logo.height), logo)

    f_chica = _fuente("Medium", 30)
    if total and total > 1:
        txt = f"{numero}/{total}"
        d.text((ancho - 80 - f_chica.getlength(txt), arriba), txt, font=f_chica, fill=GRIS)
        if numero < total:
            txt = "DESLIZÁ →"
            d.text((ancho - 80 - f_chica.getlength(txt), abajo - 38), txt,
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
            color = OSCURO if e["boton"][0] == VERDE_1 else CLARO
            d.text((centro, y + 50), txt, font=f, fill=color, anchor="mm")
            y += 100
            continue
        color = {"etiqueta": e["acento"], "titulo": CLARO,
                 "texto": (205, 214, 219)}[tipo]
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
