from __future__ import annotations

import json
import logging
import ssl
from typing import AsyncIterator

import pytest
import trio
import httpcore

from trio_engineio import trio_client, EngineIoConnectionError
from trio_engineio import packet, payload
from trio_engineio import eio_types

# https://github.com/miguelgrinberg/python-socketio/issues/332


# from https://www.inspiredpython.com/article/five-advanced-pytest-fixture-patterns
@pytest.fixture
def mock_connect_polling(monkeypatch):
    called_with = {}

    async def _connect_polling(*args):
        called_with["url"] = bytes(args[2])
        called_with["headers"] = args[3]
        called_with["engineio_path"] = args[4]
        return True

    monkeypatch.setattr(
        "trio_engineio.trio_client.EngineIoClient._connect_polling", _connect_polling
    )

    return called_with


@pytest.fixture
def mock_connect_websocket(monkeypatch):
    state = {"success": True}
    called_with = {}

    async def _connect_websocket(*args):
        called_with["url"] = bytes(args[2])
        called_with["headers"] = args[3]
        called_with["engineio_path"] = args[4]
        return state["success"]

    monkeypatch.setattr(
        "trio_engineio.trio_client.EngineIoClient._connect_websocket",
        _connect_websocket,
    )

    return state, called_with


@pytest.fixture
def mock_disconnect(monkeypatch):
    called = []

    async def _disconnect(_):
        called.append(True)
        return

    monkeypatch.setattr(
        "trio_engineio.trio_client.EngineIoClient.disconnect", _disconnect
    )

    return called


@pytest.fixture
def mock_reset(monkeypatch):
    called = []

    async def _reset(_):
        called.append(True)
        return

    monkeypatch.setattr("trio_engineio.trio_client.EngineIoClient._reset", _reset)

    return called


@pytest.fixture
def mock_trigger_event(monkeypatch):
    called_with = {}

    async def _trigger_event(*args, **kwargs):
        called_with["event"] = args[1]
        called_with["args"] = args[2:]
        called_with["run_async"] = kwargs["run_async"]
        return True

    monkeypatch.setattr(
        "trio_engineio.trio_client.EngineIoClient._trigger_event", _trigger_event
    )

    return called_with


@pytest.fixture
def mock_send_packet(monkeypatch):
    saved_packets = []

    async def _send_packet(_, pkt: packet.Packet) -> None:
        saved_packets.append(pkt)
        return

    monkeypatch.setattr(
        "trio_engineio.trio_client.EngineIoClient._send_packet", _send_packet
    )

    return saved_packets


@pytest.fixture
def mock_receive_packet(monkeypatch):
    received_packets = []

    async def _receive_packet(_, pkt: packet.Packet) -> None:
        received_packets.append(pkt)
        return

    monkeypatch.setattr(
        "trio_engineio.trio_client.EngineIoClient._receive_packet", _receive_packet
    )

    return received_packets


@pytest.fixture
def mock_send_request(monkeypatch):
    state = {
        "status": 200,
        "returned_packet": packet.Packet(
            packet.OPEN,
            data={
                "sid": "123",
                "upgrades": [],
                "pingInterval": 1000,
                "pingTimeout": 2000,
            },
        ),
        "more_packets": False,
    }
    saved_request = {}

    async def _send_request(
        _, method, url, headers=None, body=None, timeouts=None
    ) -> MockResponse | None:
        saved_request["method"] = method
        saved_request["url"] = url
        saved_request["headers"] = headers
        saved_request["body"] = body
        saved_request["timeouts"] = timeouts
        status = state["status"]
        returned_packet = state["returned_packet"]
        more_packets = state["more_packets"]
        response = None
        if method == "GET":
            if status is None:
                response = None
            elif status < 200 or status >= 300:
                response = MockResponse(status)
            elif returned_packet == "INVALID":
                response = MockResponse(status, content=b"foo")
            else:
                if more_packets:
                    pkt2 = packet.Packet(packet.NOOP)
                    p = payload.Payload([returned_packet, pkt2])
                else:
                    p = payload.Payload([returned_packet])
                state["returned_packet"] = {}
                state["status"] = None
                state["more_packets"] = False
                content = p.encode()
                response = MockResponse(status, headers=headers, content=content)
        elif method == "POST":
            response = None if status is None else MockResponse(status)
        return response

    monkeypatch.setattr(
        "trio_engineio.trio_client.EngineIoClient._send_request", _send_request
    )

    return state, saved_request


@pytest.fixture
def mock_connect_trio_websocket(monkeypatch):
    state = {"success": True, "return_value": None}
    called_with = {}

    async def connect_websocket(
        _, host, port, resource, extra_headers=None, use_ssl=False, **kwargs
    ) -> MockWebsocketConnection | None:
        called_with["host"] = host
        called_with["port"] = port
        called_with["resource"] = resource
        called_with["extra_headers"] = extra_headers
        called_with["use_ssl"] = use_ssl
        if state["success"]:
            ws = state["return_value"] or MockWebsocketConnection()
            state["return_value"] = ws
            return ws
        raise trio_client.trio_ws.HandshakeError()

    monkeypatch.setattr(
        "trio_engineio.trio_client.trio_ws.connect_websocket", connect_websocket
    )

    return state, called_with


@pytest.fixture
def mock_trio_open_memory_channel(monkeypatch):
    called_with = {}

    def _open_memory_channel(buffer_size):
        called_with["buffer_size"] = buffer_size
        return "send_channel", "receive_channel"

    monkeypatch.setattr(
        "trio_engineio.trio_client.trio.open_memory_channel", _open_memory_channel
    )

    return called_with


def mock_trio_nursery_start(nursery):
    started_tasks = []

    async def _start(fn, *args, **kwargs):
        started_tasks.append(fn)
        return

    nursery.start = _start

    return started_tasks


class MockCancelScope:
    def __init__(self) -> None:
        self.cancel_called = False

    def cancel(self) -> None:
        self.cancel_called = True
        return


class MockResponse:
    def __init__(
        self,
        status: int,
        *,
        headers: list[tuple[bytes, bytes]] | None = None,
        content: bytes | None = None,
        extensions: dict | None = None,
    ) -> None:
        """
        Parameters:
            status: The HTTP status code of the response. For example `200`.
            headers: The HTTP response headers.
            content: The content of the response body.
            extensions: A dictionary of optional extra information included on
                the responseself.Possible keys include `"http_version"`,
                `"reason_phrase"`, and `"network_stream"`.
        """
        self.status: int = status
        self.headers: list[tuple[bytes, bytes]] = [] if headers is None else headers
        self.content: bytes = b"" if content is None else content
        self.extensions: dict = {} if extensions is None else extensions

    async def aread(self):
        return self.content


class MockHttpConnectionPool:
    def __init__(self, *args, **kwargs):
        self.connections = None  # property used for debug in Trio_client._reset().
        self.has_active_connections = False
        self.closed = False

    async def request(
        self,
        method: bytes | str,
        url: httpcore.URL | bytes | str,
        *,
        headers: dict | list | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
        extensions: dict | None = None,
    ) -> MockResponse:
        if self.closed:
            raise httpcore.NetworkError
        if method not in ("GET", b"GET", "POST", b"POST"):
            raise httpcore.ProtocolError
        return MockResponse(200)

    async def aclose(self):
        if self.has_active_connections:
            # Closing a connection pool with active connections raise a RuntimeError.
            raise RuntimeError
        else:
            self.closed = True


