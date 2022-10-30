# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import json
import time
from typing import (
    Any,
    Callable,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Type,
    Union,
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
        except UnicodeEncodeError as exc:
            raise TypeError(
                f"{name} strings may not include unicode characters."
            ) from exc
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
    if isinstance(value, httpcore.URL):
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
    if isinstance(value, Mapping):
        return [
            (
                enforce_bytes(k, name="header name"),
                enforce_bytes(v, name="header value"),
            )
            for k, v in value.items()
        ]
    if isinstance(value, Sequence):
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
        self._target = enforce_bytes(value, name="target")

    def add_to_target(self, value: str | bytes):
        self._target += enforce_bytes(value, name="target")


def get_engineio_url(
    url: httpcore.URL, engineio_path: bytes, transport: Transport
) -> NoCachingURL:
    """Generate the Engine.IO connection URL.

    Args:
        url: The URL to connect to.
        engineio_path: The endpoint to connect to.
        transport: Indicates whether the URL wil be used for a "polling"
            or a "websocket" connection.

    Returns:
        the built URL as a `NoCachingURL` object.
    """
    engineio_path = engineio_path.strip(b"/")

    # Adapt the URL scheme to the given transport, keeping its "secured" property.
    if transport == "polling":
        scheme = "http"
    else:  # transport == "websocket":
        scheme = "ws"
    if url.scheme in [b"https", b"wss"]:
        scheme += "s"

    # Check if a path and/or query is present in the URL (it is supposed that the
    # given path and query are used to override the standard Engine.io path and add
    # query parameters to the standard ones.
    # If an empty path ("/") is present, amend it with the given path, otherwise
    # keep the present one.
    # If a query is present, extend it with the "transport" and "EIO" keys,
    # otherwise,  build query required by the Engine.io protocol.
    target = url.target
    target_chunks = target.split(b"?", 1)
    if len(target_chunks) == 1:
        path, query = target_chunks[0], b""
    else:
        path, query = target_chunks[0], target_chunks[1]

    if path == b"/":
        path += engineio_path
    if path[-1:] == b"/":
        path = path[:-1]

    if query:
        query += b"&transport=" + transport.encode("ascii") + b"&EIO=3"
    else:
        query = b"transport=" + transport.encode("ascii") + b"&EIO=3"

    target = path + b"/?" + query

    return NoCachingURL(scheme=scheme, host=url.host, port=url.port, target=target)


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
        ...  # pragma: no cover

    def loads(
        self,
        text: str | bytes,
        *,
        cls: Type[json.JSONDecoder] | None = None,
        object_hook: Callable[[dict], Any] | None = None,
        parse_float: Callable[[str], Any] | None = None,
        parse_int: Callable[[str], Any] | None = None,
        parse_constant: Callable[[str], Any] | None = None,
        object_pairs_hook: Callable[[list[tuple[Any, Any]]], Any] | None = None,
        **kw: Any,
    ) -> Any:
        ...  # pragma: no cover
