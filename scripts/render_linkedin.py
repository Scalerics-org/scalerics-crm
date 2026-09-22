"""Renderiza las imagenes de los borradores de LinkedIn.

Corre en el runner de GitHub Actions, no en Fly: la imagen de Fly instala el
paquete pip de Playwright pero no los binarios de los navegadores, y la VM
tiene 256 MB.

Uso:
    python scripts/render_linkedin.py --crm https://scalerics-crm.fly.dev \
        --token "$ADMIN_TOKEN" --job-id 123
"""

import argparse
import base64
import html as html_mod
import hashlib
import json
import os
import sys
import time

import requests

ANCHO = 1200
ALTO = 627
PLANTILLA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates", "linkedin_card.html",
)


# Los cuatro fondos son discretos a proposito: son una decision de marca, no
# un parametro. Todo lo demas (posicion y tamano de los glows, angulo del
# filete) se deriva continuo del hash, porque con valores discretos 84 tarjetas
# en 128 combinaciones colisionan por cumpleanos: la primera version dejaba 21
# repetidas.
_FONDOS = (
    "#0f2430",  # navy de los assets
    "#0b1d35",  # base del sitio
    "#0a1620",  # el navy bajado, casi negro
    "#122c3a",  # el navy subido
)


def _entre(byte, desde, hasta):
    """Un valor del rango, derivado del byte. Determinista."""
    return desde + (byte * (hasta - desde)) // 255


def variante(frase: str) -> str:
    """CSS que le da a cada tarjeta su propia atmosfera.

    Las 84 tarjetas del banco eran identicas salvo el texto, y dos veces por
    semana durante cinco meses eso se lee como plantilla y la gente aprende a
    saltearlo. La semilla es la frase porque es unica por post, asi no hay que
    tocar la base ni pasar el tema hasta aca.

    Se hashea con md5 y no con hash(): el hash de Python lleva una sal distinta
    por proceso, asi que la misma frase daria una tarjeta distinta en cada
    corrida y el reenvio de un mail no coincidiria con el original.

    Los glows quedan siempre sobre el borde derecho y salidos del lienzo. El
    texto arranca a 90px del borde izquierdo, y estos degradados son de opacidad
    muy baja: acompanan el fondo, no compiten con la frase.
    """
    h = hashlib.md5(frase.encode("utf-8")).digest()
    return (
        "body{background:%s;}"
        ".glow{top:%dpx; right:%dpx; width:%dpx; height:%dpx;}"
        ".glow2{bottom:%dpx; right:%dpx; width:%dpx; height:%dpx;}"
        ".rule{background:linear-gradient(%ddeg,#1796d2 0%%,#7fcc2a 100%%);}"
    ) % (
        _FONDOS[h[0] % len(_FONDOS)],
        _entre(h[1], -340, -80), _entre(h[2], -280, 200),
        _entre(h[3], 600, 780), _entre(h[3], 600, 780),
        _entre(h[4], -340, -140), _entre(h[5], -200, 300),
        _entre(h[6], 440, 660), _entre(h[6], 440, 660),
        _entre(h[7], 0, 90),
    )


def armar_tarjeta(plantilla_html: str, frase: str) -> str:
    return (plantilla_html
            .replace("{{FRASE}}", html_mod.escape(frase))
            .replace("{{VARIANTE}}", variante(frase)))


def renderizar(borradores: list, plantilla_html: str, page) -> list:
    """Un PNG en base64 por borrador que tenga imagen.

    Una falla de render deja a ese borrador sin imagen y sigue con el resto:
    un mail sin foto sirve, un mail que no llega no.
    """
    salida = []
    for b in borradores:
        tipo = b.get("imagen_tipo", "ninguna")
        spec = b.get("imagen_spec") or {}
        if tipo == "ninguna":
            continue

        try:
            if tipo == "screenshot":
                url = spec.get("url", "")
                if not url:
                    continue
                page.goto(url, wait_until="networkidle", timeout=30000)
            else:
                page.set_content(armar_tarjeta(plantilla_html, spec.get("frase", "")),
                                 wait_until="networkidle")
            png = page.screenshot()
        except Exception as e:  # noqa: BLE001 - queremos seguir con los demas
            print(f"no se pudo renderizar el borrador {b.get('id')}: {e}", file=sys.stderr)
            continue

        salida.append({"id": b["id"], "png_b64": base64.b64encode(png).decode()})

    return salida