class MockWebsocketConnection:
    def __init__(self):
        self.closed = False
        self.sent = []
        self.received = []
        self.close_after_pong = False
        self.closing_delay = 0

    async def send_message(self, msg: str | bytes) -> None:
        if self.closed:
            closed_reason = trio_client.trio_ws.CloseReason(
                4001, "TEST_SEND_MESSAGE_CONNECTION_CLOSED"
            )
            raise trio_client.trio_ws.ConnectionClosed(closed_reason)
        self.sent.append(msg)

    async def get_message(self) -> str | bytes:
        try:
            msg = self.received.pop(0)
        except IndexError:
            closed_reason = trio_client.trio_ws.CloseReason(
                4002, "TEST_RECEIVE_MESSAGE_NO_MORE_MSG"
            )
            raise trio_client.trio_ws.ConnectionClosed(closed_reason)
        else:
            if msg == "ERROR":
                raise ValueError
            if msg == "TIMEOUT":
                await trio.sleep(10)
            try:
                pkt = packet.Packet(encoded_packet=msg)
                if (
                    pkt.packet_type == packet.PONG
                    and pkt.data == "probe"
                    and self.close_after_pong
                ):
                    self.closed = True
            except Exception as e:
                pass
            return msg

    async def aclose(self, code=1000, reason=None) -> None:
        if self.closing_delay > 0:
            await trio.sleep(self.closing_delay)
        self.closed = True


#
# `__init__` tests
#
def test_create():
    c = trio_client.EngineIoClient()

    assert c._handlers == {}
    for attr in [
        "_base_url",
        "_current_transport",
        "_sid",
        "_upgrades",
        "_ping_interval",
        "_ping_timeout",
        "_http",
        "_ws",
        "_send_channel",
        "_receive_channel",
        "_ping_task_scope",
        "_write_task_scope",
        "_read_task_scope",
    ]:
        assert getattr(c, attr) is None, attr + " is not None"
    assert c._pong_received
    assert c.state == "disconnected"
    assert c._transports == ["polling", "websocket"]


def test_custon_json():
    assert packet.Packet.json == json

    trio_client.EngineIoClient(json="foo")

    assert packet.Packet.json == "foo"
    packet.Packet.json = json


def test_logger():
    c = trio_client.EngineIoClient(logger=False)
    assert c._logger.getEffectiveLevel() == logging.ERROR
    c._logger.setLevel(logging.NOTSET)

    c = trio_client.EngineIoClient(logger=True)
    assert c._logger.getEffectiveLevel() == logging.INFO
    c._logger.setLevel(logging.WARNING)

    c = trio_client.EngineIoClient(logger=True)
    assert c._logger.getEffectiveLevel() == logging.WARNING
    c._logger.setLevel(logging.NOTSET)

    my_logger = logging.Logger("foo")
    c = trio_client.EngineIoClient(logger=my_logger)
    assert c._logger == my_logger


@pytest.mark.parametrize("timeout", (None, 27))
def test_custon_timeout(timeout):
    if timeout is None:
        c = trio_client.EngineIoClient()
        timeout = 5
    else:
        c = trio_client.EngineIoClient(request_timeout=timeout)

    assert c._timeouts == {
        "connect": timeout,
        "read": timeout,
        "write": timeout,
        "pool": timeout,
    }


@pytest.mark.parametrize("verify", (None, False))
def test_custon_ssl_verify(verify):
    if verify is None:
        c = trio_client.EngineIoClient()
        verify = True
    else:
        c = trio_client.EngineIoClient(ssl_verify=False)

    assert c._ssl_verify == verify


@pytest.mark.parametrize("pool", (None, MockHttpConnectionPool()))
def test_custon_http_connection_pool(pool):
    if pool is None:
        c = trio_client.EngineIoClient()
    else:
        c = trio_client.EngineIoClient(http_session=pool)

    assert c._http == pool


#
# `on` tests
#
def test_on_event():
    c = trio_client.EngineIoClient()

    @c.on("connect")
    def foo():
        pass

    c.on("disconnect", foo)

    assert c._handlers["connect"] == foo
    assert c._handlers["disconnect"] == foo


def test_on_event_invalid():
    c = trio_client.EngineIoClient()

    with pytest.raises(ValueError):
        c.on("invalid")


#
# `connect` tests
#
async def test_already_connected(nursery):
    c = trio_client.EngineIoClient()
    c.state = "connected"

    with pytest.raises(trio_client.EngineIoConnectionError):
        await c.connect(nursery, "http://foo")


async def test_bad_connection_args(nursery):
    c = trio_client.EngineIoClient()

    with pytest.raises(trio_client.EngineIoConnectionError):
        await c.connect(nursery, 3)


async def test_invalid_transports(nursery):
    c = trio_client.EngineIoClient()

    with pytest.raises(trio_client.EngineIoConnectionError):
        await c.connect(nursery, "http://foo", transports=["foo", "bar"])


async def test_some_invalid_transports(nursery, mock_connect_websocket):
    c = trio_client.EngineIoClient()

    await c.connect(nursery, "http://foo", transports=["foo", "websocket", "bar"])
    assert c._transports == ["websocket"]


@pytest.mark.parametrize("transports", (None, ["polling"], ["polling", "websocket"]))
async def test_connect_polling(
    transports, nursery, mock_connect_polling, mock_trio_open_memory_channel
):
    c = trio_client.EngineIoClient()

    if transports is None:
        connected = await c.connect(nursery, "http://foo:3000")
    else:
        connected = await c.connect(nursery, "http://foo:3000", transports=transports)

    assert connected
    assert c._send_channel == "send_channel"
    assert c._receive_channel == "receive_channel"
    assert mock_trio_open_memory_channel["buffer_size"] == 10
    assert mock_connect_polling["url"] == b"http://foo:3000/"
    assert mock_connect_polling["headers"] == []
    assert mock_connect_polling["engineio_path"] == b"/engine.io"


@pytest.mark.parametrize("transports", (["websocket"], "websocket"))
async def test_connect_websocket(transports, nursery, mock_connect_websocket):
    c = trio_client.EngineIoClient()

    _state, called_with = mock_connect_websocket

    connected = await c.connect(nursery, "ws://foo", transports=transports)

    assert connected
    assert called_with["url"] == b"ws://foo/"
    assert called_with["headers"] == []
    assert called_with["engineio_path"] == b"/engine.io"


async def test_connect_query_string(nursery, mock_connect_polling):
    c = trio_client.EngineIoClient()

    assert await c.connect(nursery, "http://foo?bar=baz")
    assert mock_connect_polling["url"] == b"http://foo/?bar=baz"
    assert mock_connect_polling["headers"] == []
    assert mock_connect_polling["engineio_path"] == b"/engine.io"


async def test_connect_custom_headers(nursery, mock_connect_polling):
    c = trio_client.EngineIoClient()

    assert await c.connect(nursery, "http://foo", headers={"Foo": "Bar"})
    assert mock_connect_polling["url"] == b"http://foo/"
    assert mock_connect_polling["headers"] == [(b"Foo", b"Bar")]
    assert mock_connect_polling["engineio_path"] == b"/engine.io"


async def test_connect_custom_path(nursery, mock_connect_polling):
    c = trio_client.EngineIoClient()

    assert await c.connect(nursery, "http://foo", engineio_path="/socket.io")
    assert mock_connect_polling["url"] == b"http://foo/"
    assert mock_connect_polling["headers"] == []
    assert mock_connect_polling["engineio_path"] == b"/socket.io"


