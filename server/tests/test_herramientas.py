"""El flujo de herramientas: el modelo pide, el cliente ejecuta, el modelo responde.

Lo que se verifica es el contrato, que es lo que la terminal va a depender:

* la primera vuelta termina en `tool_call` y **sin `done`**, porque la respuesta
  todavia no existe;
* el pedido queda guardado, asi la segunda vuelta puede reconstruir la
  conversacion tal cual la vio el modelo;
* las dos vueltas se cobran, porque son dos llamadas al proveedor;
* un cliente no puede devolver el resultado de una herramienta que el modelo no
  pidio.
"""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import text

from asistente_sdk import Asistente, DoneEvent, ToolCallEvent, TokenEvent
from asistente_server.llm import TokenUsage, ToolCallChunk

USO = TokenUsage(input_tokens=100, cached_input_tokens=0, output_tokens=10)

CURVA = {
    "type": "function",
    "function": {
        "name": "curva_del_dia",
        "description": "Trae la curva de tasas del dia",
        "parameters": {
            "type": "object",
            "properties": {"fecha": {"type": "string"}},
            "required": ["fecha"],
        },
    },
}


def test_la_primera_vuelta_termina_en_tool_call_y_sin_done(
    http, organizacion, precio, llm_guion, sync_engine
):
    llamadas = llm_guion(
        ((), USO, [ToolCallChunk(id="call_1", name="curva_del_dia",
                                 arguments={"fecha": "2026-10-07"},
                                 arguments_raw='{"fecha": "2026-10-07"}')]),
    )
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)

    eventos = list(
        cliente.ask_events(
            conversation_id="c1", external_user_id="u1", content="Dame la curva de hoy"
        )
    )

    pedidos = [e for e in eventos if isinstance(e, ToolCallEvent)]
    assert len(pedidos) == 1
    assert pedidos[0].name == "curva_del_dia"
    assert pedidos[0].arguments == {"fecha": "2026-10-07"}
    # No hay `done`: la respuesta todavia no existe.
    assert not [e for e in eventos if isinstance(e, DoneEvent)]
    assert len(llamadas) == 1

    with sync_engine.begin() as conn:
        fila = conn.execute(
            text("SELECT role, content, tool_calls FROM messages WHERE role='assistant'")
        ).one()
    assert fila.role == "assistant"
    assert fila.content == ""
    assert fila.tool_calls == [
        {"id": "call_1", "name": "curva_del_dia", "arguments": {"fecha": "2026-10-07"}}
    ]


def test_el_pedido_se_cobra_aunque_no_haya_respuesta(
    http, organizacion, precio, llm_guion, sync_engine
):
    """El proveedor cobro la llamada: tiene que quedar registrada."""
    llm_guion(
        ((), USO, [ToolCallChunk(id="call_1", name="curva_del_dia", arguments={})]),
    )
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)
    list(cliente.ask_events(conversation_id="c1", external_user_id="u1", content="curva"))

    with sync_engine.begin() as conn:
        filas = conn.execute(
            text('SELECT stage, input_tokens, cost_usd FROM "usage"')
        ).all()
    assert len(filas) == 1
    assert filas[0].stage == "answer"
    assert filas[0].input_tokens == 100


def test_la_segunda_vuelta_trae_la_respuesta_y_se_cobra_aparte(
    http, organizacion, precio, llm_guion, sync_engine
):
    llamadas = llm_guion(
        ((), USO, [ToolCallChunk(id="call_1", name="curva_del_dia",
                                 arguments={"fecha": "2026-10-07"})]),
        (("La curva ", "esta plana."), USO, None),
    )
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)

    list(cliente.ask_events(conversation_id="c1", external_user_id="u1", content="curva"))
    eventos = list(
        cliente.tool_results(
            conversation_id="c1",
            external_user_id="u1",
            results={"call_1": '{"tasa_30d": 0.031}'},
        )
    )

    texto = "".join(e.text for e in eventos if isinstance(e, TokenEvent))
    assert texto == "La curva esta plana."
    done = [e for e in eventos if isinstance(e, DoneEvent)]
    assert len(done) == 1

    # Dos llamadas al proveedor, dos filas de consumo con request_id distinto.
    assert len(llamadas) == 2
    with sync_engine.begin() as conn:
        filas = conn.execute(
            text('SELECT request_id, stage FROM "usage" ORDER BY created_at')
        ).all()
    assert len(filas) == 2
    assert filas[0].request_id != filas[1].request_id


