import pytest
import httpcore

from trio_engineio.eio_types import (
    enforce_bytes,
    enforce_url,
    enforce_headers,
    NoCachingURL,
    get_engineio_url,
)


#
# `enforce_bytes` tests
#
@pytest.mark.parametrize("value, expected", (("123", b"123"), (b"abc", b"abc")))
def test_enforce_bytes(value, expected):
    assert enforce_bytes(value, name="test") == expected


def test_enforce_bytes_unicode():
    with pytest.raises(
        TypeError, match="test strings may not include unicode characters."
    ):
        enforce_bytes("abc" + chr(1001), name="test")


def test_enforce_bytes_invalid():
    with pytest.raises(TypeError, match="test must be bytes or str, but got int."):
        enforce_bytes(123, name="test")


#
# `enforce_url` tests
#
@pytest.mark.parametrize(
    "value",
    (
        b"http://foo:1234/?test=123",
        "http://foo:1234/?test=123",
        httpcore.URL("http://foo:1234/?test=123"),
    ),
)
def test_enforce_url(value):
    assert enforce_url(value, name="test") == httpcore.URL("http://foo:1234/?test=123")


def test_enforce_url_invalid():
    with pytest.raises(
        TypeError, match="test must be a URL, bytes, or str, but got int."
    ):
        enforce_url(123, name="test")


#
# `enforce_headers` tests
#
@pytest.mark.parametrize(
    "value",
    (
        {"foo": "bar", "toto": "titi"},
        (("foo", "bar"), ("toto", "titi")),
    ),
)
def test_enforce_headers(value):
    assert enforce_headers(value, name="test") == [(b"foo", b"bar"), (b"toto", b"titi")]


def test_enforce_headers_none():
    assert enforce_headers(None, name="test") == []


def test_enforce_headers_invalid():
    with pytest.raises(
        TypeError,
        match="test must be a mapping or sequence of two-tuples, but got int.",
    ):
        enforce_headers(123, name="test")


#
# `NoCachingURL` tests
#
def test_no_caching_url(mock_time):
    url = NoCachingURL(
        scheme="http", host="foo", port=1234, target="/socket.io/?test=123"
    )

    assert url._target == b"/socket.io/?test=123"
    assert url.target == b"/socket.io/?test=123&t=123.456"


def test_set_target():
    url = NoCachingURL(
        scheme="http", host="foo", port=1234, target="/socket.io/?test=123"
    )

    url.target = "bar"

    assert url._target == b"bar"


def test_add_to_target():
    url = NoCachingURL(
        scheme="http", host="foo", port=1234, target="/socket.io/?test=123"
    )
    url.add_to_target("&sid=123")

    assert url._target == b"/socket.io/?test=123&sid=123"


#
# `get_engineio_url` tests
#
@pytest.mark.parametrize("scheme", ("http", "https", "ws", "wss"))
@pytest.mark.parametrize(
    "target, expected_target",
    (
        ("/", "/toto/?transport={0}&EIO=3&t=123.456"),
        ("/socket.io", "/socket.io/?transport={0}&EIO=3&t=123.456"),
        (
            "/socket.io/?test=123",
            "/socket.io/?test=123&transport={0}&EIO=3&t=123.456",
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
