from __future__ import annotations

import inspect
import logging
import typing
from typing import Optional, Callable, Any, Sequence, AsyncIterator

import trio
import httpcore
from httpcore.backends import trio as trio_backend
import trio_websocket as trio_ws

from .eio_types import (
    EventName,
    JsonProtocol,
    Transport,
    HeadersAsSequence,
    HeadersAsMapping,
    Headers,
    Timeouts,
    enforce_bytes,
    enforce_url,
    enforce_headers,
    NoCachingURL,
)
from .trio_util import ResultCapture, TaskWrappedException
from .exceptions import EngineIoConnectionError
from ._ssl import default_ssl_context
from . import packet
from . import payload

default_logger = logging.getLogger("engineio.client")
connected_clients = []


# def signal_handler(sig, frame):
#     """SIGINT handler.
#
#     Disconnect all active clients and then invoke the original signal handler.
#     """
#     for client in connected_clients[:]:
#         if not client.is_asyncio_based():
#             client.disconnect()
#     if callable(original_signal_handler):
#         return original_signal_handler(sig, frame)
#     else:  # pragma: no cover
#         # Handle case where no original SIGINT handler was present.
#         return signal.default_int_handler(sig, frame)
#
#
# original_signal_handler = None
#
# async_signal_handler_set = False
#
#
# def async_signal_handler():
#     """SIGINT handler.
#
#     Disconnect all active async clients.
#     """
#     async def _handler():
#         asyncio.get_event_loop().stop()
#         for c in client.connected_clients[:]:
#             if c.is_asyncio_based():
#                 await c.disconnect()
#         else:  # pragma: no cover
#             pass
#
#     asyncio.ensure_future(_handler())