def esperar_job(crm: str, token: str, job_id: int, timeout_s: int = 900,
                espera_s: int = 10) -> dict:
    """Espera a que el job termine, aguantando cortes en el medio.

    Antes, un solo 502 —el CRM reiniciando, un deploy justo a las 8:00, un
    hipo de red del runner— mataba la corrida entera aunque el job estuviera
    andando perfecto del otro lado. Ahora un error de consulta no decide nada:
    solo lo decide el reloj. Lo unico que corta antes de tiempo es que el
    propio job diga que fallo, porque eso no mejora esperando.
    """
    headers = {"x-admin-token": token}
    limite = time.time() + timeout_s
    ultimo_error = None
    while time.time() < limite:
        try:
            r = requests.get(f"{crm}/api/jobs/{job_id}", headers=headers, timeout=30)
            r.raise_for_status()
            job = r.json()
        except Exception as e:  # noqa: BLE001 - transitorio hasta que se acabe el plazo
            ultimo_error = e
            print(f"consulta al job fallo, reintento: {e}", file=sys.stderr)
            time.sleep(espera_s)
            continue

        if job.get("status") == "completed":
            return json.loads(job.get("result") or "{}")
        if job.get("status") == "failed":
            raise RuntimeError(f"el job fallo: {job.get('error_message')}")
        time.sleep(espera_s)

    if ultimo_error is not None:
        raise TimeoutError(
            f"el job {job_id} no termino en {timeout_s}s; ultimo error: {ultimo_error}")
    raise TimeoutError(f"el job {job_id} no termino en {timeout_s}s")


def postear_con_reintentos(url: str, token: str, cuerpo: dict, intentos: int = 4):
    """POST que reintenta lo transitorio.

    Un 4xx no se reintenta: si el cuerpo esta mal o el token no sirve, insistir
    solo repite el mismo error mas tarde.
    """
    espera = 5
    for intento in range(1, intentos + 1):
        try:
            r = requests.post(url, headers={"x-admin-token": token}, json=cuerpo,
                              timeout=120)
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError as e:
            codigo = e.response.status_code if e.response is not None else 0
            if 400 <= codigo < 500:
                raise
            if intento == intentos:
                raise
            print(f"intento {intento} fallo ({codigo}), reintento en {espera}s",
                  file=sys.stderr)
        except Exception as e:  # noqa: BLE001 - red, timeout, corte
            if intento == intentos:
                raise
            print(f"intento {intento} fallo ({e}), reintento en {espera}s",
                  file=sys.stderr)
        time.sleep(espera)
        espera *= 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crm", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--job-id", type=int, required=True)
    args = parser.parse_args()

    resultado = esperar_job(args.crm, args.token, args.job_id)
    # "rerender" son borradores viejos que "Otra idea" o una correccion de
    # Claude cambiaron en el panel y quedaron sin tarjeta: se dibujan en esta
    # misma pasada, pero no van en el mail (ver linkedin_job_handler).
    borradores = resultado.get("borradores", []) + resultado.get("rerender", [])
    if not borradores:
        print("el job no dejo borradores; no hay nada que renderizar")
        return 0

    with open(PLANTILLA_PATH, encoding="utf-8") as f:
        plantilla = f.read()

    # Mismo criterio que `renderizar` pero un nivel mas arriba: si el navegador
    # ni siquiera arranca (falta un binario, se quedo sin memoria el runner),
    # el mail sale igual sin imagenes. Un mail sin foto sirve; uno que no llega
    # porque no habia con que sacar una captura, no.
    imagenes = []
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": ANCHO, "height": ALTO},
                                    device_scale_factor=1)
            imagenes = renderizar(borradores, plantilla, page)
            browser.close()
    except Exception as e:  # noqa: BLE001 - el mail vale mas que las imagenes
        print(f"no se pudo abrir el navegador, el mail sale sin imagenes: {e}",
              file=sys.stderr)

    r = postear_con_reintentos(
        f"{args.crm}/api/linkedin/enviar",
        args.token,
        {
            "lote": resultado["lote"],
            "imagenes": imagenes,
            "aviso_cooldown": resultado.get("aviso_cooldown", False),
        },
    )
    print(f"mail enviado: {r.json()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
