"""La tarjeta de LinkedIn, dibujada con Pillow en el mismo CRM.

Hasta el 28/9 la dibujaba solo el runner de GitHub (Chromium con
`templates/linkedin_card.html`), dos veces por semana. Todo borrador que
cambiaba en el panel ("Otra idea", una correccion de Claude, "Generar")
quedaba sin foto hasta la corrida siguiente, y los que no tenian `frase`
guardada no la conseguian nunca. Juan (28/9): la foto tiene que estar abajo
del texto de cada borrador de la semana.

Copia la plantilla HTML: 1200x627, fondo navy, dos brillos sobre el borde
derecho, logo arriba, la frase grande, el filete azul->verde y scalerics.com
al pie. La atmosfera de cada tarjeta sale de `variante()` del script del
runner, con la misma semilla (la frase), asi la misma frase da el mismo fondo.
La letra es DM Sans (la del manual de marca y la de Instagram) porque Fly no
tiene Raleway.
"""

import hashlib
import io

from PIL import Image, ImageDraw, ImageFilter

from services.ig_render import CLARO, _fuente, _logo

ANCHO, ALTO = 1200, 627
MARGEN = 90
_FONDOS = ((15, 36, 48), (11, 29, 53), (10, 22, 32), (18, 44, 58))
_AZUL = (23, 150, 210)
_VERDE = (127, 204, 42)


def _entre(byte, desde, hasta):
    return desde + (byte * (hasta - desde)) // 255


def _brillo(base: Image.Image, color, caja, alfa: int) -> Image.Image:
    """Un circulo de luz difuso, como el radial-gradient de la plantilla."""
    escala = 4
    mascara = Image.new("L", (ANCHO // escala, ALTO // escala), 0)
    x0, y0, x1, y1 = (v // escala for v in caja)
    ImageDraw.Draw(mascara).ellipse((x0, y0, x1, y1), fill=alfa)
    radio = max(1, (x1 - x0) // 5)
    mascara = mascara.filter(ImageFilter.GaussianBlur(radio)).resize((ANCHO, ALTO), Image.BILINEAR)
    return Image.composite(Image.new("RGB", (ANCHO, ALTO), color), base, mascara)


def _lineas(frase: str, fuente, ancho_max: int) -> list[str]:
    lineas, actual = [], ""
    for palabra in frase.split():
        prueba = (actual + " " + palabra).strip()
        if actual and fuente.getlength(prueba) > ancho_max:
            lineas.append(actual)
            actual = palabra
        else:
            actual = prueba
    if actual:
        lineas.append(actual)
    return lineas


def dibujar(frase: str) -> bytes:
    """PNG de la tarjeta. La frase ya viene recortada a MAX_FRASE."""
    frase = " ".join((frase or "").split())
    h = hashlib.md5(frase.encode("utf-8")).digest()

    img = Image.new("RGB", (ANCHO, ALTO), _FONDOS[h[0] % len(_FONDOS)])
    lado = _entre(h[3], 600, 780)
    arriba, derecha = _entre(h[1], -340, -80), _entre(h[2], -280, 200)
    img = _brillo(img, _AZUL, (ANCHO - derecha - lado, arriba, ANCHO - derecha, arriba + lado), 60)
    lado2 = _entre(h[6], 440, 660)
    abajo, derecha2 = _entre(h[4], -340, -140), _entre(h[5], -200, 300)
    img = _brillo(img, _VERDE, (ANCHO - derecha2 - lado2, ALTO - abajo - lado2, ANCHO - derecha2, ALTO - abajo), 28)

    d = ImageDraw.Draw(img)
    # La frase: 62px como la plantilla, mas chica si no entra en tres lineas.
    ancho_max = 930
    for tam in range(62, 39, -4):
        f = _fuente("Bold", tam)
        lineas = _lineas(frase, f, ancho_max)
        if len(lineas) <= 3:
            break
    interlineado = round(f.size * 1.13)
    logo = _logo(38)
    bloque = logo.height + 40 + interlineado * len(lineas) + 38 + 4
    y = (ALTO - bloque) // 2

    img.paste(logo, (MARGEN, y), logo)
    y += logo.height + 40
    for linea in lineas:
        d.text((MARGEN, y), linea, font=f, fill=CLARO)
        y += interlineado
    y += 38

    # El filete: 96x4, degradado azul -> verde.
    for x in range(96):
        t = x / 95
        color = tuple(round(a + (b - a) * t) for a, b in zip(_AZUL, _VERDE))
        d.line((MARGEN + x, y, MARGEN + x, y + 3), fill=color)

    pie = _fuente("Regular", 17)
    d.text((MARGEN, ALTO - 54 - 17), "scalerics.com", font=pie, fill=(160, 170, 175))

    salida = io.BytesIO()
    img.save(salida, "PNG", optimize=True)
    return salida.getvalue()
