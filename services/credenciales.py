"""Cifrado de las contraseñas guardadas en el panel Credenciales (Juan, 22/9).

Nunca se guarda una clave en texto plano en la base: `cifrar()` antes de
INSERT/UPDATE, `descifrar()` al leer para el panel. La clave de cifrado
(`CREDENCIALES_KEY`) vive en un secret de Fly, no en el código ni en la base;
sin ella, nada guardado se puede leer — ni siquiera con acceso directo al
archivo de la base.
"""

import os

from cryptography.fernet import Fernet, InvalidToken


class SinClave(RuntimeError):
    """Falta CREDENCIALES_KEY: no se puede cifrar ni descifrar nada."""


def _fernet() -> Fernet:
    clave = os.environ.get("CREDENCIALES_KEY", "").strip()
    if not clave:
        raise SinClave(
            "Falta CREDENCIALES_KEY. Correr 'Activar contraseñas.bat' del Escritorio.")
    try:
        return Fernet(clave.encode())
    except (ValueError, TypeError) as e:
        raise SinClave(f"CREDENCIALES_KEY inválida: {e}") from e


def cifrar(texto: str) -> str:
    return _fernet().encrypt((texto or "").encode()).decode()


def descifrar(token: str) -> str:
    """El texto plano, o un aviso si no se pudo (clave rotada, dato corrupto).

    Nunca revienta la pantalla: mejor un aviso en la fila que tirar abajo
    todo el panel por una sola clave rara.
    """
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return "(no se pudo descifrar: la clave de cifrado cambió)"


def activo() -> bool:
    return bool(os.environ.get("CREDENCIALES_KEY", "").strip())
