"""Errores del SDK."""

from __future__ import annotations


class AsistenteError(Exception):
    """Base de todos los errores del SDK."""


class InvalidApiKey(AsistenteError):
    """La API key no existe o fue revocada."""


class NetworkError(AsistenteError):
    """No se pudo llegar al servidor despues de los reintentos."""


class ServerError(AsistenteError):
    """El servidor respondio con un error."""


class StreamError(AsistenteError):
    """El servidor emitio un evento de error en medio de la respuesta."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
