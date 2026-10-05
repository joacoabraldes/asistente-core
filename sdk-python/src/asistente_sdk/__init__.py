"""SDK de asistente-core."""

from .client import Asistente
from .errors import (
    AsistenteError,
    InvalidApiKey,
    NetworkError,
    ServerError,
    StreamError,
)
from .models import (
    ConsumoMensual,
    DoneEvent,
    ErrorEvent,
    Event,
    OutOfScopeEvent,
    RawEvent,
    TokenEvent,
    ToolCallEvent,
    Usage,
)

__all__ = [
    "Asistente",
    "ConsumoMensual",
    "AsistenteError",
    "InvalidApiKey",
    "NetworkError",
    "ServerError",
    "StreamError",
    "DoneEvent",
    "ErrorEvent",
    "Event",
    "OutOfScopeEvent",
    "RawEvent",
    "TokenEvent",
    "ToolCallEvent",
    "Usage",
]
__version__ = "0.1.0"