class EngineIoClient:
    """An Engine.IO client for trio.

    This class implements an asynchronous Engine.IO client with support for websocket
    and long-polling transports, compatible with the revision 3 of Engine.IO protocol
    and the trio framework.

    Args:
        logger: The logging configuration. Possible values are:
            - `False` to disable logging. Note that fatal errors will still be logged.
            - `True` to enable logging at INFO log level.
            - a custom `logging.Logger` object.
        json: An alternate json module to use for encoding and decoding packets.
            Custom json modules must have `dumps` and `loads` functions that are
            compatible with the standard library versions.
        request_timeout: A timeout in seconds for requests. The default is 5 seconds.
        http_session: an initialized `httpcore.AsyncConnectionPool` object to be used
            when sending requests to the server. Use it if you need to add special
            client options such as proxy servers, SSL certificates, etc.
        ssl_verify: `True` to verify SSL certificates, or `False` to skip SSL
            certificate verification, allowing connections to servers with self-signed
            certificates. The default is `True`.

    Attributes:
        state: A string representing the state of the Engine.IO connection. Possible
            values are:
            - "disconnected" at initialization or after connection closing.
            - "connected" after a successful connection handshake.
            - "disconnecting", transient state during connection closing.
    """

    event_names: tuple[EventName, ...] = typing.get_args(EventName)
    """A tuple of authorized keys to identify an event handlers (Class attribute).
    """

    def __init__(
        self,
        logger: logging.Logger | bool = False,
        json: JsonProtocol | None = None,
        request_timeout: float = 5.0,
        http_session: httpcore.AsyncConnectionPool | None = None,
        ssl_verify: bool = True,
    ) -> None:
        # global original_signal_handler
        # if original_signal_handler is None and \
        #         threading.current_thread() == threading.main_thread():
        #     original_signal_handler = signal.signal(signal.SIGINT,
        #                                             signal_handler)
        self.state: str = "disconnected"

        self._handlers: dict[EventName, Callable[[Any], Any]] = {}
        self._base_url: NoCachingURL | None = None
        self._transports: Sequence[Transport] = ["polling", "websocket"]
        self._current_transport: Transport | None = None
        self._sid: str | None = None
        self._upgrades: Sequence[Transport] | None = None
        self._ping_interval: float | None = None
        self._ping_timeout: float | None = None
        self._pong_received: bool = True
        self._http: httpcore.AsyncConnectionPool | None = http_session
        self._ws: trio_ws.WebSocketConnection | None = None
        self._send_channel: trio.MemorySendChannel | None = None
        self._receive_channel: trio.MemoryReceiveChannel | None = None
        self._ssl_verify: bool = ssl_verify
        self._ping_task_scope: trio.CancelScope | None = None
        self._write_task_scope: trio.CancelScope | None = None
        self._read_task_scope: trio.CancelScope | None = None
        self._timeouts: Timeouts = {
            "connect": request_timeout,
            "read": request_timeout,
            "write": request_timeout,
            "pool": request_timeout,
        }

        if json is not None:
            packet.Packet.json = json

        self._logger: logging.Logger
        if not isinstance(logger, bool):
            self._logger = logger
        else:
            self._logger = default_logger
            if self._logger.level == logging.NOTSET:
                if logger:
                    self._logger.setLevel(logging.INFO)
                else:
                    self._logger.setLevel(logging.ERROR)
                self._logger.addHandler(logging.StreamHandler())

    def on(
        self, event: EventName, handler: Callable[[Any], Any] | None = None
    ) -> Callable[[Any], Any]:
        """Register an event handler.

        Args:
            event: The event name. Can be "connect", "message" or "disconnect".
            handler: The function that should be invoked to handle the event.
                When this parameter is not given, the method acts as a decorator for
                the handler function.

        Raises:
            ValueError: Raised if `event` is not a valid event name.

        Examples:
            - As a decorator:
                    @eio.on('connect')
                    def connect_handler():
                        print('Connection request')

            - As a method:
                    def message_handler(msg):
                        print('Received message: ', msg)
                        eio.send('response')
                    eio.on('message', message_handler)
        """
        if event not in EngineIoClient.event_names:
            raise ValueError("Invalid event")

        def set_handler(h):
            self._handlers[event] = h
            return h

        if handler is None:
            return set_handler
        set_handler(handler)

    async def connect(
        self,
        nursery: trio.Nursery,
        url: httpcore.URL | bytes | str,
        headers: HeadersAsMapping | HeadersAsSequence | None = None,
        transports: Sequence[Transport] | None = None,
        engineio_path: str | bytes = b"/engine.io",
    ) -> bool:
        """Connect to an Engine.IO server.

        Args:
            nursery: a `trio.Nursery` object in which to run the Engine.IO background
                tasks.
            url: The URL of the Engine.IO server. It can include custom query string
                parameters if required by the server.
            headers: An optional dictionary with custom headers to send with the
                connection request.
            transports: A sequence of allowed transports. Valid transports are "polling"
                and "websocket". If not given, the polling transport is connected
                first, then an upgrade to websocket is attempted.
            engineio_path: The endpoint where the Engine.IO server is installed.
                The default value is appropriate for most cases.

        Raises:
            EngineIoConnectionError: Raised if called while the Engine.IO client is not
                in a "disconnected" state or if no valid transports are provided or if
                either `url` or `headers` or `engineio_path` type are incorrect.

        Returns:
            `True` if the connection succeeded, `False` otherwise.

        Examples:
            eio = EngineIoClient()
            async with trio.open_nursery() as nursery:
                await eio.connect(nursery, "http://127.0.0.1:1234")
        """
        # global async_signal_handler_set
        # if not async_signal_handler_set and \
        #         threading.current_thread() == threading.main_thread():
        #
        #     try:
        #         asyncio.get_event_loop().add_signal_handler(
        #             signal.SIGINT, async_signal_handler)
        #         async_signal_handler_set = True
        #     except NotImplementedError:  # pragma: no cover
        #         self._logger.warning('Signal handler is unsupported')

        if self.state != "disconnected":
            raise EngineIoConnectionError(
                "Client is already connected or disconnecting"
            )

        valid_transports = ["polling", "websocket"]
        if transports is not None:
            if isinstance(transports, str):
                transports = [transports]
            transports = [
                transport for transport in transports if transport in valid_transports
            ]
            if not transports:
                raise EngineIoConnectionError("No valid transports provided")
        self._transports = transports or valid_transports

        try:
            url = enforce_url(url, name="url")
            headers = enforce_headers(headers, name="headers")
            engineio_path = enforce_bytes(engineio_path, name="path")
        except TypeError as e:
            raise EngineIoConnectionError(f"Bad type: {e}")

        self._send_channel, self._receive_channel = trio.open_memory_channel(10)

        if self._transports[0] == "polling":
            return await self._connect_polling(nursery, url, headers, engineio_path)
        # websocket
        return await self._connect_websocket(nursery, url, headers, engineio_path)

    # async def wait(self):
    #     """Wait until the connection with the server ends.
    #
    #     Client applications can use this function to block the main thread
    #     during the life of the connection.
    #
    #     Note: this method is a coroutine.
    #     """
    #     if self.read_loop_task:
    #         await self.read_loop_task

    async def send(self, data: str | bytes | list | dict, binary: bool = None) -> None:
        """Send a message to a client.

        Do nothing if the client is not connected.

        Args:
            data: The data to send to the client.
                Data can be of type `str`, `bytes`, `list` or `dict`.
                If a `list` or `dict`, the data will be serialized as JSON.
            binary: `True` to send packet as binary, `False` to send as text.
                If not given, `str` are sent as text and `bytes` are sent as binary.
        """
        if self.state != "connected":
            self._logger.warning(
                "Attempt to send data before a connection is established"
            )
            return

        await self._send_packet(packet.Packet(packet.MESSAGE, data=data, binary=binary))

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        if self.state == "connected":
            await self._send_packet(packet.Packet(packet.CLOSE))

            self.state = "disconnecting"

            # The ping loop task may sleep: cancel it (it will in turn cancel the
            # read and write loop task).
            self._logger.debug("User disconnection: Cancelling ping loop task")
            self._ping_task_scope.cancel()

            await self._trigger_event("disconnect", run_async=False)

            self.state = "disconnected"

            try:
                connected_clients.remove(self)
            except ValueError:  # pragma: no cover
                pass

        self.state = "disconnected"
        self._sid = None
        self._current_transport = None

    def transport(self) -> Transport:
        """Return the name of the transport currently in use.

        Returns:
            Either "polling" or "websocket" when the client is connected
            or `None` otherwise.
        """
        return self._current_transport

    async def sleep(self, seconds: float = 0.0) -> None:
        """Sleep for the requested amount of time.

        Args:
            seconds: sleep duration in seconds.
        """
        return await trio.sleep(seconds)

    async def _reset(self) -> None:
        """Reset the Engine.io client.

        Try to politely close the underlying transport connection.
        """
        if self._http:
            self._logger.info("Reset: Closing HTTP connections pool")
            self._logger.debug(
                f"Reset: Current conections are: {self._http.connections}"
            )
            try:
                await self._http.aclose()
            except RuntimeError:
                self._logger.exception(
                    f"Reset: Error while closing the HTTP connection pool"
                )
            else:
                self._logger.info("Reset: HTTP connections pool closed")

        if self._ws:
            if self._ws.closed:
                self._logger.debug(
                    f"Reset: Websocket already closed: {self._ws.closed}"
                )
            else:
                self._logger.info("Reset: Closing websocket")
                with trio.move_on_after(self._timeouts["connect"]):
                    await self._ws.aclose()
                self._logger.info(f"Reset: Websocket closed: {self._ws.closed}")

        self.state = "disconnected"
        self._sid = None
        self._current_transport = None

    async def _connect_polling(
        self,
        nursery: trio.Nursery,
        url: httpcore.URL,
        headers: Headers,
        engineio_path: bytes,
    ) -> bool:
        """Establish a long-polling connection to the Engine.IO server.

        Args:
            nursery: a `trio.Nursery` object in which to run the Engine.IO background
                tasks.
            url: The URL to connect to.
            headers: A sequence of custom headers keys-values to send with the
                connection request. Empty list if no custome headers.
            engineio_path: The endpoint to connect to.

        Raises:
            EngineIoConnectionError: Raised if the connection fails.

        Returns:
            `True` if the connection succeeds.
        """
        self._base_url: NoCachingURL = self._get_engineio_url(
            url, engineio_path, "polling"
        )
        self._logger.info(f"Connect: Attempting polling connection to {self._base_url}")

        r = await self._send_request(
            "GET", self._base_url, headers=headers, timeouts=self._timeouts
        )

        if r is None:
            await self._reset()
            raise EngineIoConnectionError("Connection refused by the server")

        if r.status < 200 or r.status >= 300:
            await self._reset()
            raise EngineIoConnectionError(
                f"Unexpected status code {r.status} in server response"
            )

        ep = await r.aread()
        try:
            p = payload.Payload(encoded_payload=ep)
        except ValueError:
            await self._reset()
            raise EngineIoConnectionError("Unexpected response from server")

        open_packet = p.packets[0]
        if open_packet.packet_type != packet.OPEN:
            await self._reset()
            raise EngineIoConnectionError("OPEN packet not returned by server")

        self._logger.info(
            f"Connect: Polling connection accepted with {open_packet.data}"
        )
        self._sid = open_packet.data["sid"]
        self._upgrades = open_packet.data["upgrades"]
        self._ping_interval = int(open_packet.data["pingInterval"]) / 1000.0
        self._ping_timeout = int(open_packet.data["pingTimeout"]) / 1000.0
        self._current_transport = "polling"
        self._base_url.add_to_target(b"&sid=" + enforce_bytes(self._sid, name="sid"))

        self.state = "connected"
        connected_clients.append(self)

        await self._trigger_event("connect", run_async=False)

        for pkt in p.packets[1:]:
            await self._receive_packet(pkt)

        if "websocket" in self._upgrades and "websocket" in self._transports:
            # attempt to upgrade to websocket
            if await self._connect_websocket(nursery, url, headers, engineio_path):
                # upgrade to websocket succeeded, we're done here
                return True

        # No websocket upgrade attempt or upgrade failed: start polling background tasks.
        self._ping_task_scope = await nursery.start(self._ping_loop)
        self._write_task_scope = await nursery.start(self._write_loop)
        self._read_task_scope = await nursery.start(self._read_loop_polling)
        return True

    async def _connect_websocket(
        self,
        nursery: trio.Nursery,
        url: httpcore.URL,
        headers: Headers,
        engineio_path: bytes,
    ) -> bool:
        """Establish or upgrade to a WebSocket connection with the server.

        Args:
            nursery: a `trio.Nursery` object in which to run the Engine.IO background
                tasks.
            url: The URL to connect to.
            headers: A sequence of custom headers keys-values to send with the
                connection request. Empty list if no custome headers.
            engineio_path: The endpoint to connect to.

        Raises:
            EngineIoConnectionError: Raised if the connection fails.

        Returns:
            `True` if the connection or upgrade succeeds, `False` if upgrade fails.
        """
        websocket_url: NoCachingURL = self._get_engineio_url(
            url, engineio_path, "websocket"
        )
        if self._sid:
            self._logger.info(
                f"Connect: Attempting WebSocket upgrade to {websocket_url}"
            )
            upgrade = True
            websocket_url.add_to_target(b"&sid=" + enforce_bytes(self._sid, name="sid"))
        else:
            upgrade = False
            self._base_url = websocket_url
            self._logger.info(
                f"Connect: Attempting WebSocket connection to {websocket_url}"
            )

        if websocket_url.scheme == b"wss":
            ssl_context = default_ssl_context(verify=self._ssl_verify)
        else:
            ssl_context = False

        try:
            with trio.fail_after(self._timeouts["connect"]):
                ws = await trio_ws.connect_websocket(
                    nursery,
                    host=websocket_url.host.decode("ascii"),
                    port=websocket_url.port,
                    resource=f"{websocket_url.target.decode('ascii')}",
                    extra_headers=headers,
                    use_ssl=ssl_context,
                )
        except (trio.TooSlowError, trio_ws.HandshakeError, trio_ws.ConnectionRejected):
            if upgrade:
                self._logger.warning(
                    "Connect: WebSocket upgrade failed: connection error"
                )
                return False
            else:
                raise EngineIoConnectionError("Websocket connection error")

        if upgrade:
            p = packet.Packet(packet.PING, data="probe").encode(always_bytes=False)
            try:
                await ws.send_message(p)
            except trio_ws.ConnectionClosed as e:
                self._logger.warning(
                    f"Connect: WebSocket upgrade failed: unexpected send exception: {e}"
                )
                return False

            try:
                p = await ws.get_message()
            except trio_ws.ConnectionClosed as e:
                self._logger.warning(
                    f"Connect: WebSocket upgrade failed: unexpected recv exception: {e}"
                )
                return False
            pkt = packet.Packet(encoded_packet=p)
            if pkt.packet_type != packet.PONG or pkt.data != "probe":
                self._logger.warning(
                    "Connect: WebSocket upgrade failed: no PONG packet"
                )
                return False

            p = packet.Packet(packet.UPGRADE).encode(always_bytes=False)
            try:
                await ws.send_message(p)
            except trio_ws.ConnectionClosed as e:
                self._logger.warning(
                    f"Connect: WebSocket upgrade failed: unexpected send exception: {e}"
                )
                return False

            self._current_transport = "websocket"
            self._logger.info("Connect: WebSocket upgrade was successful")

        else:
            try:
                p = await ws.get_message()
            except trio_ws.ConnectionClosed as e:
                raise EngineIoConnectionError(f"Unexpected recv exception: {e}")

            open_packet = packet.Packet(encoded_packet=p)
            if open_packet.packet_type != packet.OPEN:
                self._ws = ws  # Required by _reset
                await self._reset()
                raise EngineIoConnectionError("no OPEN packet")

            self._logger.info(
                f"Connect: WebSocket connection accepted with {open_packet.data}"
            )
            self._sid = open_packet.data["sid"]
            self._upgrades = open_packet.data["upgrades"]
            self._ping_interval = int(open_packet.data["pingInterval"]) / 1000.0
            self._ping_timeout = int(open_packet.data["pingTimeout"]) / 1000.0
            self._current_transport = "websocket"

            self.state = "connected"
            connected_clients.append(self)

            await self._trigger_event("connect", run_async=False)

        self._ws = ws
        self._ping_task_scope = await nursery.start(self._ping_loop)
        self._write_task_scope = await nursery.start(self._write_loop)
        self._read_task_scope = await nursery.start(self._read_loop_websocket)
        return True

    async def _receive_packet(self, pkt: packet.Packet) -> None:
        """Handle incoming packets from the server.

        Args:
            pkt: the received `packet.Packet` object to handle.
        """
        packet_name = packet.packet_names.get(pkt.packet_type, "UNKNOWN")
        self._logger.info(
            f"Received packet {packet_name}, data: "
            f"{pkt.data if not isinstance(pkt.data, bytes) else '<binary>'}"
        )

        if pkt.packet_type == packet.MESSAGE:
            await self._trigger_event("message", pkt.data, run_async=True)

        elif pkt.packet_type == packet.PONG:
            self._pong_received = True

        elif pkt.packet_type == packet.CLOSE:
            await self.disconnect()

        elif pkt.packet_type == packet.NOOP:
            pass

        else:
            self._logger.warning(
                f"Received unexpected packet of type {pkt.packet_type}"
            )

    async def _send_packet(self, pkt: packet.Packet) -> None:
        """Queue a packet to be sent to the server.

        Do nothing if the EngineIoClient object is not connected.

        Args:
            pkt: the `packet.Packet` object to queue.
        """
        if self.state != "connected":
            return
        self._logger.info(
            f"Sending packet {packet.packet_names[pkt.packet_type]}, data: "
            f"{pkt.data if not isinstance(pkt.data, bytes) else '<binary>'}"
        )
        await self._send_channel.send(pkt)

    async def _send_request(
        self,
        method: bytes | str,
        url: httpcore.URL | bytes | str,
        headers: HeadersAsMapping | HeadersAsSequence | None = None,
        body: bytes | AsyncIterator[bytes] | None = None,
        timeouts: Timeouts | None = None,
    ) -> Optional[httpcore.Response]:
        """Sends an HTTP 1.1 request and returns the server response.

        Creates an `httpcore.AsyncConnectionPool` object on the first request to handle
        the HTTP connection.

        Args:
            method: the HTTP request method, either "GET" or "POST".
            url: The request URL, either as a `httpcore.URL` object, or as `str`
                or `bytes`.
            headers: The HTTP request headers (optional).
            body: The content of the request body (optional).
            timeouts: A dictionary of timeouts extra information to include in
                the request (optional).

        Returns:
            The server response if the request succeeds, `None` otherwise.
        """
        if self._http is None:
            self._http = httpcore.AsyncConnectionPool(
                network_backend=trio_backend.TrioBackend()
            )

        extensions = {} if timeouts is None else {"timeout": timeouts}

        try:
            return await self._http.request(
                method=method,
                url=url,
                headers=headers,
                content=body,
                extensions=extensions,
            )

        except (
            httpcore.UnsupportedProtocol,
            httpcore.ProtocolError,
            httpcore.NetworkError,
            httpcore.TimeoutException,
        ):
            self._logger.exception(f"HTTP {method} request to {url} failed with error.")
            return None

    async def _trigger_event(
        self, event: EventName, *args, run_async: bool = False
    ) -> Any:
        """Invoke an event handler.

        The event handler may be a coroutine or a synchronous function.
        A synchronous event handler may be run asynchronously if required.
        Any unregistered event is silently ignored.

        Args:
            event: The name of the event to trigger.
            *args: Positional arguments to pass to the event handler.
            run_async: If `True`, any synchronous event handler is run as an
                asynchronous task in its own `trio.Nursery` instance.
                If `False` (the default), a synchronous call to the event handler is
                done.
                Do not apply to coroutine event handler.

        Returns:
            The object returned by the event handler.
        """
        self._logger.debug(f"Triggering event: {event}")

        if event in self._handlers:
            if inspect.iscoroutinefunction(self._handlers[event]) is True:
                try:
                    return await self._handlers[event](*args)
                except Exception:
                    self._logger.exception(f"{event} async handler error")
            else:
                if run_async:

                    async def async_handler():
                        return self._handlers[event](*args)

                    try:
                        async with trio.open_nursery() as nursery:
                            t = ResultCapture.start_soon(nursery, async_handler)
                    except BaseException:
                        pass
                    try:
                        return t.result
                    except TaskWrappedException:
                        self._logger.exception(f"{event} handler error")
                else:
                    try:
                        return self._handlers[event](*args)
                    except Exception:
                        self._logger.exception(f"{event} handler error")

    def _get_engineio_url(
        self, url: httpcore.URL, engineio_path: bytes, transport: Transport
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

        if query:
            query += b"&transport=" + transport.encode("ascii") + b"&EIO=3"
        else:
            query = b"transport=" + transport.encode("ascii") + b"&EIO=3"

        target = path + b"?" + query

        return NoCachingURL(scheme=scheme, host=url.host, port=url.port, target=target)

    async def _ping_loop(self, task_status=trio.TASK_STATUS_IGNORED) -> None:
        """This background task sends a PING to the server at the requested interval.

        To simplify the timeout handling, use the maximum of the ping interval and
        ping timeout as interval: a PONG should be received between in the interval.

        When started by `await trio.Nursery.start`, the task returns its own
        `trio.CancelScope` after its initialization, allowing being cancelled by other
        tasks.
        """
        with trio.CancelScope() as scope:
            self._pong_received = True
            ping_interval = max(self._ping_interval, self._ping_timeout)
            task_status.started(scope)

            while self.state == "connected":
                if not self._pong_received:
                    self._logger.info(
                        "Ping loop: PONG response has not been received, aborting"
                    )
                    # No pong response from server but try to politely close the
                    # websocket connection if any.
                    if self._ws:
                        await self._ws.aclose()
                    break

                self._pong_received = False
                await self._send_packet(packet.Packet(packet.PING))
                await trio.sleep(ping_interval)

        # When exiting the ping loop, the connection is no more usable and other
        # background tasks can be cancelled, avoiding waiting for any timeouts.
        # Note: a short delay is set to let the tasks ending any ongoing IO.
        self._logger.debug("Ping loop: Waiting before cancelling background tasks")
        await trio.sleep(0.1)
        self._logger.debug("Ping loop: Canceling write loop task")
        self._write_task_scope.cancel()
        self._logger.debug("Ping loop: Canceling read loop task")
        self._read_task_scope.cancel()

        self._logger.info("Ping loop: Exiting ping task")

    async def _read_loop_polling(self, task_status=trio.TASK_STATUS_IGNORED) -> None:
        """Read and handle packets by polling the Engine.IO server.

        As a PING is sent every "dt = max(ping interval, ping timeout)", a PONG
        response is due by the server during the same "dt" interval. An extra 5s grace
        period is added for safety to the polling request timeout.

        When started by `await trio.Nursery.start`, the task returns its own
        `trio.CancelScope` after its initialization, allowing being cancelled by other
        tasks.
        """
        with trio.CancelScope() as scope:
            t_out = max(self._ping_interval, self._ping_timeout) + 5
            timeout: Timeouts = {
                "connect": t_out,
                "read": t_out,
                "write": t_out,
                "pool": t_out,
            }
            task_status.started(scope)

            while self.state == "connected":
                # Wait for incoming packets by a long-polling GET request
                self._logger.info(
                    f"Polling read loop: Sending polling GET request to {self._base_url}"
                )
                r = await self._send_request("GET", self._base_url, timeouts=timeout)
                if r is None:
                    self._logger.warning(
                        "Polling read loop: Connection refused by the server, aborting"
                    )
                    break
                if r.status < 200 or r.status >= 300:
                    self._logger.warning(
                        f"Polling read loop: Unexpected status code {r.status} in server "
                        "response, aborting"
                    )
                    break

                # Decode the received message as valid payload of packets
                try:
                    p = payload.Payload(encoded_payload=await r.aread())
                except ValueError:
                    self._logger.warning(
                        "Polling read loop: Unexpected packet from server, aborting"
                    )
                    break

                # Handle the received packets
                for pkt in p.packets:
                    await self._receive_packet(pkt)

        # When exiting the read loop, the connection is no more usable and other
        # background tasks can be cancelled, avoiding waiting for any timeouts.
        # Note: a short delay is set to let the tasks ending any ongoing IO.
        self._logger.info(
            "Polling read loop: Waiting before cancelling background tasks"
        )
        await trio.sleep(0.1)
        self._logger.info("Polling read loop: Canceling write loop task")
        self._write_task_scope.cancel()
        self._logger.info("Polling read loop: Cancelling ping loop task")
        self._ping_task_scope.cancel()

        if self.state == "connected":
            # Disconnection is not due to the user calling `EngineIoClient.disconnect()`:
            # trigger the "disconnect" event, remove the client from the
            # connected_clients list and reset the connection.
            await self._trigger_event("disconnect", run_async=False)
            try:
                connected_clients.remove(self)
            except ValueError:  # pragma: no cover
                pass
            await self._reset()
        else:
            # Disconnection is due to the user: clean up the residual connections
            await self._reset()

        self._logger.info("Polling read loop: Exiting read loop task")

    async def _read_loop_websocket(self, task_status=trio.TASK_STATUS_IGNORED):
        """Read and handle packets from the Engine.IO WebSocket connection.

        As a PING is sent every "dt = max(ping interval, ping timeout)", a PONG
        response is due by the server during the same "dt" interval. An extra 5s grace
        period is added for safety to the polling request timeout.

        When started by `await trio.Nursery.start`, the task returns its own
        `trio.CancelScope` after its initialization, allowing being cancelled by other
        tasks.
        """
        with trio.CancelScope() as scope:
            timeout = max(self._ping_interval, self._ping_timeout) + 5
            task_status.started(scope)

            while self.state == "connected":
                # Wait for an incoming packet
                self._logger.info(f"Websocket read loop: Wait for an incoming packet")
                try:
                    with trio.fail_after(timeout):
                        p = await self._ws.get_message()
                        if p is None:
                            self._logger.warning(
                                f"Websocket read loop: WebSocket read returned None, aborting"
                            )
                            break
                except trio_ws.ConnectionClosed:
                    self._logger.warning(
                        "Websocket read loop: WebSocket connection was closed, aborting"
                    )
                    break
                except trio.TooSlowError:
                    self._logger.warning(
                        "Websocket read loop: WebSocket connection timeout, aborting"
                    )
                    break
                except Exception as e:
                    self._logger.warning(
                        f"Websocket read loop: Unexpected error receiving packet: {e},"
                        f" aborting"
                    )
                    break

                # Decode the received message as valid packet
                if isinstance(p, str):
                    p = p.encode("utf-8")
                try:
                    pkt = packet.Packet(encoded_packet=p)
                except Exception as e:  # pragma: no cover
                    self._logger.info(
                        f"Websocket read loop: Unexpected error decoding packet: {e}, aborting"
                    )
                    break

                # Handle the received packet
                await self._receive_packet(pkt)

        # When exiting the read loop, the connection is no more usable and other
        # background tasks can be cancelled, avoiding waiting for any timeouts.
        # Note: a short delay is set to let the tasks ending any ongoing IO.
        self._logger.info(
            "Websocket read loop: Waiting before cancelling background tasks"
        )
        await trio.sleep(0.1)
        self._logger.info("Websocket read loop: Cancelling write loop task")
        self._write_task_scope.cancel()
        self._logger.info("Websocket read loop: Cancelling ping loop task")
        self._ping_task_scope.cancel()

        if self.state == "connected":
            # Disconnection is not due to the user calling `EngineIoClient.disconnect()`:
            # trigger the "disconnect" event, remove the client from the
            # connected_clients list and reset the connection.
            await self._trigger_event("disconnect", run_async=False)
            try:
                connected_clients.remove(self)
            except ValueError:  # pragma: no cover
                pass
            await self._reset()
        else:
            # Disconnection is due to the user: clean up the residual connections
            await self._reset()

        self._logger.info("Websocket read loop: Exiting read loop task")

    async def _write_loop(self, task_status=trio.TASK_STATUS_IGNORED):
        """Send packets to the server as they are pushed to the send queue.

        As a PING is enqueued every "dt = max(ping interval, ping timeout)", the same
        "dt" interval is used here, with an extra 5s grace period.

        When started by `await trio.Nursery.start`, the task returns its own
        `trio.CancelScope` after its initialization, allowing being cancelled by other
        tasks.
        """
        with trio.CancelScope() as scope:
            timeout = max(self._ping_interval, self._ping_timeout) + 5
            transport = self._current_transport
            task_status.started(scope)

            while self.state == "connected":

                # Wait for packets to write
                with trio.move_on_after(timeout) as cancel_scope:
                    try:
                        self._logger.debug(f"Write loop: Wait for packet to write")
                        pkt = await self._receive_channel.receive()
                        self._logger.debug(f"Write loop: Get a packet to write: {pkt}")
                    except (
                        trio.EndOfChannel,
                        trio.BrokenResourceError,
                        trio.ClosedResourceError,
                    ):
                        self._logger.error(
                            "Write loop: packet queue is closed, aborting"
                        )
                        break
                if cancel_scope.cancelled_caught:
                    self._logger.error("Write loop: packet queue is empty, aborting")
                    break

                # Is it useful?
                if pkt is None:
                    packets = []
                else:
                    packets = [pkt]
                    # Try to get all other enqueued packets if any
                    while True:
                        try:
                            pkt = self._receive_channel.receive_nowait()
                            self._logger.debug(
                                f"Write loop: Get other packet to write: {pkt}"
                            )
                        except (trio.WouldBlock, trio.EndOfChannel):
                            self._logger.debug(
                                f"Write loop: No more packet available, continue"
                            )
                            break
                        except (
                            trio.BrokenResourceError,
                            trio.ClosedResourceError,
                        ):  # pragma: no cover
                            self._logger.error(
                                "Write loop: packet queue is closed, aborting"
                            )
                            break
                        else:
                            if pkt is not None:  # pragma: no branch
                                packets.append(pkt)

                if not packets:
                    # empty packet list returned -> connection closed
                    self._logger.error("Write loop: No packet to send, aborting")
                    break

                # Send the packets using the correct transport method
                if transport == "polling":
                    # Build a payload to send all packets in one POST request
                    p = payload.Payload(packets=packets)
                    self._logger.info(
                        f"Write loop: Sending POST request to {self._base_url}"
                    )
                    r = await self._send_request(
                        "POST",
                        self._base_url,
                        headers={"Content-Type": "application/octet-stream"},
                        body=p.encode(),
                        timeouts=self._timeouts,
                    )
                    if r is None:
                        self._logger.warning(
                            "Write loop: Connection refused by the server, aborting"
                        )
                        break
                    if r.status < 200 or r.status >= 300:
                        self._logger.warning(
                            f"Write loop: Unexpected status code {r.status} in server response, "
                            f"aborting"
                        )
                        break
                    self._logger.debug("Write loop: Packet(s) written")
                else:
                    # websocket. Packets are sent individually as websocket messages.
                    try:
                        for pkt in packets:
                            await self._ws.send_message(pkt.encode(always_bytes=False))
                    except trio_ws.ConnectionClosed:
                        self._logger.info(
                            "Write loop: WebSocket connection was closed, aborting"
                        )
                        break
                    self._logger.debug("Write loop: Packet(s) written")

        self._logger.info("Write loop: Exiting write loop task")
