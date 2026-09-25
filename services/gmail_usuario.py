"""El Gmail de cada usuario del CRM, para mandar mails desde su propia casilla
(pedido de Juan, 25/9): si manda Lucas sale de la de Lucas, si manda Juan de la
de Juan. Queda en sus Enviados y las respuestas le llegan a él.

Cada uno conecta su cuenta una vez (OAuth con el permiso `gmail.send`, que solo
deja mandar: no lee la casilla). El refresh token se guarda cifrado con
`services.credenciales` en `usuarios_gmail`.

El cliente OAuth es uno *web* aparte del que usa Calendly (aquel es de
escritorio y no acepta una URL de vuelta del CRM): GMAIL_WEB_CLIENT_ID y
GMAIL_WEB_CLIENT_SECRET, secrets de Fly.
"""

import base64
import os
import sqlite3
from datetime import datetime
from email.headerregistry import Address
from email.message import EmailMessage
from urllib.parse import urlencode

import requests
from cryptography.fernet import InvalidToken

from services import credenciales

SCOPES = ["https://www.googleapis.com/auth/gmail.send", "openid", "email"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class Desconectado(RuntimeError):
    """El usuario no conectó su Gmail, o Google revocó el permiso."""


def _cliente() -> tuple[str, str]:
    cid = os.environ.get("GMAIL_WEB_CLIENT_ID", "").strip()
    sec = os.environ.get("GMAIL_WEB_CLIENT_SECRET", "").strip()
    return cid, sec


def configurado() -> bool:
    return all(_cliente())


def init_gmail_usuario(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usuarios_gmail (
            user_id        INTEGER PRIMARY KEY,
            email          TEXT NOT NULL,
            refresh_token  TEXT NOT NULL,
            conectado_en   TEXT
        )
    """)


def _conn(db: str) -> sqlite3.Connection:
    c = sqlite3.connect(db, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def estado(db: str, user_id) -> dict:
    c = _conn(db)
    try:
        f = c.execute("SELECT email, conectado_en FROM usuarios_gmail WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        c.close()
    return {"configurado": configurado(), "conectado": bool(f),
            "email": f["email"] if f else None, "conectado_en": f["conectado_en"] if f else None}


def url_autorizacion(redirect_uri: str, state: str, email: str | None = None) -> str:
    params = {"client_id": _cliente()[0], "redirect_uri": redirect_uri, "response_type": "code",
              "scope": " ".join(SCOPES), "access_type": "offline", "state": state,
              # Sin prompt=consent Google no vuelve a dar refresh token si ya lo dio.
              "prompt": "consent", "include_granted_scopes": "true"}
    if email:
        params["login_hint"] = email
    return f"{AUTH_URL}?{urlencode(params)}"


def conectar(db: str, user_id, code: str, redirect_uri: str) -> str:
    """Canjea el código de Google y guarda la conexión. Devuelve el mail."""
    cid, sec = _cliente()
    r = requests.post(TOKEN_URL, data={"code": code, "client_id": cid, "client_secret": sec,
                                       "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
                      timeout=15)
    datos = r.json() if r.content else {}
    if r.status_code != 200 or not datos.get("refresh_token"):
        raise Desconectado(datos.get("error_description") or datos.get("error") or "Google no dio el permiso")
    info = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {datos['access_token']}"}, timeout=15).json()
    email = (info.get("email") or "").strip().lower()
    if not email:
        raise Desconectado("Google no dijo de qué cuenta es")
    c = _conn(db)
    try:
        c.execute("INSERT OR REPLACE INTO usuarios_gmail (user_id, email, refresh_token, conectado_en) "
                  "VALUES (?,?,?,?)", (user_id, email, credenciales.cifrar(datos["refresh_token"]),
                                       datetime.now().strftime("%Y-%m-%d %H:%M")))
        c.commit()
    finally:
        c.close()
    return email


def desconectar(db: str, user_id) -> None:
    c = _conn(db)
    try:
        c.execute("DELETE FROM usuarios_gmail WHERE user_id = ?", (user_id,))
        c.commit()
    finally:
        c.close()


def armar_mensaje(de_nombre: str, de_email: str, para: str, asunto: str, cuerpo: str) -> str:
    """El MIME en base64url, como lo pide la API de Gmail."""
    m = EmailMessage()
    usuario, _, dominio = de_email.partition("@")
    m["From"] = Address(display_name=de_nombre or "", username=usuario, domain=dominio)
    m["To"] = para
    m["Subject"] = asunto
    m.set_content(cuerpo)
    return base64.urlsafe_b64encode(m.as_bytes()).decode()


def _descifrar(guardado: str) -> str:
    try:
        return credenciales._fernet().decrypt(guardado.encode()).decode()
    except InvalidToken:
        raise Desconectado("la clave de cifrado cambió")


def _access_token(refresh_token: str) -> str:
    cid, sec = _cliente()
    r = requests.post(TOKEN_URL, data={"client_id": cid, "client_secret": sec, "refresh_token": refresh_token,
                                       "grant_type": "refresh_token"}, timeout=15)
    datos = r.json() if r.content else {}
    if r.status_code != 200 or not datos.get("access_token"):
        raise Desconectado(datos.get("error_description") or datos.get("error") or "Google rechazó el permiso")
    return datos["access_token"]


def enviar(db: str, user_id, de_nombre: str, para: str, asunto: str, cuerpo: str) -> dict:
    """Manda el mail desde la casilla del usuario. Devuelve {id, de}."""
    c = _conn(db)
    try:
        f = c.execute("SELECT email, refresh_token FROM usuarios_gmail WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        c.close()
    if not f:
        raise Desconectado("Conectá tu Gmail para mandar desde tu casilla")
    try:
        token = _access_token(_descifrar(f["refresh_token"]))
    except Desconectado:
        # Revocado o vencido: se borra para que la pantalla ofrezca reconectar.
        desconectar(db, user_id)
        raise Desconectado("Tu Gmail se desconectó: volvé a conectarlo")
    raw = armar_mensaje(de_nombre, f["email"], para, asunto, cuerpo)
    r = requests.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                      headers={"Authorization": f"Bearer {token}"}, json={"raw": raw}, timeout=20)
    if r.status_code != 200:
        err = (r.json().get("error") or {}).get("message") if r.content else None
        raise RuntimeError(f"Gmail no lo mandó: {err or r.status_code}")
    return {"id": r.json().get("id"), "de": f["email"]}