#
# `send` tests
#
@pytest.mark.parametrize("data", ("foo", b"foo"))
@pytest.mark.parametrize("binary", (None, False, True))
async def test_send(data, binary, mock_send_packet):
    c = trio_client.EngineIoClient()
    c.state = "connected"

    if binary is None:
        binary = isinstance(data, bytes)
        await c.send(data)
    else:
        await c.send(data, binary=binary)

    assert mock_send_packet[0].packet_type == packet.MESSAGE
    assert mock_send_packet[0].data == data
    assert mock_send_packet[0].binary == binary


async def test_send_not_connected(mock_send_packet):
    c = trio_client.EngineIoClient()
    c.state = "foo"

    await c.send("bar")

    assert len(mock_send_packet) == 0


#
# `disconnect` tests
#
async def test_disconnect_not_connected():
    c = trio_client.EngineIoClient()
    c.state = "foo"
    c._sid = "bar"
    c._current_transport = "baz"

    await c.disconnect()

    assert c.state == "disconnected"
    assert c._sid is None
    assert c._current_transport is None


async def test_disconnect(mock_send_packet, mock_trigger_event):
    c = trio_client.EngineIoClient()
    trio_client.connected_clients.append(c)
    c.state = "connected"
    c._current_transport = "polling"
    c._ping_task_scope = MockCancelScope()

    await c.disconnect()

    assert mock_send_packet[0].packet_type == packet.CLOSE
    assert c._ping_task_scope.cancel_called
    assert mock_trigger_event["event"] == "disconnect"
    assert not mock_trigger_event["run_async"]
    assert c.state == "disconnected"
    assert c not in trio_client.connected_clients


#
# `transport` tests
#
@pytest.mark.parametrize("transport", (None, "a_transport"))
async def test_transport(transport):
    c = trio_client.EngineIoClient()
    c._current_transport = transport

    assert c.transport() == transport


#
# `sleep` tests
#
async def test_sleep(autojump_clock):
    c = trio_client.EngineIoClient()
    start_time = trio.current_time()

    await c.sleep(5)

    end_time = trio.current_time()
    assert end_time - start_time == 5


#
# `_reset` tests
#
@pytest.mark.parametrize(
    "has_active_connections, should_close", ((False, True), (True, False))
)
async def test_reset_polling(has_active_connections, should_close):
    c = trio_client.EngineIoClient()
    c.state = "connected"
    c._sid = "123"
    c._current_transport = "polling"
    c._http = MockHttpConnectionPool()
    c._http.has_active_connections = has_active_connections

    await c._reset()

    assert c._http.closed == should_close
    assert c.state == "disconnected"
    assert c._sid is None
    assert c._current_transport is None


@pytest.mark.parametrize(
    "was_closed, timeout, should_close",
    ((True, False, True), (False, False, True), (False, True, False)),
)
async def test_reset_websocket(was_closed, timeout, should_close, autojump_clock):
    c = trio_client.EngineIoClient(request_timeout=10)
    c.state = "connected"
    c._sid = "123"
    c._current_transport = "websocket"
    c._ws = MockWebsocketConnection()
    c._ws.closed = was_closed
    c._ws.closing_delay = 20 if timeout else 0

    await c._reset()

    assert c._ws.closed == should_close
    assert c.state == "disconnected"
    assert c._sid is None
    assert c._current_transport is None


#
# `_connect_polling` tests
#
async def test_polling_connection_failed(
    nursery, mock_time, mock_send_request, mock_reset
):
    c = trio_client.EngineIoClient()

    state, saved_request = mock_send_request
    state["status"] = None

    with pytest.raises(
        EngineIoConnectionError, match="Connection refused by the server"
    ):
        await c._connect_polling(
            nursery, httpcore.URL("http://foo"), [(b"Foo", b"Bar")], b"/socket.io"
        )

    assert saved_request["method"] == "GET"
    assert (
        bytes(saved_request["url"])
        == b"http://foo/socket.io/?transport=polling&EIO=3&t=123.456"
    )
    assert saved_request["headers"] == [(b"Foo", b"Bar")]
    assert saved_request["timeouts"] == {
        "connect": 5.0,
        "read": 5.0,
        "write": 5.0,
        "pool": 5.0,
    }
    assert len(mock_reset) == 1


async def test_polling_connection_404(
    nursery, mock_time, mock_send_request, mock_reset
):
    c = trio_client.EngineIoClient()

    state, saved_request = mock_send_request
    state["status"] = 404

    with pytest.raises(EngineIoConnectionError) as excinfo:
        await c._connect_polling(nursery, httpcore.URL("http://foo"), [], b"/engine.io")

    assert str(excinfo.value) == "Unexpected status code 404 in server response"
    assert (
        bytes(saved_request["url"])
        == b"http://foo/engine.io/?transport=polling&EIO=3&t=123.456"
    )
    assert len(mock_reset) == 1


async def test_polling_connection_invalid_packet(
    nursery, mock_time, mock_send_request, mock_reset
):
    c = trio_client.EngineIoClient()

    state, saved_request = mock_send_request
    state["status"] = 200
    state["returned_packet"] = "INVALID"

    with pytest.raises(
        EngineIoConnectionError, match="Unexpected response from server"
    ):
        await c._connect_polling(nursery, httpcore.URL("http://foo"), [], b"/socket.io")

    assert (
        bytes(saved_request["url"])
        == b"http://foo/socket.io/?transport=polling&EIO=3&t=123.456"
    )
    assert len(mock_reset) == 1


async def test_polling_connection_no_open_packet(
    nursery, mock_time, mock_send_request, mock_reset
):
    c = trio_client.EngineIoClient()

    state, saved_request = mock_send_request
    state["status"] = 200
    state["returned_packet"] = packet.Packet(packet.CLOSE)

    with pytest.raises(
        EngineIoConnectionError, match="OPEN packet not returned by server"
    ):
        await c._connect_polling(nursery, httpcore.URL("http://foo"), [], b"/socket.io")

    assert (
        bytes(saved_request["url"])
        == b"http://foo/socket.io/?transport=polling&EIO=3&t=123.456"
    )
    assert len(mock_reset) == 1


@pytest.mark.parametrize("scheme", ("http", "https"))
async def test_polling_connection_successful(
    scheme,
    nursery,
    mock_time,
    mock_send_request,
    mock_trigger_event,
    mock_receive_packet,
    mock_connect_websocket,
):
    c = trio_client.EngineIoClient()

    state, saved_request = mock_send_request
    state["status"] = 200
    state["returned_packet"] = packet.Packet(
        packet.OPEN,
        data={
            "sid": "123",
            "upgrades": [],
            "pingInterval": 1000,
            "pingTimeout": 2000,
        },
    )
    started_tasks = mock_trio_nursery_start(nursery)

    connected = await c._connect_polling(
        nursery, httpcore.URL(f"{scheme}://foo/?test=1"), [], b"/engine.io"
    )

    assert connected
    assert c._sid == "123"
    assert c._upgrades == []
    assert c._ping_interval == 1
    assert c._ping_timeout == 2
    assert c._current_transport == "polling"
    assert bytes(
        c._base_url
    ) == b"%b://foo/engine.io/?test=1&transport=polling&EIO=3&sid=123&t=123.456" % scheme.encode(
        "ascii"
    )
    assert c.state == "connected"
    assert c in trio_client.connected_clients
    assert mock_trigger_event["event"] == "connect"
    assert mock_trigger_event["args"] == ()
    assert not mock_trigger_event["run_async"]
    assert len(mock_receive_packet) == 0
    assert len(mock_connect_websocket[1]) == 0  # _connect_websocket is not called
    assert c._ping_loop in started_tasks
    assert c._write_loop in started_tasks
    assert c._read_loop_polling in started_tasks
    assert c._read_loop_websocket not in started_tasks


