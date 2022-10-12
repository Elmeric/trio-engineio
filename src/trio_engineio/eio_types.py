from __future__ import annotations

import time
import json
from typing import (
    Protocol,
    Any,
    Union,
    Type,
    Callable,
    Literal,
    Sequence,
    Mapping,
    Optional,
)

import httpcore

EventName = Literal["connect", "disconnect", "message"]
Transport = Literal["polling", "websocket"]
Headers = list[tuple[bytes, bytes]]
HeadersAsSequence = Sequence[tuple[Union[bytes, str], Union[bytes, str]]]
HeadersAsMapping = Mapping[Union[bytes, str], Union[bytes, str]]
TimeoutKey = Literal["connect", "read", "write", "pool"]
Timeouts = dict[TimeoutKey, Optional[float]]


def enforce_bytes(value: bytes | str, *, name: str) -> bytes:
    """
    Any arguments that are ultimately represented as bytes can be specified
    either as bytes or as strings.

    However, we enforce that any string arguments must only contain characters in
    the plain ASCII range. chr(0)...chr(127). If you need to use characters
    outside that range then be precise, and use a byte-wise argument.
    """
    if isinstance(value, str):
        try:
            return value.encode("ascii")
        except UnicodeEncodeError:
            raise TypeError(f"{name} strings may not include unicode characters.")
    elif isinstance(value, bytes):
        return value

    seen_type = type(value).__name__
    raise TypeError(f"{name} must be bytes or str, but got {seen_type}.")


def enforce_url(value: httpcore.URL | bytes | str, *, name: str) -> httpcore.URL:
    """
    Type check for URL parameters.
    """
    if isinstance(value, (bytes, str)):
        return httpcore.URL(value)
    elif isinstance(value, httpcore.URL):
        return value

    seen_type = type(value).__name__
    raise TypeError(f"{name} must be a URL, bytes, or str, but got {seen_type}.")


def enforce_headers(
    value: HeadersAsMapping | HeadersAsSequence | None = None, *, name: str
) -> list[tuple[bytes, bytes]]:
    """
    Convienence function that ensure all items in request or response headers
    are either bytes or strings in the plain ASCII range.
    """
    if value is None:
        return []
    elif isinstance(value, Mapping):
        return [
            (
                enforce_bytes(k, name="header name"),
                enforce_bytes(v, name="header value"),
            )
            for k, v in value.items()
        ]
    elif isinstance(value, Sequence):
        return [
            (
                enforce_bytes(k, name="header name"),
                enforce_bytes(v, name="header value"),
            )
            for k, v in value
        ]

    seen_type = type(value).__name__
    raise TypeError(
        f"{name} must be a mapping or sequence of two-tuples, but got {seen_type}."
    )


class NoCachingURL(httpcore.URL):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._target = self.target.split(b"&t=")[0]

    @property
    def target(self) -> bytes:
        return self._target + b"&t=" + enforce_bytes(str(time.time()), name="time")

    @target.setter
    def target(self, value: bytes) -> None:
        self._target = value

    def add_to_target(self, value: str | bytes):
        value = enforce_bytes(value, name="target")
        self._target += value


class JsonProtocol(Protocol):
    def dumps(
        self,
        obj: Any,
        *,
        skipkeys: bool = False,
        ensure_ascii: bool = True,
        check_circular: bool = True,
        allow_nan: bool = True,
        cls: Type[json.JSONEncoder] | None = None,
        indent: int | str | None = None,
        separators: tuple[str, str] | None = None,
        default: Callable[[Any], Any] | None = None,
        sort_keys: bool = False,
        **kw: Any,
    ) -> str:
        ...

    def loads(
        self,
        s: str | bytes,
        *,
        cls: Type[json.JSONDecoder] | None = None,
        object_hook: Callable[[dict], Any] | None = None,
        parse_float: Callable[[str], Any] | None = None,
        parse_int: Callable[[str], Any] | None = None,
        parse_constant: Callable[[str], Any] | None = None,
        object_pairs_hook: Callable[[list[tuple[Any, Any]]], Any] | None = None,
        **kw: Any,
    ) -> Any:
        ...
