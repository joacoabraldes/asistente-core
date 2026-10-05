"""El mismo request_id no se cobra dos veces."""

from __future__ import annotations

from sqlalchemy import text

from asistente_sdk import Asistente


def test_mismo_request_id_deja_una_sola_fila_de_consumo(
    http, organizacion, precio, llm_falso, sync_engine
):
    llm_falso()
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)

    for _ in range(2):
        list(
            cliente.ask(
                conversation_id="c1",
                external_user_id="u1",
                content="Hola",
                request_id="pedido-fijo",
            )
        )

    with sync_engine.begin() as conn:
        total = conn.execute(text('SELECT count(*) FROM "usage"')).scalar()
    assert total == 1