async def test_polling_connection_with_more_packets(
    nursery, mock_time, mock_send_request, mock_receive_packet
):
    c = trio_client.EngineIoClient()

    state, saved_request = mock_send_request
    state["status"] = 200
    state["returned_packet"] = packet.Packet(
        packet.OPEN,
        data={
            "sid": "123",
            "upgrades": [],
            "pingInterval": 1000,
            "pingTimeout": 2000,
        },
    )
    state["more_packets"] = True
    mock_trio_nursery_start(nursery)

    await c._connect_polling(nursery, httpcore.URL("http://foo"), [], b"/engine.io")

    assert mock_receive_packet[0].packet_type == packet.NOOP


async def test_polling_connection_upgraded(
    nursery,
    mock_time,
    mock_send_request,
    mock_receive_packet,
    mock_trigger_event,
    mock_connect_websocket,
):
    c = trio_client.EngineIoClient()

    state, saved_request = mock_send_request
    state["status"] = 200
    state["returned_packet"] = packet.Packet(
        packet.OPEN,
        data={
            "sid": "123",
            "upgrades": ["websocket"],
            "pingInterval": 1000,
            "pingTimeout": 2000,
        },
    )
    started_tasks = mock_trio_nursery_start(nursery)

    connected = await c._connect_polling(
        nursery, httpcore.URL("http://foo"), [], b"/engine.io"
    )

    assert connected
    assert c._sid == "123"
    assert c._upgrades == ["websocket"]
    assert c._ping_interval == 1
    assert c._ping_timeout == 2
    assert c._current_transport == "polling"
    assert (
        bytes(c._base_url)
        == b"http://foo/engine.io/?transport=polling&EIO=3&sid=123&t=123.456"
    )
    assert c.state == "connected"
    assert c in trio_client.connected_clients
    assert mock_trigger_event["event"] == "connect"
    assert mock_trigger_event["args"] == ()
    assert not mock_trigger_event["run_async"]
    assert len(mock_receive_packet) == 0
    assert len(mock_connect_websocket[1]) == 3  # _connect_websocket called once
    assert c._ping_loop not in started_tasks
    assert c._write_loop not in started_tasks
    assert c._read_loop_polling not in started_tasks
    assert c._read_loop_websocket not in started_tasks


async def test_polling_connection_not_upgraded(
    nursery,
    mock_time,
    mock_send_request,
    mock_receive_packet,
    mock_trigger_event,
    mock_connect_websocket,
):
    c = trio_client.EngineIoClient()

    state, saved_request = mock_send_request
    state["status"] = 200
    state["returned_packet"] = packet.Packet(
        packet.OPEN,
        data={
            "sid": "123",
            "upgrades": ["websocket"],
            "pingInterval": 1000,
            "pingTimeout": 2000,
        },
    )
    started_tasks = mock_trio_nursery_start(nursery)
    mock_connect_websocket[0]["success"] = False

    connected = await c._connect_polling(
        nursery, httpcore.URL("http://foo"), [], b"/engine.io"
    )

    assert connected
    assert c._sid == "123"
    assert c._upgrades == ["websocket"]
    assert c._ping_interval == 1
    assert c._ping_timeout == 2
    assert c._current_transport == "polling"
    assert (
        bytes(c._base_url)
        == b"http://foo/engine.io/?transport=polling&EIO=3&sid=123&t=123.456"
    )
    assert c.state == "connected"
    assert c in trio_client.connected_clients
    assert mock_trigger_event["event"] == "connect"
    assert mock_trigger_event["args"] == ()
    assert not mock_trigger_event["run_async"]
    assert len(mock_receive_packet) == 0
    assert c._ping_loop in started_tasks
    assert c._write_loop in started_tasks
    assert c._read_loop_polling in started_tasks
    assert c._read_loop_websocket not in started_tasks


async def test_polling_connection_no_upgrade(
    nursery,
    mock_time,
    mock_send_request,
    mock_receive_packet,
    mock_trigger_event,
    mock_connect_websocket,
):
    c = trio_client.EngineIoClient()
    c._transports = ["polling"]

    state, saved_request = mock_send_request
    state["status"] = 200
    state["returned_packet"] = packet.Packet(
        packet.OPEN,
        data={
            "sid": "123",
            "upgrades": ["websocket"],
            "pingInterval": 1000,
            "pingTimeout": 2000,
        },
    )
    started_tasks = mock_trio_nursery_start(nursery)

    connected = await c._connect_polling(
        nursery, httpcore.URL("http://foo"), [], b"/engine.io"
    )

    assert connected
    assert c._sid == "123"
    assert c._upgrades == ["websocket"]
    assert c._ping_interval == 1
    assert c._ping_timeout == 2
    assert c._current_transport == "polling"
    assert (
        bytes(c._base_url)
        == b"http://foo/engine.io/?transport=polling&EIO=3&sid=123&t=123.456"
    )
    assert c.state == "connected"
    assert c in trio_client.connected_clients
    assert mock_trigger_event["event"] == "connect"
    assert mock_trigger_event["args"] == ()
    assert not mock_trigger_event["run_async"]
    assert len(mock_receive_packet) == 0
    assert len(mock_connect_websocket[1]) == 0  # _connect_websocket is not called
    assert c._ping_loop in started_tasks
    assert c._write_loop in started_tasks
    assert c._read_loop_polling in started_tasks
    assert c._read_loop_websocket not in started_tasks


#
# `_connect_websocket` tests
#
async def test_websocket_connection_failed(
    nursery, mock_time, mock_connect_trio_websocket
):
    c = trio_client.EngineIoClient()

    state, called_with = mock_connect_trio_websocket
    state["success"] = False

    with pytest.raises(EngineIoConnectionError, match="Websocket connection error"):
        await c._connect_websocket(
            nursery, httpcore.URL("http://foo"), [(b"Foo", b"Bar")], b"/socket.io"
        )
    assert called_with["host"] == "foo"
    assert called_with["port"] is None
    assert called_with["resource"] == "/socket.io/?transport=websocket&EIO=3&t=123.456"
    assert called_with["extra_headers"] == [(b"Foo", b"Bar")]
    assert not called_with["use_ssl"]


async def test_websocket_upgrade_failed(
    nursery, mock_time, mock_connect_trio_websocket
):
    c = trio_client.EngineIoClient()
    c._sid = "123"

    state, called_with = mock_connect_trio_websocket
    state["success"] = False

    connected = await c._connect_websocket(
        nursery, httpcore.URL("http://foo:1234"), [], b"/engine.io"
    )

    assert not connected
    assert called_with["host"] == "foo"
    assert called_with["port"] == 1234
    assert (
        called_with["resource"]
        == "/engine.io/?transport=websocket&EIO=3&sid=123&t=123.456"
    )
    assert called_with["extra_headers"] == []
    assert not called_with["use_ssl"]


