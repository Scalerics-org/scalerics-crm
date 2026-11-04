"""Publicar en la pagina de LinkedIn de Scalerics con la Community Management API.

La app "Scalerics - Community Management" tiene aprobado el Development Tier
(4/11/2026). Con un token de un admin de la pagina y el permiso
`w_organization_social` se puede publicar como la empresa.

Un posteo con imagen son tres llamadas: pedir lugar para la imagen
(images?action=initializeUpload), subir el PNG a la URL que devuelve y crear el
post (posts) apuntando a esa imagen.

El texto va en el formato "little text" de LinkedIn: los caracteres reservados
se escapan con barra invertida y los hashtags van como {hashtag|\\#|Tag}. Si no
se escapan, LinkedIn corta el texto en silencio.
"""

import re
from datetime import date

import requests

API = "https://api.linkedin.com/rest"
TIMEOUT = 30

# LinkedIn versiona la API por mes (AAAAMM) y da de baja las versiones de mas
# de un año. Arrancamos dos meses atras y, si una version ya no existe,
# probamos la anterior.
_MESES_HACIA_ATRAS = range(2, 10)

_RESERVADOS = re.compile(r"([\\|{}@\[\]()<>#*_~])")
_HASHTAG = re.compile(r"(?<![\w#])#(\w+)")


class LinkedInError(Exception):
    def __init__(self, mensaje: str, status: int | None = None):
        super().__init__(mensaje)
        self.status = status


def versiones(hoy: date | None = None) -> list[str]:
    hoy = hoy or date.today()
    salida = []
    for atras in _MESES_HACIA_ATRAS:
        mes = hoy.month - atras
        anio = hoy.year
        while mes <= 0:
            mes += 12
            anio -= 1
        salida.append(f"{anio}{mes:02d}")
    return salida


def little_text(texto: str) -> str:
    """Escapa el texto para el campo commentary y convierte los hashtags."""
    partes = []
    ultimo = 0
    for m in _HASHTAG.finditer(texto):
        partes.append(_RESERVADOS.sub(r"\\\1", texto[ultimo:m.start()]))
        partes.append("{hashtag|\\#|" + _RESERVADOS.sub(r"\\\1", m.group(1)) + "}")
        ultimo = m.end()
    partes.append(_RESERVADOS.sub(r"\\\1", texto[ultimo:]))
    return "".join(partes)


class Cliente:
    def __init__(self, token: str, sesion: requests.Session | None = None):
        self.token = token
        self.http = sesion or requests.Session()
        self.version: str | None = None

    def _headers(self, version: str) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "LinkedIn-Version": version,
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def _pedir(self, metodo: str, ruta: str, **kw) -> requests.Response:
        candidatas = [self.version] if self.version else versiones()
        for version in candidatas:
            r = self.http.request(metodo, API + ruta, headers=self._headers(version),
                                  timeout=TIMEOUT, **kw)
            if r.status_code == 426 or (r.status_code == 400 and "VERSION" in r.text.upper()):
                continue
            self.version = version
            if r.status_code >= 400:
                raise LinkedInError(f"{metodo} {ruta}: {r.status_code} {r.text[:500]}", r.status_code)
            return r
        raise LinkedInError("LinkedIn no acepto ninguna version de la API", 426)

    def paginas_admin(self) -> list[str]:
        """URNs de las paginas donde el dueño del token es administrador."""
        r = self._pedir("GET", "/organizationAcls",
                        params={"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED"})
        return [e["organization"] for e in r.json().get("elements", []) if e.get("organization")]

    def nombre_pagina(self, org_urn: str) -> str:
        org_id = org_urn.rsplit(":", 1)[-1]
        r = self._pedir("GET", f"/organizations/{org_id}")
        return r.json().get("localizedName") or org_urn

    def subir_imagen(self, org_urn: str, png: bytes) -> str:
        r = self._pedir("POST", "/images", params={"action": "initializeUpload"},
                        json={"initializeUploadRequest": {"owner": org_urn}})
        valor = r.json()["value"]
        subida = self.http.put(valor["uploadUrl"], data=png, timeout=TIMEOUT,
                               headers={"Authorization": f"Bearer {self.token}"})
        if subida.status_code >= 400:
            raise LinkedInError(f"subida de imagen: {subida.status_code} {subida.text[:300]}",
                                subida.status_code)
        return valor["image"]

    def publicar(self, org_urn: str, texto: str, imagen_urn: str | None = None,
                 alt: str = "") -> str:
        """Crea el post y devuelve su URN (urn:li:share:... o urn:li:ugcPost:...)."""
        cuerpo = {
            "author": org_urn,
            "commentary": little_text(texto),
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if imagen_urn:
            cuerpo["content"] = {"media": {"id": imagen_urn, "altText": alt[:4000]}}
        r = self._pedir("POST", "/posts", json=cuerpo)
        return r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id") or ""


def url_del_post(post_urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{post_urn}/"
