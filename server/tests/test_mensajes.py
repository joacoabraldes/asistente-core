"""Punta a punta: SDK -> servidor -> proveedor mockeado -> fila de consumo."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from asistente_sdk import Asistente


def test_punta_a_punta_registra_una_fila_con_el_costo_correcto(
    http, organizacion, precio, llm_falso, sync_engine
):
    llm_falso()
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)

    texto = "".join(
        cliente.ask(conversation_id="c1", external_user_id="u1", content="Hola")
    )
    assert texto == "Hola mundo"

    with sync_engine.begin() as conn:
        filas = conn.execute(
            text(
                "SELECT stage, provider, model, input_tokens, cached_input_tokens, "
                "output_tokens, cost_usd, price_id, conversation_id, external_user_id "
                'FROM "usage"'
            )
        ).all()
        mensajes = conn.execute(
            text("SELECT role, content FROM messages ORDER BY created_at, role")
        ).all()

    assert len(filas) == 1
    fila = filas[0]
    assert fila.stage == "answer"
    assert (fila.provider, fila.model) == ("openai", "gpt-4o-mini")
    assert (fila.input_tokens, fila.cached_input_tokens, fila.output_tokens) == (1000, 200, 500)
    # 800 de entrada a 1 USD/Mtok + 200 cacheados a 0,50 + 500 de salida a 2
    assert fila.cost_usd == Decimal("0.001900")
    assert fila.price_id == precio
    assert (fila.conversation_id, fila.external_user_id) == ("c1", "u1")

    # Primero la pregunta, despues la respuesta: la pregunta se guarda antes de
    # llamar al proveedor.
    assert [(m.role, m.content) for m in mensajes] == [
        ("user", "Hola"),
        ("assistant", "Hola mundo"),
    ]


def test_el_evento_done_trae_el_uso_y_el_costo(http, organizacion, precio, llm_falso):
    from asistente_sdk import DoneEvent

    llm_falso()
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)

    eventos = list(
        cliente.ask_events(conversation_id="c1", external_user_id="u1", content="Hola")
    )
    done = [e for e in eventos if isinstance(e, DoneEvent)]
    assert len(done) == 1
    assert done[0].usage is not None
    assert done[0].usage.output_tokens == 500
    assert done[0].cost_usd == Decimal("0.001900")


def test_sin_uso_del_proveedor_la_fila_queda_sin_tokens_ni_costo(
    http, organizacion, precio, llm_falso, sync_engine
):
    """Un agujero visible es mejor que un numero inventado."""
    llm_falso(textos=("ok",), usage=None)
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)

    assert "".join(
        cliente.ask(conversation_id="c1", external_user_id="u1", content="Hola")
    ) == "ok"

    with sync_engine.begin() as conn:
        fila = conn.execute(
            text('SELECT input_tokens, output_tokens, cost_usd FROM "usage"')
        ).one()
    assert fila.input_tokens is None
    assert fila.output_tokens is None
    assert fila.cost_usd is None


def test_sin_precio_cargado_no_se_llama_al_proveedor(
    http, organizacion, llm_falso, sync_engine
):
    """Sin precio no se gasta un token y no queda una fila en cero."""
    from asistente_sdk import ErrorEvent

    llm_falso()
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)

    eventos = list(
        cliente.ask_events(conversation_id="c1", external_user_id="u1", content="Hola")
    )
    errores = [e for e in eventos if isinstance(e, ErrorEvent)]
    assert len(errores) == 1
    assert errores[0].code == "price_not_found"

    with sync_engine.begin() as conn:
        assert conn.execute(text('SELECT count(*) FROM "usage"')).scalar() == 0