@pytest.mark.parametrize("closed", (True, False))
async def test_websocket_connection_no_open_packet(
    closed, nursery, mock_connect_trio_websocket, mock_reset
):
    c = trio_client.EngineIoClient()

    state, called_with = mock_connect_trio_websocket
    state["success"] = True
    ws = MockWebsocketConnection()
    if closed:
        ws.closed = closed
        match = "Unexpected recv exception"
        reset_calls = 0
    else:
        ws.received = [packet.Packet(packet.CLOSE).encode()]
        match = "no OPEN packet"
        reset_calls = 1
    state["return_value"] = ws

    with pytest.raises(EngineIoConnectionError, match=match):
        await c._connect_websocket(
            nursery, httpcore.URL("http://foo"), [], b"/engine.io"
        )
    assert len(mock_reset) == reset_calls  # _reset called once if connection is open


@pytest.mark.parametrize(
    "scheme, ssl_verify", (("ws", True), ("wss", True), ("wss", False))
)
async def test_websocket_connection_successful(
    scheme,
    ssl_verify,
    nursery,
    mock_time,
    mock_connect_trio_websocket,
    mock_trigger_event,
):
    c = trio_client.EngineIoClient(ssl_verify=ssl_verify)

    state, called_with = mock_connect_trio_websocket
    state["success"] = True
    ws = MockWebsocketConnection()
    ws.received = [
        packet.Packet(
            packet.OPEN,
            data={
                "sid": "123",
                "upgrades": [],
                "pingInterval": 1000,
                "pingTimeout": 2000,
            },
        ).encode()
    ]
    state["return_value"] = ws
    started_tasks = mock_trio_nursery_start(nursery)

    connected = await c._connect_websocket(
        nursery, httpcore.URL(f"{scheme}://foo"), [], b"/engine.io"
    )

    assert connected
    assert c._sid == "123"
    assert c._upgrades == []
    assert c._ping_interval == 1
    assert c._ping_timeout == 2
    assert c._current_transport == "websocket"
    assert bytes(
        c._base_url
    ) == b"%b://foo/engine.io/?transport=websocket&EIO=3&t=123.456" % scheme.encode(
        "ascii"
    )
    assert c.state == "connected"
    assert c in trio_client.connected_clients
    assert mock_trigger_event["event"] == "connect"
    assert mock_trigger_event["args"] == ()
    assert not mock_trigger_event["run_async"]
    assert c._ws == ws
    assert c._ping_loop in started_tasks
    assert c._write_loop in started_tasks
    assert c._read_loop_websocket in started_tasks
    assert c._read_loop_polling not in started_tasks
    if scheme == "wss":
        context = called_with["use_ssl"]
        assert isinstance(context, ssl.SSLContext)
        if not ssl_verify:
            assert context.verify_mode == ssl.CERT_NONE


@pytest.mark.parametrize("closed", (True, False))
async def test_websocket_upgrade_ping_handshake_failed(
    closed, nursery, mock_connect_trio_websocket, mock_trigger_event
):
    c = trio_client.EngineIoClient()
    c._sid = "123"
    c._current_transport = "polling"

    state, called_with = mock_connect_trio_websocket
    state["success"] = True
    ws = MockWebsocketConnection()
    ws.closed = closed
    state["return_value"] = ws
    started_tasks = mock_trio_nursery_start(nursery)

    connected = await c._connect_websocket(
        nursery, httpcore.URL("ws://foo"), [], b"/engine.io"
    )

    assert not connected
    if closed:
        assert len(ws.sent) == 0
    else:
        assert ws.sent[0] == packet.Packet(packet.PING, data="probe").encode(
            always_bytes=False
        )
    assert c._current_transport == "polling"
    assert mock_trigger_event == {}  # _trigger_vent is not called
    assert c._ping_loop not in started_tasks
    assert c._write_loop not in started_tasks
    assert c._read_loop_websocket not in started_tasks
    assert c._read_loop_polling not in started_tasks


@pytest.mark.parametrize("pong_status", (None, "BAD"))
async def test_websocket_upgrade_no_pong(
    pong_status, nursery, mock_connect_trio_websocket, mock_trigger_event
):
    c = trio_client.EngineIoClient()
    c._sid = "123"
    c._current_transport = "polling"

    state, called_with = mock_connect_trio_websocket
    state["success"] = True
    ws = MockWebsocketConnection()
    if pong_status is not None:
        ws.received = [packet.Packet(packet.PONG, data="eborp").encode()]
    state["return_value"] = ws
    started_tasks = mock_trio_nursery_start(nursery)

    connected = await c._connect_websocket(
        nursery, httpcore.URL("ws://foo"), [], b"/engine.io"
    )

    assert not connected
    assert ws.sent[0] == packet.Packet(packet.PING, data="probe").encode(
        always_bytes=False
    )
    assert c._current_transport == "polling"
    assert mock_trigger_event == {}  # _trigger_vent is not called
    assert c._ping_loop not in started_tasks
    assert c._write_loop not in started_tasks
    assert c._read_loop_websocket not in started_tasks
    assert c._read_loop_polling not in started_tasks


async def test_websocket_upgrade_upgrade_sending_failed(
    nursery, mock_connect_trio_websocket, mock_trigger_event
):
    c = trio_client.EngineIoClient()
    c._sid = "123"
    c._current_transport = "polling"

    state, called_with = mock_connect_trio_websocket
    state["success"] = True
    ws = MockWebsocketConnection()
    ws.received = [packet.Packet(packet.PONG, data="probe").encode()]
    ws.close_after_pong = True
    state["return_value"] = ws
    started_tasks = mock_trio_nursery_start(nursery)

    connected = await c._connect_websocket(
        nursery, httpcore.URL("ws://foo"), [], b"/engine.io"
    )

    assert not connected
    assert ws.sent[0] == packet.Packet(packet.PING, data="probe").encode(
        always_bytes=False
    )
    assert c._current_transport == "polling"
    assert mock_trigger_event == {}  # _trigger_vent is not called
    assert c._ping_loop not in started_tasks
    assert c._write_loop not in started_tasks
    assert c._read_loop_websocket not in started_tasks
    assert c._read_loop_polling not in started_tasks


async def test_websocket_upgrade_successful(
    nursery, mock_time, mock_connect_trio_websocket, mock_trigger_event
):
    c = trio_client.EngineIoClient()
    c._sid = "123"
    c._current_transport = "polling"
    c._base_url = eio_types.NoCachingURL(
        scheme=b"http",
        host=b"foo",
        port=None,
        target=b"/engine.io?transport=polling&EIO=3&sid=123",
    )

    state, called_with = mock_connect_trio_websocket
    state["success"] = True
    ws = MockWebsocketConnection()
    ws.received = [packet.Packet(packet.PONG, data="probe").encode()]
    state["return_value"] = ws
    started_tasks = mock_trio_nursery_start(nursery)

    connected = await c._connect_websocket(
        nursery, httpcore.URL("ws://foo"), [], b"/engine.io"
    )

    assert connected
    assert ws.sent[0] == packet.Packet(packet.PING, data="probe").encode(
        always_bytes=False
    )
    assert ws.sent[1] == packet.Packet(packet.UPGRADE).encode(always_bytes=False)
    assert c._sid == "123"  # not changed
    assert (
        bytes(c._base_url)
        == b"http://foo/engine.io?transport=polling&EIO=3&sid=123&t=123.456"
    )  # not changed except t=... set when accessing the _base_url.target property
    assert c not in trio_client.connected_clients  # was added by polling connection
    assert mock_trigger_event == {}  # _trigger_vent was called by polling connection
    assert c._current_transport == "websocket"
    assert c._ws == ws
    assert c._ping_loop in started_tasks
    assert c._write_loop in started_tasks
    assert c._read_loop_websocket in started_tasks
    assert c._read_loop_polling not in started_tasks


