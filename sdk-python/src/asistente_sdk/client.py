"""Cliente del servicio.

Los reintentos solo ocurren si todavia no llego nada de la respuesta: reintentar
en medio del stream duplicaria texto. Se reusa el mismo request_id, asi el
servidor no registra el consumo dos veces.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx

from .errors import InvalidApiKey, NetworkError, ServerError, StreamError
from .models import (
    ConsumoMensual,
    DoneEvent,
    ErrorEvent,
    Event,
    OutOfScopeEvent,
    RawEvent,
    TokenEvent,
    ToolCallEvent,
)

_EVENTOS = {
    "token": TokenEvent,
    "done": DoneEvent,
    "error": ErrorEvent,
    "out_of_scope": OutOfScopeEvent,
    "tool_call": ToolCallEvent,
}


def _parse_sse(lines: Iterator[str]) -> Iterator[Event]:
    nombre: str | None = None
    datos: list[str] = []
    for linea in lines:
        if linea == "":
            if nombre is not None:
                yield _build(nombre, "".join(datos))
            nombre, datos = None, []
            continue
        if linea.startswith("event:"):
            nombre = linea[len("event:") :].strip()
        elif linea.startswith("data:"):
            datos.append(linea[len("data:") :].strip())
    if nombre is not None:
        yield _build(nombre, "".join(datos))


def _build(nombre: str, payload: str) -> Event:
    data: dict[str, Any] = json.loads(payload) if payload else {}
    modelo = _EVENTOS.get(nombre)
    if modelo is None:
        return RawEvent(event=nombre, data=data)
    return modelo.model_validate(data)


class Asistente:
    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost:8000",
        timeout: float = 60.0,
        max_retries: int = 3,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._max_retries = max_retries
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Asistente:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def ask(
        self,
        *,
        conversation_id: str,
        external_user_id: str,
        content: str,
        request_id: str | None = None,
    ) -> Iterator[str]:
        """Va devolviendo el texto de la respuesta.

        Si el servidor emite un error, levanta StreamError.
        """
        for evento in self.ask_events(
            conversation_id=conversation_id,
            external_user_id=external_user_id,
            content=content,
            request_id=request_id,
        ):
            if isinstance(evento, TokenEvent):
                yield evento.text
            elif isinstance(evento, ErrorEvent):
                raise StreamError(evento.code, evento.message)

    def ask_events(
        self,
        *,
        conversation_id: str,
        external_user_id: str,
        content: str,
        request_id: str | None = None,
    ) -> Iterator[Event]:
        request_id = request_id or str(uuid.uuid4())
        espera = 0.5
        for intento in range(self._max_retries + 1):
            llego_algo = False
            try:
                for evento in self._stream(
                    conversation_id, external_user_id, content, request_id
                ):
                    llego_algo = True
                    yield evento
                return
            except httpx.TransportError as exc:
                if llego_algo or intento == self._max_retries:
                    raise NetworkError(str(exc)) from exc
                time.sleep(espera)
                espera *= 2

    def consumo(self, *, anio: int | None = None, mes: int | None = None) -> ConsumoMensual:
        """Consumo del mes de la organizacion de esta API key.

        Sin argumentos, el mes en curso. Cada organizacion ve solo lo suyo: el
        servidor lo resuelve con la API key, no con un id que se pueda pasar.
        """
        params: dict[str, int] = {}
        if anio is not None:
            params["anio"] = anio
        if mes is not None:
            params["mes"] = mes
        try:
            response = self._client.get(
                "/v1/usage/summary",
                params=params,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.TransportError as exc:
            raise NetworkError(str(exc)) from exc
        if response.status_code == 401:
            raise InvalidApiKey("La API key es invalida o fue revocada")
        if response.status_code >= 400:
            raise ServerError(
                f"El servidor respondio {response.status_code}: {response.text}"
            )
        return ConsumoMensual.model_validate(response.json())

    def _stream(
        self, conversation_id: str, external_user_id: str, content: str, request_id: str
    ) -> Iterator[Event]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "X-Request-Id": request_id,
            "Accept": "text/event-stream",
        }
        payload = {"external_user_id": external_user_id, "content": content}
        with self._client.stream(
            "POST",
            f"/v1/conversations/{conversation_id}/messages",
            json=payload,
            headers=headers,
        ) as response:
            if response.status_code == 401:
                response.read()
                raise InvalidApiKey("La API key es invalida o fue revocada")
            if response.status_code >= 400:
                response.read()
                raise ServerError(
                    f"El servidor respondio {response.status_code}: {response.text}"
                )
            yield from _parse_sse(response.iter_lines())
