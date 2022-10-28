import pytest
import httpcore

from trio_engineio.eio_types import get_engineio_url

#
# `get_engineio_url` tests
#
@pytest.mark.parametrize("scheme", ("http", "https", "ws", "wss"))
@pytest.mark.parametrize(
    "target, expected_target",
    (
        ("/", "/toto?transport={0}&EIO=3&t=123.456"),
        ("/socket.io", "/socket.io?transport={0}&EIO=3&t=123.456"),
        (
            "/socket.io?test=123",
            "/socket.io?test=123&transport={0}&EIO=3&t=123.456",
        ),
    ),
)
@pytest.mark.parametrize("transport", ("polling", "websocket"))
def test_get_engineio_url(scheme, target, expected_target, transport, mock_time):
    if transport == "polling":
        expected_scheme = b"https" if scheme in ("https", "wss") else b"http"
    else:
        expected_scheme = b"wss" if scheme in ("https", "wss") else b"ws"

    url = httpcore.URL(scheme=scheme, host="foo", port=1234, target=target)
    engineio_url = get_engineio_url(url, b"/toto/", transport)

    assert engineio_url.scheme == expected_scheme
    assert engineio_url.host == b"foo"
    assert engineio_url.port == 1234
    assert engineio_url.target == expected_target.format(transport).encode("ascii")