#
# `_receive_packet` tests
#
async def test_receive_unknown_packet():
    c = trio_client.EngineIoClient()

    await c._receive_packet(packet.Packet(encoded_packet=b"9"))
    # should be ignored


async def test_receive_noop_packet():
    c = trio_client.EngineIoClient()

    await c._receive_packet(packet.Packet(packet.NOOP))
    # should be ignored


async def test_receive_pong_packet():
    c = trio_client.EngineIoClient()
    c._pong_received = False

    await c._receive_packet(packet.Packet(packet.PONG))

    assert c._pong_received


async def test_receive_message_packet(mock_trigger_event):
    c = trio_client.EngineIoClient()

    await c._receive_packet(packet.Packet(packet.MESSAGE, {"foo": "bar"}))

    assert mock_trigger_event["event"] == "message"
    assert mock_trigger_event["args"] == ({"foo": "bar"},)
    assert mock_trigger_event["run_async"]


async def test_receive_close_packet(mock_disconnect):
    c = trio_client.EngineIoClient()

    await c._receive_packet(packet.Packet(packet.CLOSE))

    assert len(mock_disconnect) == 1  # disconnect is called once


#
# `_send_packet` tests
#
async def test_send_packet_disconnected():
    c = trio_client.EngineIoClient()
    c.state = "disconnected"
    c._send_channel, c._receive_channel = trio.open_memory_channel(10)

    await c._send_packet(packet.Packet(packet.NOOP))

    with pytest.raises((trio.EndOfChannel, trio.WouldBlock)):
        c._receive_channel.receive_nowait()


async def test_send_packet():
    c = trio_client.EngineIoClient()
    c.state = "connected"
    c._send_channel, c._receive_channel = trio.open_memory_channel(10)

    await c._send_packet(packet.Packet(packet.NOOP))

    pkt = c._receive_channel.receive_nowait()
    assert pkt.packet_type == packet.NOOP


@pytest.mark.parametrize("method", ("BBB", "GET"))
async def test_send_request_no_http(method):
    c = trio_client.EngineIoClient()

    trio_client.httpcore.AsyncConnectionPool = MockHttpConnectionPool

    r = await c._send_request(method, "http://foo")

    if method == "BBB":
        assert r is None
    else:
        assert r.status == 200


#
# `_send_request` tests
#
async def test_send_request_with_http():
    c = trio_client.EngineIoClient()
    c._http = MockHttpConnectionPool()

    r = await c._send_request("GET", "http://foo")

    assert r.status == 200


#
# `_trigger_event` tests
#
async def test_trigger_event_function():
    result = []

    def foo_handler(arg):
        result.append("ok")
        result.append(arg)

    c = trio_client.EngineIoClient()
    c.on("message", handler=foo_handler)

    await c._trigger_event("message", "bar")

    assert result == ["ok", "bar"]


async def test_trigger_event_coroutine():
    result = []

    async def foo_handler(arg):
        result.append("ok")
        result.append(arg)

    c = trio_client.EngineIoClient()
    c.on("message", handler=foo_handler)

    await c._trigger_event("message", "bar")

    assert result == ["ok", "bar"]


async def test_trigger_event_function_error():
    def foo_handler(_arg):
        return 1 / 0

    c = trio_client.EngineIoClient()
    c.on("message", handler=foo_handler)

    assert await c._trigger_event("message", "bar") is None


async def test_trigger_event_coroutine_error():
    async def foo_handler(arg):
        return 1 / 0

    c = trio_client.EngineIoClient()
    c.on("message", handler=foo_handler)

    assert await c._trigger_event("message", "bar") is None


async def test_trigger_event_function_async():
    result = []

    def foo_handler(arg):
        result.append("ok")
        result.append(arg)

    c = trio_client.EngineIoClient()
    c.on("message", handler=foo_handler)

    await c._trigger_event("message", "bar", run_async=True)

    assert result == ["ok", "bar"]


async def test_trigger_event_coroutine_async():
    result = []

    async def foo_handler(arg):
        result.append("ok")
        result.append(arg)

    c = trio_client.EngineIoClient()
    c.on("message", handler=foo_handler)

    await c._trigger_event("message", "bar", run_async=True)

    assert result == ["ok", "bar"]


async def test_trigger_event_function_async_error():
    result = []

    def foo_handler(arg):
        result.append(arg)
        return 1 / 0

    c = trio_client.EngineIoClient()
    c.on("message", handler=foo_handler)

    await c._trigger_event("message", "bar", run_async=True)

    assert result == ["bar"]


async def test_trigger_event_coroutine_async_error():
    result = []

    async def foo_handler(arg):
        result.append(arg)
        return 1 / 0

    c = trio_client.EngineIoClient()
    c.on("message", handler=foo_handler)

    await c._trigger_event("message", "bar", run_async=True)

    assert result == ["bar"]


async def test_trigger_unknown_event():
    c = trio_client.EngineIoClient()

    await c._trigger_event("message", 123, run_async=True)
    # should do nothing


#
# `_ping_loop` tests
#
async def test_ping_loop_disconnected(autojump_clock):
    c = trio_client.EngineIoClient()
    c.state = "disconnected"
    c._ping_interval = 2
    c._ping_timeout = 1
    c._write_task_scope = MockCancelScope()
    c._read_task_scope = MockCancelScope()

    start_time = trio.current_time()
    await c._ping_loop()
    end_time = trio.current_time()

    assert end_time - start_time == 0.1
    assert c._write_task_scope.cancel_called
    assert c._read_task_scope.cancel_called


async def test_ping_loop_disconnect(monkeypatch, mock_send_packet, autojump_clock):
    c = trio_client.EngineIoClient()
    c.state = "connected"
    c._ping_interval = 2
    c._ping_timeout = 1
    c._write_task_scope = MockCancelScope()
    c._read_task_scope = MockCancelScope()

    # Replace the trio.sleep delay by a side effect to force disconnection.
    async def fake_trio_sleep(delay):
        c.state = "disconnecting"

    monkeypatch.setattr("trio_engineio.trio_client.trio.sleep", fake_trio_sleep)

    await c._ping_loop()

    assert not c._pong_received
    assert mock_send_packet[0].packet_type == packet.PING
    assert c._write_task_scope.cancel_called
    assert c._read_task_scope.cancel_called


async def test_ping_loop_missing_pong_polling(monkeypatch, mock_send_packet):
    c = trio_client.EngineIoClient()
    c.state = "connected"
    c._ping_interval = 2
    c._ping_timeout = 1
    c._write_task_scope = MockCancelScope()
    c._read_task_scope = MockCancelScope()

    # Replace the trio.sleep delay by a side effect to simulate no PONG reception.
    async def fake_trio_sleep(_):
        c._pong_received = False

    monkeypatch.setattr("trio_engineio.trio_client.trio.sleep", fake_trio_sleep)

    await c._ping_loop()

    assert c.state == "connected"
    assert not c._pong_received
    assert mock_send_packet[0].packet_type == packet.PING
    assert c._write_task_scope.cancel_called
    assert c._read_task_scope.cancel_called


