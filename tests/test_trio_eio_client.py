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
from trio_engineio import trio_util

# https://github.com/miguelgrinberg/python-socketio/issues/332


# from https://www.inspiredpython.com/article/five-advanced-pytest-fixture-patterns
@pytest.fixture
def mock_connect_polling(monkeypatch):
    params = {}

    async def _connect_polling(*args):
        params["url"] = bytes(args[2])
        params["headers"] = args[3]
        params["engineio_path"] = args[4]
        return True

    monkeypatch.setattr("trio_engineio.trio_client.EngineIoClient._connect_polling", _connect_polling)

    return params


@pytest.fixture
def mock_connect_websocket(monkeypatch):
    params = {}

    async def _connect_websocket(*args):
        params["url"] = bytes(args[2])
        params["headers"] = args[3]
        params["engineio_path"] = args[4]
        return True

    monkeypatch.setattr("trio_engineio.trio_client.EngineIoClient._connect_websocket", _connect_websocket)

    return params


@pytest.fixture
def mock_trigger_event(monkeypatch):
    params = {}

    async def _trigger_event(*args, **kwargs):
        params["event"] = args[1]
        params["args"] = args[2:]
        params["run_async"] = kwargs["run_async"]
        return True

    monkeypatch.setattr("trio_engineio.trio_client.EngineIoClient._trigger_event", _trigger_event)

    return params


@pytest.fixture
def mock_send_packet(monkeypatch):
    saved_packets = []

    async def _send_packet(_, pkt: packet.Packet) -> None:
        saved_packets.append(pkt)
        return

    monkeypatch.setattr("trio_engineio.trio_client.EngineIoClient._send_packet", _send_packet)

    return saved_packets


@pytest.fixture
def mock_send_request(monkeypatch):
    state = {
        "mode": "connect",
        "status": 200,
        "returned_packet": packet.Packet(
            packet.OPEN,
            data={"sid": "123", "upgrades": [], "pingInterval": 1000, "pingTimeout": 2000}
        ),
        "more_packets": False,
        "success": True,
    }
    saved_request = {}

    async def _send_request(_, method, url, headers=None, body=None, timeouts=None) -> MockResponse | None:
        saved_request["method"] = method
        saved_request["url"] = url
        saved_request["headers"] = headers
        saved_request["body"] = body
        saved_request["timeouts"] = timeouts
        mode = state["mode"]
        status = state["status"]
        returned_packet = state["returned_packet"]
        more_packets = state["more_packets"]
        response = None
        if method == "GET":
            if mode in ("connect", "poll"):
                if status is None:
                    response = None
                elif status == 404:
                    response = MockResponse(status)
                elif status == 200 and returned_packet == "INVALID":
                    response = MockResponse(status, content=b"foo")
                elif status == 200:
                    if more_packets:
                        pkt2 = packet.Packet(packet.NOOP)
                        p = payload.Payload([returned_packet, pkt2])
                    else:
                        p = payload.Payload([returned_packet])
                    content = p.encode()
                    response = MockResponse(status, headers=headers, content=content)
        elif method == "POST":
            if status is None:
                response = None
            else:
                response = MockResponse(status)
            # elif status == 200:
            #     response = MockResponse(status, content=b"ok")
        return response

    monkeypatch.setattr("trio_engineio.trio_client.EngineIoClient._send_request", _send_request)

    return state, saved_request


@pytest.fixture
def mock_connect_trio_websocket(monkeypatch):
    state = {"success": True, "return_value": None}
    called_with = {}

    async def connect_websocket(_, host, port, resource, extra_headers=None, use_ssl=False, **kwargs) -> MockWebsocketConnection | None:
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

    monkeypatch.setattr("trio_engineio.trio_client.trio_ws.connect_websocket", connect_websocket)

    return state, called_with


@pytest.fixture
def mock_time(monkeypatch):
    def _time():
        return "123.456"

    monkeypatch.setattr("trio_engineio.eio_types.time.time", _time)


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
        self.connections = None
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
        if self.connections is not None:
            raise RuntimeError
        else:
            self.closed = True


class MockWebsocketConnection:
    def __init__(self):
        self.closed = False
        self.sent = []
        self.received = []
        self.close_after_pong = False

    async def send_message(self, msg: str | bytes) -> None:
        if self.closed:
            closed_reason = trio_client.trio_ws.CloseReason(4001, "TEST_SEND_MESSAGE_CONNECTION_CLOSED")
            raise trio_client.trio_ws.ConnectionClosed(closed_reason)
        self.sent.append(msg)

    async def get_message(self) -> str | bytes:
        try:
            msg = self.received.pop(0)
        except IndexError:
            closed_reason = trio_client.trio_ws.CloseReason(4002, "TEST_RECEIVE_MESSAGE_NO_MORE_MSG")
            raise trio_client.trio_ws.ConnectionClosed(closed_reason)
        else:
            if msg == "ERROR":
                raise ValueError
            if msg == "TIMEOUT":
                await trio.sleep(10)
            try:
                pkt = packet.Packet(encoded_packet=msg)
                if pkt.packet_type == packet.PONG and pkt.data == "probe" and self.close_after_pong:
                    self.closed = True
            except Exception as e:
                pass
            return msg

    async def aclose(self, code=1000, reason=None) -> None:
        self.closed = True


