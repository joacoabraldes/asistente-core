"""Formato de los eventos Server-Sent Events."""

from __future__ import annotations

import json
from typing import Any


def event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
