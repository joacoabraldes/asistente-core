"""Autenticacion por API key."""

from __future__ import annotations

import pytest

from asistente_sdk import Asistente, InvalidApiKey


def test_key_invalida_da_401(http):
    cliente = Asistente(api_key="ask_no-existe", http_client=http)
    with pytest.raises(InvalidApiKey):
        list(cliente.ask(conversation_id="c1", external_user_id="u1", content="Hola"))


def test_sin_header_da_401(http):
    respuesta = http.post(
        "/v1/conversations/c1/messages",
        json={"external_user_id": "u1", "content": "Hola"},
    )
    assert respuesta.status_code == 401


def test_health_no_pide_key(http):
    assert http.get("/health").json() == {"status": "ok"}