class TestTrioClient:
    def test_create(self):
        c = trio_client.EngineIoClient()
        assert c._handlers == {}
        for attr in [
            '_base_url',
            '_transports',
            '_current_transport',
            '_sid',
            '_upgrades',
            '_ping_interval',
            '_ping_timeout',
            '_http',
            '_ws',
            '_send_channel',
            '_receive_channel',
            '_ping_task_scope',
            '_write_task_scope',
            '_read_task_scope',
        ]:
            assert getattr(c, attr) is None, attr + ' is not None'
        assert c._pong_received
        assert c.state == 'disconnected'

    def test_custon_json(self):
        assert packet.Packet.json == json

        trio_client.EngineIoClient(json='foo')  # noqa
        assert packet.Packet.json == 'foo'
        packet.Packet.json = json

    def test_logger(self):
        c = trio_client.EngineIoClient(logger=False)
        assert c._logger.getEffectiveLevel() == logging.ERROR
        c._logger.setLevel(logging.NOTSET)

        c = trio_client.EngineIoClient(logger=True)
        assert c._logger.getEffectiveLevel() == logging.INFO
        c._logger.setLevel(logging.WARNING)

        c = trio_client.EngineIoClient(logger=True)
        assert c._logger.getEffectiveLevel() == logging.WARNING
        c._logger.setLevel(logging.NOTSET)

        my_logger = logging.Logger('foo')
        c = trio_client.EngineIoClient(logger=my_logger)
        assert c._logger == my_logger

    def test_custon_timeout(self):
        c = trio_client.EngineIoClient()
        assert c._timeouts == {"connect": 5, "read": 5, "write": 5, "pool": 5,}

        c = trio_client.EngineIoClient(request_timeout=27)
        assert c._timeouts == {"connect": 27, "read": 27, "write": 27, "pool": 27,}

    def test_custon_ssl_verify(self):
        c = trio_client.EngineIoClient()
        assert c._ssl_verify

        c = trio_client.EngineIoClient(ssl_verify=False)
        assert not c._ssl_verify

    def test_on_event(self):
        c = trio_client.EngineIoClient()

        @c.on('connect')
        def foo():
            pass

        c.on('disconnect', foo)

        assert c._handlers['connect'] == foo
        assert c._handlers['disconnect'] == foo

    def test_on_event_invalid(self):
        c = trio_client.EngineIoClient()
        with pytest.raises(ValueError):
            c.on('invalid')     # noqa

    async def test_already_connected(self, nursery):
        c = trio_client.EngineIoClient()
        c.state = "connected"

        with pytest.raises(trio_client.EngineIoConnectionError):
            await c.connect(nursery, 'http://foo')

    async def test_bad_connection_args(self, nursery):
        c = trio_client.EngineIoClient()

        with pytest.raises(trio_client.EngineIoConnectionError):
            await c.connect(nursery, 3)

    async def test_invalid_transports(self, nursery):
        c = trio_client.EngineIoClient()

        with pytest.raises(trio_client.EngineIoConnectionError):
            await c.connect(nursery, 'http://foo', transports=['foo', 'bar'])   # noqa

    async def test_some_invalid_transports(self, nursery, mocker):
        c = trio_client.EngineIoClient()

        async def _connect_websocket(*args, **kwargs):
            return True

        mocker.patch.object(c, "_connect_websocket", _connect_websocket)

        await c.connect(nursery, 'http://foo', transports=['foo', 'websocket', 'bar'])  # noqa
        assert c._transports == ['websocket']

    # async def test_some_invalid_transports(self, nursery, monkeypatch):
    #     c = trio_client.EngineIoClient()
    #
    #     async def _connect_websocket(*args, **kwargs):
    #         return True
    #
    #     monkeypatch.setattr(c, "_connect_websocket", _connect_websocket)
    #
    #     await c.connect(nursery, 'http://foo', transports=['foo', 'websocket', 'bar'])  # noqa
    #     assert c._transports == ['websocket']

    async def test_connect_polling(self, nursery, mock_connect_polling):
        c = trio_client.EngineIoClient()

        assert await c.connect(nursery, 'http://foo')
        assert mock_connect_polling["url"] == b'http://foo/'
        assert mock_connect_polling["headers"] == []
        assert mock_connect_polling["engineio_path"] == b"/engine.io"

        c = trio_client.EngineIoClient()

        assert await c.connect(nursery, 'http://foo', transports=['polling'])
        assert mock_connect_polling["url"] == b'http://foo/'
        assert mock_connect_polling["headers"] == []
        assert mock_connect_polling["engineio_path"] == b"/engine.io"

        c = trio_client.EngineIoClient()

        assert await c.connect(nursery, 'http://foo', transports=['polling', 'websocket'])
        assert mock_connect_polling["url"] == b'http://foo/'
        assert mock_connect_polling["headers"] == []
        assert mock_connect_polling["engineio_path"] == b"/engine.io"

    async def test_connect_websocket(self, nursery, mock_connect_websocket):
        c = trio_client.EngineIoClient()

        assert await c.connect(nursery, 'http://foo', transports=['websocket'])
        assert mock_connect_websocket["url"] == b'http://foo/'
        assert mock_connect_websocket["headers"] == []
        assert mock_connect_websocket["engineio_path"] == b"/engine.io"

        c = trio_client.EngineIoClient()

        assert await c.connect(nursery, 'http://foo', transports='websocket')
        assert mock_connect_websocket["url"] == b'http://foo/'
        assert mock_connect_websocket["headers"] == []
        assert mock_connect_websocket["engineio_path"] == b"/engine.io"

    async def test_connect_query_string(self, nursery, mock_connect_polling):
        c = trio_client.EngineIoClient()

        assert await c.connect(nursery, 'http://foo?bar=baz')
        assert mock_connect_polling["url"] == b'http://foo/?bar=baz'
        assert mock_connect_polling["headers"] == []
        assert mock_connect_polling["engineio_path"] == b"/engine.io"

    async def test_connect_custom_headers(self, nursery, mock_connect_polling):
        c = trio_client.EngineIoClient()

        assert await c.connect(nursery, 'http://foo', headers={'Foo': 'Bar'})
        assert mock_connect_polling["url"] == b'http://foo/'
        assert mock_connect_polling["headers"] == [(b"Foo", b"Bar")]
        assert mock_connect_polling["engineio_path"] == b"/engine.io"

    async def test_send(self, mock_send_packet):
        c = trio_client.EngineIoClient()
        c.state = "connected"

        await c.send('foo')
        await c.send('foo', binary=False)
        await c.send(b'foo', binary=True)

        assert mock_send_packet[0].packet_type == packet.MESSAGE
        assert mock_send_packet[0].data == 'foo'
        assert mock_send_packet[0].binary == False

        assert mock_send_packet[1].packet_type == packet.MESSAGE
        assert mock_send_packet[1].data == 'foo'
        assert not mock_send_packet[1].binary

        assert mock_send_packet[2].packet_type == packet.MESSAGE
        assert mock_send_packet[2].data == b'foo'
        assert mock_send_packet[2].binary

    async def test_send_not_connected(self, mock_send_packet):
        c = trio_client.EngineIoClient()
        c.state = 'foo'

        await c.send('bar')

        assert len(mock_send_packet) == 0

    async def test_disconnect_not_connected(self):
        c = trio_client.EngineIoClient()
        c.state = 'foo'
        c.sid = 'bar'

        await c.disconnect()

        assert c.state == 'disconnected'
        assert c._sid is None
        assert c._current_transport is None

    async def test_disconnect(self, mock_send_packet, mock_trigger_event):
        c = trio_client.EngineIoClient()
        trio_client.connected_clients.append(c)
        c.state = 'connected'
        c._current_transport = 'polling'
        c._ping_task_scope = MockCancelScope()

        await c.disconnect()

        assert mock_send_packet[0].packet_type == packet.CLOSE
        assert c._ping_task_scope.cancel_called
        assert mock_trigger_event["event"] == "disconnect"
        assert not mock_trigger_event["run_async"]
        assert c.state == "disconnected"
        assert c not in trio_client.connected_clients

    async def test_sleep(self):
        c = trio_client.EngineIoClient()
        await c.sleep(0)

    @pytest.mark.parametrize(("connections", "closed"), ((None, True), ("c1", False)))
    async def test_reset_polling(self, connections, closed):
        c = trio_client.EngineIoClient()
        c.state = "connected"
        c._sid = "123"
        c._current_transport = "polling"
        c._http = MockHttpConnectionPool()
        c._http.connections = connections

        await c._reset()

        assert c._http.closed == closed
        assert c.state == 'disconnected'
        assert c._sid is None
        assert c._current_transport is None

    @pytest.mark.parametrize("closed", (True, False))
    async def test_reset_websocket(self, closed):
        c = trio_client.EngineIoClient()
        c.state = "connected"
        c._sid = "123"
        c._current_transport = "websocket"
        c._ws = MockWebsocketConnection()
        c._ws.closed = closed

        await c._reset()

        assert c._ws.closed
        assert c.state == 'disconnected'
        assert c._sid is None
        assert c._current_transport is None

    async def test_polling_connection_failed(self, nursery, mock_time, mock_send_request):
        c = trio_client.EngineIoClient()

        async def fake_reset():
            return

        state, saved_request = mock_send_request
        state["mode"] = "connect"
        state["status"] = None
        c._reset = fake_reset

        with pytest.raises(EngineIoConnectionError, match="Connection refused by the server"):
            await c.connect(nursery, 'http://foo', headers={'Foo': 'Bar'})

        assert saved_request["method"] == "GET"
        assert bytes(saved_request["url"]) == b"http://foo/engine.io?transport=polling&EIO=3&t=123.456"
        assert saved_request["headers"] == [(b"Foo", b"Bar")]
        assert saved_request["timeouts"] == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}

    async def test_polling_connection_404(self, nursery, mock_time, mock_send_request):
        c = trio_client.EngineIoClient()

        async def fake_reset():
            return

        state, saved_request = mock_send_request
        state["mode"] = "connect"
        state["status"] = 404
        c._reset = fake_reset

        with pytest.raises(EngineIoConnectionError) as excinfo:
            await c.connect(nursery, 'http://foo/engine.io')
        assert str(excinfo.value) == "Unexpected status code 404 in server response"
        assert bytes(saved_request["url"]) == b"http://foo/engine.io?transport=polling&EIO=3&t=123.456"

    async def test_polling_connection_invalid_packet(self, nursery, mock_time, mock_send_request):
        c = trio_client.EngineIoClient()

        async def fake_reset():
            return

        state, saved_request = mock_send_request
        state["mode"] = "connect"
        state["status"] = 200
        state["returned_packet"] = "INVALID"
        c._reset = fake_reset

        with pytest.raises(EngineIoConnectionError, match="Unexpected response from server"):
            await c.connect(nursery, 'http://foo/socket.io')
        assert bytes(saved_request["url"]) == b"http://foo/socket.io?transport=polling&EIO=3&t=123.456"

    async def test_polling_connection_no_open_packet(self, nursery, mock_send_request):
        c = trio_client.EngineIoClient()

        async def fake_reset():
            return

        state, saved_request = mock_send_request
        state["mode"] = "connect"
        state["status"] = 200
        state["returned_packet"] = packet.Packet(packet.CLOSE)
        c._reset = fake_reset

        with pytest.raises(EngineIoConnectionError, match="OPEN packet not returned by server"):
            await c.connect(nursery, 'http://foo')

    @pytest.mark.parametrize("scheme", ("http", "https"))
    async def test_polling_connection_successful(self, scheme, nursery, mock_time, mock_send_request):
        c = trio_client.EngineIoClient()

        started_tasks = []
        triggered_events = []

        async def fake_start(fn, *args, **kwargs):
            started_tasks.append(fn)
            return True

        def on_connect():
            triggered_events.append("connect")

        c.on("connect", on_connect)

        state, saved_request = mock_send_request
        state["mode"] = "connect"
        state["status"] = 200
        state["returned_packet"] = packet.Packet(
            packet.OPEN,
            data={"sid": "123", "upgrades": [], "pingInterval": 1000, "pingTimeout": 2000}
        )
        nursery.start = fake_start

        await c.connect(nursery, f'{scheme}://foo/?test=1')

        assert c._sid == '123'
        assert c._upgrades == []
        assert c._ping_interval == 1
        assert c._ping_timeout == 2
        assert c.transport() == "polling"
        assert (
            bytes(c._base_url)
            == b'%b://foo/engine.io?test=1&transport=polling&EIO=3&sid=123&t=123.456' % scheme.encode("ascii")
        )
        assert c.state == "connected"
        assert c in trio_client.connected_clients
        assert "connect" in triggered_events
        assert c._ping_loop in started_tasks
        assert c._write_loop in started_tasks
        assert c._read_loop_polling in started_tasks
        assert c._read_loop_websocket not in started_tasks

    async def test_polling_connection_with_more_packets(self, nursery, mock_time, mock_send_request):
        c = trio_client.EngineIoClient()
        received_packets = []

        async def fake_start(*args, **kwargs):
            return True

        async def _receive_packet(pkt: packet.Packet):
            received_packets.append((pkt.packet_type, pkt.data))

        state, saved_request = mock_send_request
        state["mode"] = "connect"
        state["status"] = 200
        state["returned_packet"] = packet.Packet(
            packet.OPEN,
            data={"sid": "123", "upgrades": [], "pingInterval": 1000, "pingTimeout": 2000}
        )
        state["more_packets"] = True
        nursery.start = fake_start
        c._receive_packet = _receive_packet

        await c.connect(nursery, f'http://foo')

        assert (packet.NOOP, None) in received_packets

    async def test_polling_connection_upgraded(
            self, nursery, mock_time, mock_send_request
    ):
        c = trio_client.EngineIoClient()

        async def fake_connect_websocket(*args):
            return True

        c._connect_websocket = fake_connect_websocket

        triggered_events = []

        def on_connect():
            triggered_events.append("connect")

        c.on("connect", on_connect)

        state, saved_request = mock_send_request
        state["mode"] = "connect"
        state["status"] = 200
        state["returned_packet"] = packet.Packet(
            packet.OPEN,
            data={"sid": "123", "upgrades": ["websocket"], "pingInterval": 1000, "pingTimeout": 2000}
        )

        await c.connect(nursery, f'http://foo')

        assert c._sid == '123'
        assert c._upgrades == ["websocket"]
        assert c._ping_interval == 1
        assert c._ping_timeout == 2
        assert c.transport() == "polling"
        assert (
            bytes(c._base_url)
            == b'http://foo/engine.io?transport=polling&EIO=3&sid=123&t=123.456'
        )
        assert c.state == "connected"
        assert c in trio_client.connected_clients
        assert "connect" in triggered_events

    async def test_polling_connection_not_upgraded(
            self, nursery, mock_time, mock_send_request
    ):
        c = trio_client.EngineIoClient()

        started_tasks = []
        triggered_events = []

        async def fake_connect_websocket(*args):
            return False

        async def fake_start(fn, *args, **kwargs):
            started_tasks.append(fn)
            return True

        def on_connect():
            triggered_events.append("connect")

        c.on("connect", on_connect)

        state, saved_request = mock_send_request
        state["mode"] = "connect"
        state["status"] = 200
        state["returned_packet"] = packet.Packet(
            packet.OPEN,
            data={"sid": "123", "upgrades": ["websocket"], "pingInterval": 1000, "pingTimeout": 2000}
        )
        c._connect_websocket = fake_connect_websocket
        nursery.start = fake_start

        await c.connect(nursery, f'http://foo')

        assert c._sid == '123'
        assert c._upgrades == ["websocket"]
        assert c._ping_interval == 1
        assert c._ping_timeout == 2
        assert c.transport() == "polling"
        assert (
            bytes(c._base_url)
            == b'http://foo/engine.io?transport=polling&EIO=3&sid=123&t=123.456'
        )
        assert c.state == "connected"
        assert c in trio_client.connected_clients
        assert "connect" in triggered_events
        assert c._ping_loop in started_tasks
        assert c._write_loop in started_tasks
        assert c._read_loop_polling in started_tasks
        assert c._read_loop_websocket not in started_tasks

    async def test_websocket_connection_failed(
            self, nursery, mock_time, mock_connect_trio_websocket
    ):
        c = trio_client.EngineIoClient()

        state, called_with = mock_connect_trio_websocket
        state["success"] = False

        with pytest.raises(EngineIoConnectionError, match="Websocket connection error"):
            await c.connect(
                nursery, 'http://foo', transports=["websocket"], headers={'Foo': 'Bar'}
            )
        assert called_with["host"] == "foo"
        assert called_with["port"] is None
        assert called_with["resource"] == "/engine.io?transport=websocket&EIO=3&t=123.456"
        assert called_with["extra_headers"] == [(b"Foo", b"Bar")]
        assert not called_with["use_ssl"]

    async def test_websocket_upgrade_failed(self, nursery, mock_time, mock_connect_trio_websocket):
        c = trio_client.EngineIoClient()
        c._sid = "123"

        state, called_with = mock_connect_trio_websocket
        state["success"] = False

        assert not await c.connect(nursery, 'http://foo:1234', transports=["websocket"])
        assert called_with["host"] == "foo"
        assert called_with["port"] == 1234
        assert called_with["resource"] == "/engine.io?transport=websocket&EIO=3&sid=123&t=123.456"
        assert called_with["extra_headers"] == []
        assert not called_with["use_ssl"]

    @pytest.mark.parametrize("closed", (True, False))
    async def test_websocket_connection_no_open_packet(
            self, closed, nursery, mock_connect_trio_websocket
    ):
        c = trio_client.EngineIoClient()

        async def fake_reset():
            return

        state, called_with = mock_connect_trio_websocket
        state["success"] = True
        ws = MockWebsocketConnection()
        if closed:
            ws.closed = closed
            match = "Unexpected recv exception"
        else:
            ws.received = [packet.Packet(packet.CLOSE).encode()]
            match = "no OPEN packet"
        state["return_value"] = ws
        c._reset = fake_reset

        with pytest.raises(EngineIoConnectionError, match=match):
            await c.connect(nursery, 'http://foo', transports=["websocket"])

    @pytest.mark.parametrize(("scheme", "ssl_verify"), (("ws", True), ("wss", True), ("wss", False)))
    async def test_websocket_connection_successful(
            self, scheme, ssl_verify, nursery, mock_time, mock_connect_trio_websocket
    ):
        c = trio_client.EngineIoClient(ssl_verify=ssl_verify)

        started_tasks = []
        triggered_events = []

        async def fake_start(fn, *args, **kwargs):
            started_tasks.append(fn)
            return True

        def on_connect():
            triggered_events.append("connect")

        c.on("connect", on_connect)

        state, called_with = mock_connect_trio_websocket
        state["success"] = True
        ws = MockWebsocketConnection()
        ws.received = [
            packet.Packet(
                packet.OPEN,
                data={"sid": "123", "upgrades": [], "pingInterval": 1000, "pingTimeout": 2000}
            ).encode()
        ]
        state["return_value"] = ws
        nursery.start = fake_start

        assert await c.connect(nursery, f'{scheme}://foo', transports=["websocket"])
        assert c._sid == '123'
        assert c._upgrades == []
        assert c._ping_interval == 1
        assert c._ping_timeout == 2
        assert c.transport() == "websocket"
        assert (
            bytes(c._base_url)
            == b'%b://foo/engine.io?transport=websocket&EIO=3&t=123.456' % scheme.encode("ascii")
        )
        assert c.state == "connected"
        assert c in trio_client.connected_clients
        assert "connect" in triggered_events
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
            self, closed, nursery, mock_connect_trio_websocket
    ):
        c = trio_client.EngineIoClient()
        c._sid = "123"
        c._current_transport = "polling"

        started_tasks = []
        triggered_events = []

        async def fake_start(fn, *args, **kwargs):
            started_tasks.append(fn)
            return True

        def on_connect():
            triggered_events.append("connect")

        c.on("connect", on_connect)

        state, called_with = mock_connect_trio_websocket
        state["success"] = True
        ws = MockWebsocketConnection()
        ws.closed = closed
        state["return_value"] = ws
        nursery.start = fake_start

        assert not await c.connect(nursery, f'ws://foo', transports=["websocket"])
        if closed:
            assert len(ws.sent) == 0
        else:
            assert ws.sent[0] == packet.Packet(packet.PING, data="probe").encode(always_bytes=False)
        assert c.transport() == "polling"
        assert "connect" not in triggered_events
        assert c._ping_loop not in started_tasks
        assert c._write_loop not in started_tasks
        assert c._read_loop_websocket not in started_tasks
        assert c._read_loop_polling not in started_tasks

    async def test_websocket_upgrade_no_pong(
            self, nursery, mock_connect_trio_websocket
    ):
        c = trio_client.EngineIoClient()
        c._sid = "123"
        c._current_transport = "polling"

        started_tasks = []
        triggered_events = []

        async def fake_start(fn, *args, **kwargs):
            started_tasks.append(fn)
            return True

        def on_connect():
            triggered_events.append("connect")

        c.on("connect", on_connect)

        state, called_with = mock_connect_trio_websocket
        state["success"] = True
        ws = MockWebsocketConnection()
        ws.received = [
            packet.Packet(
                packet.OPEN,
                data={"sid": "123", "upgrades": [], "pingInterval": 1000, "pingTimeout": 2000}
            ).encode()
        ]
        state["return_value"] = ws
        nursery.start = fake_start

        assert not await c.connect(nursery, f'ws://foo', transports=["websocket"])
        assert ws.sent[0] == packet.Packet(packet.PING, data="probe").encode(always_bytes=False)
        assert c.transport() == "polling"
        assert "connect" not in triggered_events
        assert c._ping_loop not in started_tasks
        assert c._write_loop not in started_tasks
        assert c._read_loop_websocket not in started_tasks
        assert c._read_loop_polling not in started_tasks

    async def test_websocket_upgrade_upgrade_sending_failed(
            self, nursery, mock_connect_trio_websocket
    ):
        c = trio_client.EngineIoClient()
        c._sid = "123"
        c._current_transport = "polling"

        started_tasks = []
        triggered_events = []

        async def fake_start(fn, *args, **kwargs):
            started_tasks.append(fn)
            return True

        def on_connect():
            triggered_events.append("connect")

        c.on("connect", on_connect)

        state, called_with = mock_connect_trio_websocket
        state["success"] = True
        ws = MockWebsocketConnection()
        ws.received = [packet.Packet(packet.PONG, data="probe").encode()]
        ws.close_after_pong = True
        state["return_value"] = ws
        nursery.start = fake_start

        assert not await c.connect(nursery, f'ws://foo', transports=["websocket"])
        assert ws.sent[0] == packet.Packet(packet.PING, data="probe").encode(always_bytes=False)
        assert c.transport() == "polling"
        assert "connect" not in triggered_events
        assert c._ping_loop not in started_tasks
        assert c._write_loop not in started_tasks
        assert c._read_loop_websocket not in started_tasks
        assert c._read_loop_polling not in started_tasks

    async def test_websocket_upgrade_successful(self, nursery, mock_time, mock_connect_trio_websocket):
        c = trio_client.EngineIoClient()
        c._sid = "123"
        c._current_transport = "polling"
        c._base_url = eio_types.NoCachingURL(
            scheme=b"http",
            host=b"foo",
            port=None,
            target=b"/engine.io?transport=polling&EIO=3&sid=123"
        )

        started_tasks = []
        triggered_events = []

        async def fake_start(fn, *args, **kwargs):
            started_tasks.append(fn)
            return True

        def on_connect():
            triggered_events.append("connect")

        c.on("connect", on_connect)

        state, called_with = mock_connect_trio_websocket
        state["success"] = True
        ws = MockWebsocketConnection()
        ws.received = [packet.Packet(packet.PONG, data="probe").encode()]
        state["return_value"] = ws
        nursery.start = fake_start

        assert await c.connect(nursery, f'ws://foo', transports=["websocket"])
        assert ws.sent[0] == packet.Packet(packet.PING, data="probe").encode(always_bytes=False)
        assert ws.sent[1] == packet.Packet(packet.UPGRADE).encode(always_bytes=False)
        assert c._sid == '123'  # not changed
        assert (
            bytes(c._base_url)
            == b'http://foo/engine.io?transport=polling&EIO=3&sid=123&t=123.456'
        )   # not changed except t=... set when accessing the _base_url.target property
        assert c not in trio_client.connected_clients   # was added by polling connection
        assert "connect" not in triggered_events    # was called by polling connection
        assert c.transport() == "websocket"
        assert c._ws == ws
        assert c._ping_loop in started_tasks
        assert c._write_loop in started_tasks
        assert c._read_loop_websocket in started_tasks
        assert c._read_loop_polling not in started_tasks

    async def test_receive_unknown_packet(self):
        c = trio_client.EngineIoClient()

        await c._receive_packet(packet.Packet(encoded_packet=b'9'))
        # should be ignored

    async def test_receive_noop_packet(self):
        c = trio_client.EngineIoClient()

        await c._receive_packet(packet.Packet(packet.NOOP))
        # should be ignored

    async def test_receive_pong_packet(self):
        c = trio_client.EngineIoClient()
        c._pong_received = False

        await c._receive_packet(packet.Packet(packet.PONG))

        assert c._pong_received

    async def test_receive_message_packet(self, mock_trigger_event):
        c = trio_client.EngineIoClient()

        await c._receive_packet(packet.Packet(packet.MESSAGE, {'foo': 'bar'}))

        assert mock_trigger_event["event"] == "message"
        assert mock_trigger_event["args"] == ({'foo': 'bar'},)
        assert mock_trigger_event["run_async"]

    async def test_receive_close_packet(self):
        c = trio_client.EngineIoClient()

        disconnect_calls = []

        async def fake_disconnect():
            disconnect_calls.append(True)

        c.disconnect = fake_disconnect

        await c._receive_packet(packet.Packet(packet.CLOSE))

        assert disconnect_calls[0]

    async def test_send_packet_disconnected(self):
        c = trio_client.EngineIoClient()
        c.state = 'disconnected'
        c._send_channel, c._receive_channel = trio.open_memory_channel(1)

        await c._send_packet(packet.Packet(packet.NOOP))

        with pytest.raises((trio.EndOfChannel, trio.WouldBlock)):
            c._receive_channel.receive_nowait()

    async def test_send_packet(self):
        c = trio_client.EngineIoClient()
        c.state = 'connected'
        c._send_channel, c._receive_channel = trio.open_memory_channel(1)

        await c._send_packet(packet.Packet(packet.NOOP))

        pkt = c._receive_channel.receive_nowait()
        assert pkt.packet_type == packet.NOOP

    @pytest.mark.parametrize("method", ("BBB", "GET"))
    async def test_send_request_no_http(self, method):
        c = trio_client.EngineIoClient()

        trio_client.httpcore.AsyncConnectionPool = MockHttpConnectionPool

        r = await c._send_request(method, "http://foo")

        if method == "BBB":
            assert r is None
        else:
            assert r.status == 200

    async def test_send_request_with_http(self):
        c = trio_client.EngineIoClient()
        c._http = MockHttpConnectionPool()

        r = await c._send_request("GET", "http://foo")

        assert r.status == 200

    async def test_trigger_event_function(self):
        result = []

        def foo_handler(arg):
            result.append('ok')
            result.append(arg)

        c = trio_client.EngineIoClient()
        c.on('message', handler=foo_handler)

        await c._trigger_event('message', 'bar')

        assert result == ['ok', 'bar']

    async def test_trigger_event_coroutine(self):
        result = []

        async def foo_handler(arg):
            result.append('ok')
            result.append(arg)

        c = trio_client.EngineIoClient()
        c.on('message', handler=foo_handler)

        await c._trigger_event('message', 'bar')

        assert result == ['ok', 'bar']

    async def test_trigger_event_function_error(self):
        def connect_handler(_arg):
            return 1 / 0

        def foo_handler(_arg):
            return 1 / 0

        c = trio_client.EngineIoClient()
        c.on('connect', handler=connect_handler)
        c.on('message', handler=foo_handler)

        assert not await c._trigger_event('connect', '123')
        assert await c._trigger_event('message', 'bar') is None

    async def test_trigger_event_coroutine_error(self):
        async def connect_handler(arg):
            return 1 / 0

        async def foo_handler(arg):
            return 1 / 0

        c = trio_client.EngineIoClient()
        c.on('connect', handler=connect_handler)
        c.on('message', handler=foo_handler)

        assert not await c._trigger_event('connect', '123')
        assert await c._trigger_event('message', 'bar') is None

    async def test_trigger_event_function_async(self):
        result = []

        def foo_handler(arg):
            result.append('ok')
            result.append(arg)

        c = trio_client.EngineIoClient()
        c.on('message', handler=foo_handler)

        await c._trigger_event('message', 'bar', run_async=True)

        assert result == ['ok', 'bar']

    async def test_trigger_event_coroutine_async(self):
        result = []

        async def foo_handler(arg):
            result.append('ok')
            result.append(arg)

        c = trio_client.EngineIoClient()
        c.on('message', handler=foo_handler)

        await c._trigger_event('message', 'bar', run_async=True)

        assert result == ['ok', 'bar']

    async def test_trigger_event_function_async_error(self):
        result = []

        def foo_handler(arg):
            result.append(arg)
            return 1 / 0

        c = trio_client.EngineIoClient()
        c.on('message', handler=foo_handler)

        await c._trigger_event('message', 'bar', run_async=True)

        assert result == ['bar']

    async def test_trigger_event_coroutine_async_error(self):
        result = []

        async def foo_handler(arg):
            result.append(arg)
            return 1 / 0

        c = trio_client.EngineIoClient()
        c.on('message', handler=foo_handler)

        await c._trigger_event('message', 'bar', run_async=True)

        assert result == ['bar']

    async def test_trigger_unknown_event(self):
        c = trio_client.EngineIoClient()

        await c._trigger_event('connect', run_async=False)
        await c._trigger_event('message', 123, run_async=True)
        # should do nothing

    async def test_ping_loop_disconnected(self):
        c = trio_client.EngineIoClient()
        c.state = "disconnected"
        c._ping_interval = 2
        c._ping_timeout = 1
        c._write_task_scope = MockCancelScope()
        c._read_task_scope = MockCancelScope()

        await c._ping_loop()

        assert c._write_task_scope.cancel_called
        assert c._read_task_scope.cancel_called

    async def test_ping_loop_disconnect(self, monkeypatch, mock_send_packet):
        c = trio_client.EngineIoClient()
        c.state = "connected"
        c._ping_interval = 2
        c._ping_timeout = 1
        c._write_task_scope = MockCancelScope()
        c._read_task_scope = MockCancelScope()

        async def fake_trio_sleep(_):
            c.state = "disconnecting"

        monkeypatch.setattr("trio_engineio.trio_client.trio.sleep", fake_trio_sleep)

        await c._ping_loop()

        assert not c._pong_received
        assert mock_send_packet[0].packet_type == packet.PING
        assert c._write_task_scope.cancel_called
        assert c._read_task_scope.cancel_called

    async def test_ping_loop_missing_pong(self, monkeypatch, mock_send_packet):
        c = trio_client.EngineIoClient()
        c.state = "connected"
        c._ping_interval = 2
        c._ping_timeout = 1
        c._write_task_scope = MockCancelScope()
        c._read_task_scope = MockCancelScope()

        async def fake_trio_sleep(_):
            c._pong_received = False

        monkeypatch.setattr("trio_engineio.trio_client.trio.sleep", fake_trio_sleep)

        await c._ping_loop()

        assert c.state == 'connected'
        assert not c._pong_received
        assert mock_send_packet[0].packet_type == packet.PING
        assert c._write_task_scope.cancel_called
        assert c._read_task_scope.cancel_called

    async def test_ping_loop_missing_pong_websocket(self, monkeypatch, mock_send_packet):
        c = trio_client.EngineIoClient()
        c.state = "connected"
        c._ping_interval = 2
        c._ping_timeout = 1
        c._write_task_scope = MockCancelScope()
        c._read_task_scope = MockCancelScope()
        c._ws = MockWebsocketConnection()

        async def fake_trio_sleep(_):
            c._pong_received = False

        monkeypatch.setattr("trio_engineio.trio_client.trio.sleep", fake_trio_sleep)

        await c._ping_loop()

        assert c.state == 'connected'
        assert not c._pong_received
        assert mock_send_packet[0].packet_type == packet.PING
        assert c._write_task_scope.cancel_called
        assert c._read_task_scope.cancel_called
        assert c._ws.closed

    async def test_read_loop_polling_disconnected(self, mock_trigger_event):
        c = trio_client.EngineIoClient()
        c.state = 'disconnected'
        c._ping_interval = 2
        c._ping_timeout = 1
        c._write_task_scope = MockCancelScope()
        c._ping_task_scope = MockCancelScope()

        async def fake_reset():
            return

        c._reset = fake_reset

        await c._read_loop_polling()

        assert c._write_task_scope.cancel_called
        assert c._ping_task_scope.cancel_called
        assert mock_trigger_event == {}

    @pytest.mark.parametrize(("status", "pkt"), ((None, None), (404, None), (200, "INVALID")))
    async def test_read_loop_polling_error(
            self, status, pkt, mock_send_request, mock_trigger_event
    ):
        c = trio_client.EngineIoClient()
        c._ping_interval = 25
        c._ping_timeout = 5
        c.state = 'connected'
        trio_client.connected_clients.append(c)
        c._base_url = b'http://foo'
        c._write_task_scope = MockCancelScope()
        c._ping_task_scope = MockCancelScope()

        async def fake_reset():
            c.state = "disconnected"
            return

        state, saved_request = mock_send_request
        state["mode"] = "poll"
        state["status"] = status
        state["returned_packet"] = pkt
        c._reset = fake_reset

        await c._read_loop_polling()

        assert saved_request["method"] == "GET"
        assert bytes(saved_request["url"]) == b"http://foo"
        assert saved_request["headers"] is None
        assert saved_request["timeouts"] == {
            "connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0
        }
        assert c._write_task_scope.cancel_called
        assert c._ping_task_scope.cancel_called
        assert mock_trigger_event["event"] == "disconnect"
        assert mock_trigger_event["args"] == ()
        assert not mock_trigger_event["run_async"]
        assert c not in trio_client.connected_clients
        assert c.state == 'disconnected'

    async def test_read_loop_polling(self, mock_send_request):
        c = trio_client.EngineIoClient()
        c._ping_interval = 25
        c._ping_timeout = 5
        c.state = 'connected'
        c._base_url = b'http://foo'
        c._write_task_scope = MockCancelScope()
        c._ping_task_scope = MockCancelScope()

        received_packets = []

        async def _receive_packet(pkt: packet.Packet):
            received_packets.append((pkt.packet_type, pkt.data))
            if len(received_packets) > 1:
                c.state = "disconnected"

        async def fake_reset():
            c.state = "disconnected"
            return

        state, saved_request = mock_send_request
        state["mode"] = "poll"
        state["status"] = 200
        state["returned_packet"] = packet.Packet(packet.PONG)
        state["more_packets"] = True
        c._reset = fake_reset
        c._receive_packet = _receive_packet

        await c._read_loop_polling()

        assert saved_request["method"] == "GET"
        assert bytes(saved_request["url"]) == b"http://foo"
        assert saved_request["headers"] is None
        assert saved_request["timeouts"] == {
            "connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0
        }
        assert (3, None) in received_packets
        assert (6, None) in received_packets

    async def test_read_loop_websocket_disconnected(self, mock_trigger_event):
        c = trio_client.EngineIoClient()
        c.state = 'disconnected'
        c._ping_interval = 2
        c._ping_timeout = 1
        c._write_task_scope = MockCancelScope()
        c._ping_task_scope = MockCancelScope()

        async def fake_reset():
            return

        c._reset = fake_reset

        await c._read_loop_websocket()

        assert c._write_task_scope.cancel_called
        assert c._ping_task_scope.cancel_called
        assert mock_trigger_event == {}

    async def test_read_loop_websocket_no_response(self, mock_trigger_event):
        c = trio_client.EngineIoClient()
        c._ping_interval = 25
        c._ping_timeout = 5
        c.state = 'connected'
        trio_client.connected_clients.append(c)
        c._base_url = b'ws://foo'
        c._write_task_scope = MockCancelScope()
        c._ping_task_scope = MockCancelScope()
        c._ws = MockWebsocketConnection()

        async def fake_reset():
            c.state = "disconnected"
            return

        c._reset = fake_reset

        await c._read_loop_websocket()

        assert c._write_task_scope.cancel_called
        assert c._ping_task_scope.cancel_called
        assert mock_trigger_event["event"] == "disconnect"
        assert mock_trigger_event["args"] == ()
        assert not mock_trigger_event["run_async"]
        assert c not in trio_client.connected_clients
        assert c.state == 'disconnected'

    @pytest.mark.parametrize("pkt", ("ERROR", b"bfoo", None, "TIMEOUT"))
    async def test_read_loop_websocket_unexpected_error(
            self, pkt, mock_trigger_event, autojump_clock
    ):
        c = trio_client.EngineIoClient()
        c._ping_interval = 2
        c._ping_timeout = 1
        c.state = 'connected'
        trio_client.connected_clients.append(c)
        c._base_url = b'ws://foo'
        c._write_task_scope = MockCancelScope()
        c._ping_task_scope = MockCancelScope()
        c._ws = MockWebsocketConnection()
        c._ws.received = [pkt]

        async def fake_reset():
            c.state = "disconnected"
            return

        c._reset = fake_reset

        await c._read_loop_websocket()

        assert c._write_task_scope.cancel_called
        assert c._ping_task_scope.cancel_called
        assert mock_trigger_event["event"] == "disconnect"
        assert mock_trigger_event["args"] == ()
        assert not mock_trigger_event["run_async"]
        assert c not in trio_client.connected_clients
        assert c.state == 'disconnected'

    async def test_read_loop_websocket(self):
        c = trio_client.EngineIoClient()
        c._ping_interval = 25
        c._ping_timeout = 5
        c.state = 'connected'
        trio_client.connected_clients.append(c)
        c._base_url = b'ws://foo'
        c._write_task_scope = MockCancelScope()
        c._ping_task_scope = MockCancelScope()
        c._ws = MockWebsocketConnection()
        c._ws.received = ["4[message]", packet.Packet(packet.CLOSE).encode()]

        received_packets = []

        async def _receive_packet(pkt: packet.Packet):
            received_packets.append((pkt.packet_type, pkt.data))
            if len(received_packets) > 1:
                c.state = "disconnected"

        async def fake_reset():
            c.state = "disconnected"
            return

        c._receive_packet = _receive_packet
        c._reset = fake_reset

        await c._read_loop_websocket()

        assert  received_packets[0] == (4, "[message]")
        assert  received_packets[1] == (1, None)
        assert c._write_task_scope.cancel_called
        assert c._ping_task_scope.cancel_called
        assert c.state == 'disconnected'

    async def test_write_loop_disconnected(self):
        c = trio_client.EngineIoClient()
        c._ping_interval = 25
        c._ping_timeout = 5
        c._current_transport = "polling"
        c.state = 'disconnected'

        await c._write_loop()
        # should not block

    async def test_write_loop_no_packets(self):
        c = trio_client.EngineIoClient()
        c._ping_interval = 25
        c._ping_timeout = 5
        c._current_transport = "polling"
        c.state = 'connected'
        c._send_channel, c._receive_channel = trio.open_memory_channel(1)
        await c._send_channel.send(None)

        await c._write_loop()
        # should not block

    async def test_write_loop_empty_channel(self, autojump_clock):
        c = trio_client.EngineIoClient()
        c._ping_interval = 2
        c._ping_timeout = 1
        c._current_transport = "polling"
        c.state = 'connected'
        c._send_channel, c._receive_channel = trio.open_memory_channel(1)

        await c._write_loop()
        # should not block

    async def test_write_loop_closed_channel(self):
        c = trio_client.EngineIoClient()
        c._ping_interval = 2
        c._ping_timeout = 1
        c._current_transport = "polling"
        c.state = 'connected'
        c._send_channel, c._receive_channel = trio.open_memory_channel(1)
        await c._send_channel.send(packet.Packet(packet.PONG))
        c._receive_channel.close()

        await c._write_loop()
        # should not block

    async def test_write_loop_polling_one_packet(self, mock_send_request, autojump_clock):
        c = trio_client.EngineIoClient()
        c = trio_client.EngineIoClient()
        c._ping_interval = 2
        c._ping_timeout = 1
        c._current_transport = "polling"
        c.state = 'connected'
        c._base_url = b'http://foo'
        c._send_channel, c._receive_channel = trio.open_memory_channel(1)

        state, saved_request = mock_send_request
        state["mode"] = "post"
        state["status"] = 200

        pkt = packet.Packet(packet.MESSAGE, {'foo': 'bar'})
        await c._send_channel.send(pkt)

        await c._write_loop()

        assert saved_request["method"] == "POST"
        assert bytes(saved_request["url"]) == b"http://foo"
        assert saved_request["headers"] == {'Content-Type': 'application/octet-stream'}
        p = payload.Payload([pkt])
        assert saved_request["body"] == p.encode()
        assert saved_request["timeouts"] == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}

    async def test_write_loop_polling_three_packets(self, mock_send_request, autojump_clock):
        c = trio_client.EngineIoClient()
        c._ping_interval = 2
        c._ping_timeout = 1
        c._current_transport = "polling"
        c.state = 'connected'
        c._base_url = b'http://foo'
        c._send_channel, c._receive_channel = trio.open_memory_channel(10)

        state, saved_request = mock_send_request
        state["mode"] = "post"
        state["status"] = 200

        pkts = [
            packet.Packet(packet.MESSAGE, {'foo': 'bar'}),
            packet.Packet(packet.PING),
            packet.Packet(packet.NOOP),
        ]
        for pkt in pkts:
            await c._send_channel.send(pkt)

        await c._write_loop()

        assert saved_request["method"] == "POST"
        assert bytes(saved_request["url"]) == b"http://foo"
        assert saved_request["headers"] == {'Content-Type': 'application/octet-stream'}
        p = payload.Payload(pkts)
        assert saved_request["body"] == p.encode()
        assert saved_request["timeouts"] == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}

    @pytest.mark.parametrize("status", (None, 500))
    async def test_write_loop_polling_connection_error(
            self, status, mock_send_request, autojump_clock
    ):
        c = trio_client.EngineIoClient()
        c._ping_interval = 2
        c._ping_timeout = 1
        c._current_transport = "polling"
        c.state = 'connected'
        c._base_url = b'http://foo'
        c._send_channel, c._receive_channel = trio.open_memory_channel(10)

        state, saved_request = mock_send_request
        state["mode"] = "post"
        state["status"] = status

        pkts = [
            packet.Packet(packet.MESSAGE, {'foo': 'bar'}),
        ]
        for pkt in pkts:
            await c._send_channel.send(pkt)

        await c._write_loop()

        assert saved_request["method"] == "POST"
        assert bytes(saved_request["url"]) == b"http://foo"
        assert saved_request["headers"] == {'Content-Type': 'application/octet-stream'}
        p = payload.Payload(pkts)
        assert saved_request["body"] == p.encode()
        assert saved_request["timeouts"] == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}
        assert c.state == 'connected'

    async def test_write_loop_websocket_one_packet(self, autojump_clock):
        c = trio_client.EngineIoClient()
        c._ping_interval = 2
        c._ping_timeout = 1
        c._current_transport = "websocket"
        c.state = 'connected'
        c._send_channel, c._receive_channel = trio.open_memory_channel(10)
        c._ws = MockWebsocketConnection()

        pkts = [
            packet.Packet(packet.MESSAGE, {'foo': 'bar'}),
        ]
        for pkt in pkts:
            await c._send_channel.send(pkt)

        await c._write_loop()

        assert c._ws.sent[0] == '4{"foo":"bar"}'

    async def test_write_loop_websocket_three_packets(self, autojump_clock):
        c = trio_client.EngineIoClient()
        c._ping_interval = 2
        c._ping_timeout = 1
        c._current_transport = "websocket"
        c.state = 'connected'
        c._send_channel, c._receive_channel = trio.open_memory_channel(10)
        c._ws = MockWebsocketConnection()

        pkts = [
            packet.Packet(packet.MESSAGE, {'foo': 'bar'}),
            packet.Packet(packet.PING),
            packet.Packet(packet.NOOP),
        ]
        for pkt in pkts:
            await c._send_channel.send(pkt)

        await c._write_loop()

        assert c._ws.sent[0] == '4{"foo":"bar"}'
        assert c._ws.sent[1] == '2'
        assert c._ws.sent[2] == '6'

    async def test_write_loop_websocket_one_packet_binary(self, autojump_clock):
        c = trio_client.EngineIoClient()
        c._ping_interval = 2
        c._ping_timeout = 1
        c._current_transport = "websocket"
        c.state = 'connected'
        c._send_channel, c._receive_channel = trio.open_memory_channel(10)
        c._ws = MockWebsocketConnection()

        pkts = [
            packet.Packet(packet.MESSAGE, b"foo"),
        ]
        for pkt in pkts:
            await c._send_channel.send(pkt)

        await c._write_loop()

        assert c._ws.sent[0] == b'\x04foo'

    async def test_write_loop_websocket_bad_connection(self, autojump_clock):
        c = trio_client.EngineIoClient()
        c._ping_interval = 2
        c._ping_timeout = 1
        c._current_transport = "websocket"
        c.state = 'connected'
        c._send_channel, c._receive_channel = trio.open_memory_channel(10)
        c._ws = MockWebsocketConnection()
        c._ws.closed = True

        pkts = [
            packet.Packet(packet.MESSAGE, {'foo': 'bar'}),
        ]
        for pkt in pkts:
            await c._send_channel.send(pkt)

        await c._write_loop()

        assert c.state == 'connected'