async def test_ping_loop_cancelled(monkeypatch, mock_send_packet):
    c = trio_client.EngineIoClient()
    c.state = "connected"
    c._ping_interval = 2
    c._ping_timeout = 1
    c._write_task_scope = MockCancelScope()
    c._read_task_scope = MockCancelScope()
    c._ping_task_scope = MockCancelScope()

    # Replace the trio.sleep delay by a side effect to simulate external cancellation.
    async def fake_trio_sleep(_):
        c._ping_task_scope.cancel()

    monkeypatch.setattr("trio_engineio.trio_client.trio.sleep", fake_trio_sleep)

    await c._ping_loop()

    assert c.state == "connected"
    assert not c._pong_received
    assert mock_send_packet[0].packet_type == packet.PING
    assert c._write_task_scope.cancel_called
    assert c._read_task_scope.cancel_called


async def test_ping_loop_missing_pong_websocket(monkeypatch, mock_send_packet):
    c = trio_client.EngineIoClient()
    c.state = "connected"
    c._ping_interval = 2
    c._ping_timeout = 1
    c._write_task_scope = MockCancelScope()
    c._read_task_scope = MockCancelScope()
    c._ws = MockWebsocketConnection()

    # Replace the trio.sleep delay by a side effect to simulate no PONG reception.
    async def fake_trio_sleep(_):
        c._pong_received = False

    monkeypatch.setattr("trio_engineio.trio_client.trio.sleep", fake_trio_sleep)

    await c._ping_loop()

    assert c.state == "connected"
    assert not c._pong_received
    assert mock_send_packet[0].packet_type == packet.PING
    assert c._write_task_scope.cancel_called
    assert c._read_task_scope.cancel_called
    assert c._ws.closed


#
# `_read_loop_polling` tests
#
async def test_read_loop_polling_disconnected(
    mock_trigger_event, mock_reset, autojump_clock
):
    c = trio_client.EngineIoClient()
    c.state = "disconnected"
    c._ping_interval = 2
    c._ping_timeout = 1
    c._write_task_scope = MockCancelScope()
    c._ping_task_scope = MockCancelScope()

    await c._read_loop_polling()

    assert c._write_task_scope.cancel_called
    assert c._ping_task_scope.cancel_called
    assert mock_trigger_event == {}  # _trigger_event is not called
    assert len(mock_reset) == 1  # _reset is called once


@pytest.mark.parametrize("status, pkt", ((None, None), (404, None), (200, "INVALID")))
async def test_read_loop_polling_error(
    status,
    pkt,
    mock_send_request,
    mock_trigger_event,
    mock_reset,
    autojump_clock,
):
    c = trio_client.EngineIoClient()
    c._ping_interval = 25
    c._ping_timeout = 5
    c.state = "connected"
    trio_client.connected_clients.append(c)
    c._base_url = b"http://foo"
    c._write_task_scope = MockCancelScope()
    c._ping_task_scope = MockCancelScope()

    state, saved_request = mock_send_request
    state["status"] = status
    state["returned_packet"] = pkt

    await c._read_loop_polling()

    assert saved_request["method"] == "GET"
    assert bytes(saved_request["url"]) == b"http://foo"
    assert saved_request["headers"] is None
    assert saved_request["timeouts"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }
    assert c._write_task_scope.cancel_called
    assert c._ping_task_scope.cancel_called
    assert mock_trigger_event["event"] == "disconnect"
    assert mock_trigger_event["args"] == ()
    assert not mock_trigger_event["run_async"]
    assert c not in trio_client.connected_clients
    assert len(mock_reset) == 1  # _reset is called once
    # state should be "disconnected" but the mock_reset fixture do not change it.
    assert c.state == "connected"


async def test_read_loop_polling(
    mock_send_request, mock_receive_packet, mock_reset, autojump_clock
):
    c = trio_client.EngineIoClient()
    c._ping_interval = 25
    c._ping_timeout = 5
    c.state = "connected"
    c._base_url = b"http://foo"
    c._write_task_scope = MockCancelScope()
    c._ping_task_scope = MockCancelScope()

    state, saved_request = mock_send_request
    state["status"] = 200
    state["returned_packet"] = packet.Packet(packet.PONG)
    state["more_packets"] = True

    await c._read_loop_polling()

    assert saved_request["method"] == "GET"
    assert bytes(saved_request["url"]) == b"http://foo"
    assert saved_request["headers"] is None
    assert saved_request["timeouts"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }
    assert mock_receive_packet[0].packet_type == packet.PONG
    assert mock_receive_packet[1].packet_type == packet.NOOP


#
# `_read_loop_websocket` tests
#
async def test_read_loop_websocket_disconnected(
    mock_trigger_event, mock_reset, autojump_clock
):
    c = trio_client.EngineIoClient()
    c.state = "disconnected"
    c._ping_interval = 2
    c._ping_timeout = 1
    c._write_task_scope = MockCancelScope()
    c._ping_task_scope = MockCancelScope()

    await c._read_loop_websocket()

    assert c._write_task_scope.cancel_called
    assert c._ping_task_scope.cancel_called
    assert mock_trigger_event == {}  # _trigger_event is not called
    assert len(mock_reset) == 1  # _reset is called once


async def test_read_loop_websocket_no_response(
    mock_trigger_event, mock_reset, autojump_clock
):
    c = trio_client.EngineIoClient()
    c._ping_interval = 25
    c._ping_timeout = 5
    c.state = "connected"
    trio_client.connected_clients.append(c)
    c._base_url = b"ws://foo"
    c._write_task_scope = MockCancelScope()
    c._ping_task_scope = MockCancelScope()
    c._ws = MockWebsocketConnection()

    await c._read_loop_websocket()

    assert c._write_task_scope.cancel_called
    assert c._ping_task_scope.cancel_called
    assert mock_trigger_event["event"] == "disconnect"
    assert mock_trigger_event["args"] == ()
    assert not mock_trigger_event["run_async"]
    assert c not in trio_client.connected_clients
    assert len(mock_reset) == 1  # _reset is called once
    # state should be "disconnected" but the mock_reset fixture do not change it.
    assert c.state == "connected"


@pytest.mark.parametrize("pkt", ("ERROR", b"bfoo", None, "TIMEOUT"))
async def test_read_loop_websocket_unexpected_error(
    pkt, mock_trigger_event, mock_reset, autojump_clock
):
    c = trio_client.EngineIoClient()
    c._ping_interval = 2
    c._ping_timeout = 1
    c.state = "connected"
    trio_client.connected_clients.append(c)
    c._base_url = b"ws://foo"
    c._write_task_scope = MockCancelScope()
    c._ping_task_scope = MockCancelScope()
    c._ws = MockWebsocketConnection()
    c._ws.received = [pkt]

    await c._read_loop_websocket()

    assert c._write_task_scope.cancel_called
    assert c._ping_task_scope.cancel_called
    assert mock_trigger_event["event"] == "disconnect"
    assert mock_trigger_event["args"] == ()
    assert not mock_trigger_event["run_async"]
    assert c not in trio_client.connected_clients
    assert len(mock_reset) == 1  # _reset is called once
    # state should be "disconnected" but the mock_reset fixture do not change it.
    assert c.state == "connected"


async def test_read_loop_websocket(mock_receive_packet, mock_reset, autojump_clock):
    c = trio_client.EngineIoClient()
    c._ping_interval = 25
    c._ping_timeout = 5
    c.state = "connected"
    trio_client.connected_clients.append(c)
    c._base_url = b"ws://foo"
    c._write_task_scope = MockCancelScope()
    c._ping_task_scope = MockCancelScope()
    c._ws = MockWebsocketConnection()
    c._ws.received = ["42[message]", packet.Packet(packet.CLOSE).encode()]

    await c._read_loop_websocket()

    assert mock_receive_packet[0].packet_type == packet.MESSAGE
    assert mock_receive_packet[0].data == "2[message]"
    assert mock_receive_packet[1].packet_type == packet.CLOSE
    assert c._write_task_scope.cancel_called
    assert c._ping_task_scope.cancel_called