def test_la_segunda_vuelta_le_devuelve_al_modelo_su_propio_pedido(
    http, organizacion, precio, llm_guion
):
    """Sin el turno del asistente con su pedido, el modelo no entiende el resultado."""
    llamadas = llm_guion(
        ((), USO, [ToolCallChunk(id="call_1", name="curva_del_dia",
                                 arguments={"fecha": "2026-10-07"})]),
        (("ok",), USO, None),
    )
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)
    list(cliente.ask_events(conversation_id="c1", external_user_id="u1", content="curva"))
    list(
        cliente.tool_results(
            conversation_id="c1", external_user_id="u1",
            results={"call_1": '{"tasa_30d": 0.031}'},
        )
    )

    mensajes = llamadas[1]["messages"]
    asistente = [m for m in mensajes if m.get("tool_calls")]
    assert len(asistente) == 1
    tc = asistente[0]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "curva_del_dia"
    # Los argumentos vuelven como string JSON, que es lo que espera el proveedor.
    assert json.loads(tc["function"]["arguments"]) == {"fecha": "2026-10-07"}

    resultado = [m for m in mensajes if m.get("role") == "tool"]
    assert len(resultado) == 1
    assert resultado[0]["tool_call_id"] == "call_1"
    assert resultado[0]["content"] == '{"tasa_30d": 0.031}'


def test_no_se_puede_devolver_una_herramienta_que_no_se_pidio(
    http, organizacion, precio, llm_guion
):
    """Si no se valida, el cliente inyecta texto arbitrario al prompt."""
    from asistente_sdk.errors import ServerError

    llm_guion(
        ((), USO, [ToolCallChunk(id="call_1", name="curva_del_dia", arguments={})]),
        (("ok",), USO, None),
    )
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)
    list(cliente.ask_events(conversation_id="c1", external_user_id="u1", content="curva"))

    try:
        list(
            cliente.tool_results(
                conversation_id="c1", external_user_id="u1",
                results={"call_inventado": "cualquier cosa"},
            )
        )
    except ServerError as exc:
        assert "400" in str(exc)
    else:
        raise AssertionError("tendria que haber rechazado el tool_call_id")


def test_sin_pedido_pendiente_el_endpoint_rechaza(http, organizacion, precio, llm_guion):
    from asistente_sdk.errors import ServerError

    llm_guion((("respuesta normal",), USO, None))
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)
    list(cliente.ask(conversation_id="c1", external_user_id="u1", content="hola"))

    try:
        list(
            cliente.tool_results(
                conversation_id="c1", external_user_id="u1",
                results={"call_1": "x"},
            )
        )
    except ServerError as exc:
        assert "409" in str(exc)
    else:
        raise AssertionError("tendria que haber rechazado: no hay pedido pendiente")


def test_un_json_de_argumentos_roto_no_se_inventa(
    http, organizacion, precio, llm_guion
):
    """Argumentos vacios y el crudo en el log, no un diccionario inventado."""
    llm_guion(
        ((), USO, [ToolCallChunk(id="call_1", name="curva_del_dia",
                                 arguments={}, arguments_raw="{roto")]),
    )
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)
    eventos = list(
        cliente.ask_events(conversation_id="c1", external_user_id="u1", content="curva")
    )
    pedido = [e for e in eventos if isinstance(e, ToolCallEvent)][0]
    assert pedido.arguments == {}