#
# `_write_loop` tests
#
async def test_write_loop_disconnected():
    c = trio_client.EngineIoClient()
    c._ping_interval = 25
    c._ping_timeout = 5
    c._current_transport = "polling"
    c.state = "disconnected"

    await c._write_loop()
    # should not block


async def test_write_loop_no_packets():
    c = trio_client.EngineIoClient()
    c._ping_interval = 25
    c._ping_timeout = 5
    c._current_transport = "polling"
    c.state = "connected"

    c._send_channel, c._receive_channel = trio.open_memory_channel(1)
    await c._send_channel.send(None)

    await c._write_loop()
    # should not block


async def test_write_loop_empty_channel(autojump_clock):
    c = trio_client.EngineIoClient()
    c._ping_interval = 2
    c._ping_timeout = 1
    c._current_transport = "polling"
    c.state = "connected"

    c._send_channel, c._receive_channel = trio.open_memory_channel(10)

    await c._write_loop()
    # should not block


async def test_write_loop_closed_channel():
    c = trio_client.EngineIoClient()
    c._ping_interval = 2
    c._ping_timeout = 1
    c._current_transport = "polling"
    c.state = "connected"

    c._send_channel, c._receive_channel = trio.open_memory_channel(10)
    await c._send_channel.send(packet.Packet(packet.PONG))
    c._receive_channel.close()

    await c._write_loop()
    # should not block


async def test_write_loop_polling_one_packet(mock_send_request, autojump_clock):
    c = trio_client.EngineIoClient()
    c = trio_client.EngineIoClient()
    c._ping_interval = 2
    c._ping_timeout = 1
    c._current_transport = "polling"
    c.state = "connected"
    c._base_url = b"http://foo"

    state, saved_request = mock_send_request
    state["status"] = 200

    c._send_channel, c._receive_channel = trio.open_memory_channel(10)
    pkt = packet.Packet(packet.MESSAGE, {"foo": "bar"})
    await c._send_channel.send(pkt)

    await c._write_loop()

    assert saved_request["method"] == "POST"
    assert bytes(saved_request["url"]) == b"http://foo"
    assert saved_request["headers"] == {"Content-Type": "application/octet-stream"}
    p = payload.Payload([pkt])
    assert saved_request["body"] == p.encode()
    assert saved_request["timeouts"] == {
        "connect": 5.0,
        "read": 5.0,
        "write": 5.0,
        "pool": 5.0,
    }


async def test_write_loop_polling_three_packets(mock_send_request, autojump_clock):
    c = trio_client.EngineIoClient()
    c._ping_interval = 2
    c._ping_timeout = 1
    c._current_transport = "polling"
    c.state = "connected"
    c._base_url = b"http://foo"

    state, saved_request = mock_send_request
    state["status"] = 200

    c._send_channel, c._receive_channel = trio.open_memory_channel(10)
    pkts = [
        packet.Packet(packet.MESSAGE, {"foo": "bar"}),
        packet.Packet(packet.PING),
        packet.Packet(packet.NOOP),
    ]
    for pkt in pkts:
        await c._send_channel.send(pkt)

    await c._write_loop()

    assert saved_request["method"] == "POST"
    assert bytes(saved_request["url"]) == b"http://foo"
    assert saved_request["headers"] == {"Content-Type": "application/octet-stream"}
    p = payload.Payload(pkts)
    assert saved_request["body"] == p.encode()
    assert saved_request["timeouts"] == {
        "connect": 5.0,
        "read": 5.0,
        "write": 5.0,
        "pool": 5.0,
    }


@pytest.mark.parametrize("status", (None, 500))
async def test_write_loop_polling_connection_error(
    status, mock_send_request, autojump_clock
):
    c = trio_client.EngineIoClient()
    c._ping_interval = 2
    c._ping_timeout = 1
    c._current_transport = "polling"
    c.state = "connected"
    c._base_url = b"http://foo"

    state, saved_request = mock_send_request
    state["status"] = status

    c._send_channel, c._receive_channel = trio.open_memory_channel(10)
    pkt = packet.Packet(packet.MESSAGE, {"foo": "bar"})
    await c._send_channel.send(pkt)

    await c._write_loop()

    assert saved_request["method"] == "POST"
    assert bytes(saved_request["url"]) == b"http://foo"
    assert saved_request["headers"] == {"Content-Type": "application/octet-stream"}
    p = payload.Payload([pkt])
    assert saved_request["body"] == p.encode()
    assert saved_request["timeouts"] == {
        "connect": 5.0,
        "read": 5.0,
        "write": 5.0,
        "pool": 5.0,
    }
    assert c.state == "connected"


async def test_write_loop_websocket_one_packet(autojump_clock):
    c = trio_client.EngineIoClient()
    c._ping_interval = 2
    c._ping_timeout = 1
    c._current_transport = "websocket"
    c.state = "connected"
    c._ws = MockWebsocketConnection()

    c._send_channel, c._receive_channel = trio.open_memory_channel(10)
    pkt = packet.Packet(packet.MESSAGE, {"foo": "bar"})
    await c._send_channel.send(pkt)

    await c._write_loop()

    assert c._ws.sent[0] == '4{"foo":"bar"}'


async def test_write_loop_websocket_three_packets(autojump_clock):
    c = trio_client.EngineIoClient()
    c._ping_interval = 2
    c._ping_timeout = 1
    c._current_transport = "websocket"
    c.state = "connected"
    c._ws = MockWebsocketConnection()

    c._send_channel, c._receive_channel = trio.open_memory_channel(10)
    pkts = [
        packet.Packet(packet.MESSAGE, {"foo": "bar"}),
        packet.Packet(packet.PING),
        packet.Packet(packet.NOOP),
    ]
    for pkt in pkts:
        await c._send_channel.send(pkt)

    await c._write_loop()

    assert c._ws.sent[0] == '4{"foo":"bar"}'
    assert c._ws.sent[1] == "2"
    assert c._ws.sent[2] == "6"


async def test_write_loop_websocket_one_packet_binary(autojump_clock):
    c = trio_client.EngineIoClient()
    c._ping_interval = 2
    c._ping_timeout = 1
    c._current_transport = "websocket"
    c.state = "connected"
    c._send_channel, c._receive_channel = trio.open_memory_channel(10)
    c._ws = MockWebsocketConnection()

    pkt = packet.Packet(packet.MESSAGE, b"foo")
    await c._send_channel.send(pkt)

    await c._write_loop()

    assert c._ws.sent[0] == b"\x04foo"


async def test_write_loop_websocket_bad_connection(autojump_clock):
    c = trio_client.EngineIoClient()
    c._ping_interval = 2
    c._ping_timeout = 1
    c._current_transport = "websocket"
    c.state = "connected"
    c._ws = MockWebsocketConnection()
    c._ws.closed = True

    c._send_channel, c._receive_channel = trio.open_memory_channel(10)
    pkt = packet.Packet(packet.MESSAGE, {"foo": "bar"})
    await c._send_channel.send(pkt)

    await c._write_loop()

    assert c.state == "connected"
