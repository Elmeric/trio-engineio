import inspect
import logging
import ssl
import time
import urllib.parse
from typing import Optional

import trio
import httpcore
import httpcore.backends.trio as trio_backend
import trio_websocket as trio_ws

try:
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None

from .trio_util import ResultCapture
from .exceptions import EngineIoConnectionError
from . import packet
from . import payload

default_logger = logging.getLogger('engineio.client')
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


class TrioClient:
    """An Engine.IO client for trio.

    This class implements a fully compliant Engine.IO web client with support
    for websocket and long-polling transports, compatible with the trio
    framework on Python 3.7 or newer.

    :param logger: To enable logging set to ``True`` or pass a logger object to
                   use. To disable logging set to ``False``. The default is
                   ``False``. Note that fatal errors are logged even when
                   ``logger`` is ``False``.
    :param json: An alternative json module to use for encoding and decoding
                 packets. Custom json modules must have ``dumps`` and ``loads``
                 functions that are compatible with the standard library
                 versions.
    :param request_timeout: A timeout in seconds for requests. The default is
                            5 seconds.
    :param http_session: an initialized ``aiohttp.ClientSession`` object to be
                         used when sending requests to the server. Use it if
                         you need to add special client options such as proxy
                         servers, SSL certificates, etc.
    :param ssl_verify: ``True`` to verify SSL certificates, or ``False`` to
                       skip SSL certificate verification, allowing
                       connections to servers with self signed certificates.
                       The default is ``True``.
    """
    event_names = ["connect", "disconnect", "message"]

    def __init__(self,
                 # nursery,
                 logger=False,
                 json=None,
                 request_timeout=5,
                 http_session=None,
                 ssl_verify=True):
        # global original_signal_handler
        # if original_signal_handler is None and \
        #         threading.current_thread() == threading.main_thread():
        #     original_signal_handler = signal.signal(signal.SIGINT,
        #                                             signal_handler)
        self.handlers = {}
        self.base_url = None
        self.transports = None
        self.current_transport = None
        self.sid = None
        self.upgrades = None
        self.ping_interval = None
        self.ping_timeout = None
        self.pong_received = True
        self.http: Optional[httpcore.AsyncConnectionPool] = http_session
        self.ws: Optional[trio_ws.WebSocketConnection] = None
        # self.ping_loop_event = None
        self.send_channel: Optional[trio.MemorySendChannel] = None
        self.receive_channel: Optional[trio.MemoryReceiveChannel] = None
        self.state = "disconnected"
        self.ssl_verify = ssl_verify
        self._ping_task_scope = None
        self._write_task_scope = None
        self._read_task_scope = None

        if json is not None:
            packet.Packet.json = json

        if not isinstance(logger, bool):
            self.logger = logger
        else:
            self.logger = default_logger
            if not logging.root.handlers and \
                    self.logger.level == logging.NOTSET:
                if logger:
                    self.logger.setLevel(logging.INFO)
                else:
                    self.logger.setLevel(logging.DEBUG)
                self.logger.addHandler(logging.StreamHandler())

        self.request_timeout = {
            "connect": request_timeout,
            "read": request_timeout,
            "write": request_timeout,
            "pool": request_timeout,
        }

    # def is_asyncio_based(self):
    #     return True

    def on(self, event, handler=None):
        """Register an event handler.

        :param event: The event name. Can be ``'connect'``, ``'message'`` or
                      ``'disconnect'``.
        :param handler: The function that should be invoked to handle the
                        event. When this parameter is not given, the method
                        acts as a decorator for the handler function.

        Example usage::

            # as a decorator:
            @eio.on('connect')
            def connect_handler():
                print('Connection request')

            # as a method:
            def message_handler(msg):
                print('Received message: ', msg)
                eio.send('response')
            eio.on('message', message_handler)
        """
        if event not in self.event_names:
            raise ValueError("Invalid event")

        def set_handler(h):
            self.handlers[event] = h
            return h

        if handler is None:
            return set_handler
        set_handler(handler)

    async def connect(self, nursery, url, headers=None, transports=None,
                      engineio_path='engine.io'):
        """Connect to an Engine.IO server.

        :param url: The URL of the Engine.IO server. It can include custom
                    query string parameters if required by the server.
        :param headers: A dictionary with custom headers to send with the
                        connection request.
        :param transports: The list of allowed transports. Valid transports
                           are ``'polling'`` and ``'websocket'``. If not
                           given, the polling transport is connected first,
                           then an upgrade to websocket is attempted.
        :param engineio_path: The endpoint where the Engine.IO server is
                              installed. The default value is appropriate for
                              most cases.

        Note: this method is a coroutine.

        Example usage::

            eio = engineio.Client()
            await eio.connect('http://localhost:5000')
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
        #         self.logger.warning('Signal handler is unsupported')

        if self.state != "disconnected":
            raise ValueError("Client is not in a disconnected state")

        valid_transports = ["polling", "websocket"]
        if transports is not None:
            if isinstance(transports, str):
                transports = [transports]
            transports = [transport for transport in transports
                          if transport in valid_transports]
            if not transports:
                raise ValueError("No valid transports provided")
        self.transports = transports or valid_transports

        self.send_channel, self.receive_channel = trio.open_memory_channel(10)

        return await getattr(self, f"_connect_{self.transports[0]}")(
            nursery, url, headers or {}, engineio_path
        )

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

    async def send(self, data, binary=None):
        """Send a message to a client.

        :param data: The data to send to the client. Data can be of type
                     ``str``, ``bytes``, ``list`` or ``dict``. If a ``list``
                     or ``dict``, the data will be serialized as JSON.
        :param binary: ``True`` to send packet as binary, ``False`` to send
                       as text. If not given, unicode (Python 2) and str
                       (Python 3) are sent as text, and str (Python 2) and
                       bytes (Python 3) are sent as binary.

        Note: this method is a coroutine.
        """
        await self._send_packet(packet.Packet(packet.MESSAGE, data=data, binary=binary))

    async def disconnect(self):
        """Disconnect from the server.
        """
        if self.state == "connected":
            await self._send_packet(packet.Packet(packet.CLOSE))
            # self._write_task_scope.cancel()
            # await self.send_channel.send(None)
            self.state = "disconnecting"
            self.logger.info("User disconnection: Cancelling ping loop task")
            self._ping_task_scope.cancel()
            # self.logger.debug(f"Deadlines: P-{self._ping_task_scope.deadline}, R-{self._read_task_scope.deadline}, W-{self._write_task_scope.deadline}")
            # self._ping_task_scope.deadline = 0.5
            # self._read_task_scope.deadline = 0.5
            # self._write_task_scope.deadline = 0.5
            # self.logger.debug(f"Deadlines: P-{self._ping_task_scope.deadline}, R-{self._read_task_scope.deadline}, W-{self._write_task_scope.deadline}")
            # await trio.sleep(1)

            await self._trigger_event("disconnect", run_async=False)

            # if self.current_transport == "websocket":
            #     with trio.fail_after(self.request_timeout["connect"]):
            #         await self.ws.aclose()
            #         print(f"!!!!! Websocket closed: {self.ws.closed}")

            self.state = "disconnected"
            # await trio.sleep(0)
            try:
                connected_clients.remove(self)
            except ValueError:  # pragma: no cover
                pass

        self.sid = None
        # await self._reset()

    def transport(self):
        """Return the name of the transport currently in use.

        The possible values returned by this function are 'polling' and 'websocket'.
        """
        return self.current_transport

    async def sleep(self, seconds=0):
        """Sleep for the requested amount of time.
        """
        return await trio.sleep(seconds)

    async def _reset(self):
        if self.http:
            # wait_for_response = False
            # for connection in self.http.connections:
            #     if not (connection.is_idle() or connection.is_closed()):
            #         wait_for_response = True
            #         break
            # if wait_for_response:
            #     await trio.sleep(0.01)
            # await trio.sleep(1)
            self.logger.debug(self.http.connections)
            try:
                self.logger.info("Reset: Closing HTTP connections pool")
                await self.http.aclose()
            except RuntimeError as e:
                self.logger.warning(f"Reset: Error while closing the HTTP connection pool {e}")
            self.logger.info("Reset: HTTP connections pool closed")

        if self.ws and self.ws.closed:
            self.logger.info(f"Reset: Websocket already closed: {self.ws.closed}")
        if self.ws and not self.ws.closed:
            self.logger.info("Reset: Closing websocket")
            with trio.fail_after(self.request_timeout["connect"]):
                await self.ws.aclose()
            self.logger.info(f"Reset: Websocket closed: {self.ws.closed}")

        self.state = "disconnected"
        self.sid = None

    async def _connect_polling(self, nursery, url, headers, engineio_path):
        """Establish a long-polling connection to the Engine.IO server."""

        self.base_url = self._get_engineio_url(url, engineio_path, 'polling')
        self.logger.info(f"Attempting polling connection to {self.base_url}")

        r = await self._send_request(
            'GET', self.base_url + f"&t={time.time()}", headers=headers,
            timeout=self.request_timeout)

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

        self.logger.info(f"Polling connection accepted with {str(open_packet.data)}")
        self.sid = open_packet.data['sid']
        self.upgrades = open_packet.data['upgrades']
        self.ping_interval = int(open_packet.data['pingInterval']) / 1000.0
        self.ping_timeout = int(open_packet.data['pingTimeout']) / 1000.0
        self.current_transport = 'polling'
        self.base_url += '&sid=' + self.sid

        self.state = 'connected'
        connected_clients.append(self)
        await self._trigger_event('connect', run_async=False)

        for pkt in p.packets[1:]:
            await self._receive_packet(pkt)

        if "websocket" in self.upgrades and "websocket" in self.transports:
            # attempt to upgrade to websocket
            if await self._connect_websocket(nursery, url, headers, engineio_path):
                # upgrade to websocket succeeded, we're done here
                return

        self._ping_task_scope = await nursery.start(self._ping_loop)
        # nursery.start_soon(self._ping_loop)
        self._write_task_scope = await nursery.start(self._write_loop)
        # nursery.start_soon(self._write_loop)
        self._read_task_scope = await nursery.start(self._read_loop_polling)
        # nursery.start_soon(self._read_loop_polling)

    async def _connect_websocket(self, nursery, url, headers, engineio_path):
        """Establish or upgrade to a WebSocket connection with the server."""
        websocket_url = self._get_engineio_url(url, engineio_path, "websocket")
        if self.sid:
            self.logger.info(f"Attempting WebSocket upgrade to {websocket_url}")
            upgrade = True
            websocket_url = f"{websocket_url}&sid={self.sid}"
        else:
            upgrade = False
            self.base_url = websocket_url
            self.logger.info(f"Attempting WebSocket connection to {websocket_url}")

        if not self.ssl_verify:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        else:
            ssl_context = None
        try:
            with trio.fail_after(self.request_timeout["connect"]):
                ws = await trio_ws.connect_websocket_url(
                    nursery,
                    url=f"{websocket_url}&t={time.time()}",
                    extra_headers=headers,
                    ssl_context=ssl_context
                )
        except (trio.TooSlowError, trio_ws.HandshakeError, trio_ws.ConnectionRejected):
            if upgrade:
                self.logger.warning("WebSocket upgrade failed: connection error")
                return False
            else:
                raise EngineIoConnectionError("Websocket connection error")

        if upgrade:
            p = packet.Packet(packet.PING, data="probe").encode(always_bytes=False)
            try:
                await ws.send_message(p)
            except trio_ws.ConnectionClosed as e:
                self.logger.warning(
                    f"WebSocket upgrade failed: unexpected send exception: {e}"
                )
                return False

            try:
                p = await ws.get_message()
            except trio_ws.ConnectionClosed as e:
                self.logger.warning(
                    f"WebSocket upgrade failed: unexpected recv exception: {e}")
                return False
            pkt = packet.Packet(encoded_packet=p)
            if pkt.packet_type != packet.PONG or pkt.data != "probe":
                self.logger.warning("WebSocket upgrade failed: no PONG packet")
                return False

            p = packet.Packet(packet.UPGRADE).encode(always_bytes=False)
            try:
                await ws.send_message(p)
            except trio_ws.ConnectionClosed as e:
                self.logger.warning(
                    f"WebSocket upgrade failed: unexpected send exception: {e}")
                return False

            self.current_transport = "websocket"
            self.logger.info("WebSocket upgrade was successful")

        else:
            try:
                p = await ws.get_message()
            except trio_ws.ConnectionClosed as e:
                raise EngineIoConnectionError(f"Unexpected recv exception: {e}")

            open_packet = packet.Packet(encoded_packet=p)
            # open_packet.packet_type = 3
            if open_packet.packet_type != packet.OPEN:
                self.ws = ws
                await self._reset()
                raise EngineIoConnectionError("no OPEN packet")

            self.logger.info(f"WebSocket connection accepted with {open_packet.data}")
            self.sid = open_packet.data["sid"]
            self.upgrades = open_packet.data["upgrades"]
            self.ping_interval = int(open_packet.data["pingInterval"]) / 1000.0
            self.ping_timeout = int(open_packet.data["pingTimeout"]) / 1000.0
            self.current_transport = "websocket"

            self.state = "connected"
            connected_clients.append(self)
            await self._trigger_event("connect", run_async=False)

        self.ws = ws
        self._ping_task_scope = await nursery.start(self._ping_loop)
        # nursery.start_soon(self._ping_loop)
        self._write_task_scope = await nursery.start(self._write_loop)
        # nursery.start_soon(self._write_loop)
        self._read_task_scope = await nursery.start(self._read_loop_websocket)
        # nursery.start_soon(self._read_loop_websocket)
        return True

    async def _receive_packet(self, pkt):
        """Handle incoming packets from the server."""
        if pkt.packet_type < len(packet.packet_names):
            packet_name = packet.packet_names[pkt.packet_type]
        else:
            packet_name = "UNKNOWN"
        self.logger.info(
            f"Received packet {packet_name} data "
            f"{pkt.data if not isinstance(pkt.data, bytes) else '<binary>'}"
        )
        if pkt.packet_type == packet.MESSAGE:
            # await self._trigger_event("message", pkt.data, run_async=False)
            await self._trigger_event("message", pkt.data, run_async=True)
        elif pkt.packet_type == packet.PONG:
            self.pong_received = True
        elif pkt.packet_type == packet.CLOSE:
            await self.disconnect()
        elif pkt.packet_type == packet.NOOP:
            pass
        else:
            self.logger.error(f"Received unexpected packet of type {pkt.packet_type}")

    async def _send_packet(self, pkt):
        """Queue a packet to be sent to the server."""
        if self.state != "connected":
            return
        await self.send_channel.send(pkt)
        self.logger.info(
            f"Sending packet {packet.packet_names[pkt.packet_type]} data "
            f"{pkt.data if not isinstance(pkt.data, bytes) else '<binary>'}")

    async def _send_request(
            self, method, url, headers=None, body=None, timeout=None
    ) -> Optional[httpcore.Response]:
        if self.http is None:
            self.http = httpcore.AsyncConnectionPool(
                network_backend=trio_backend.TrioBackend()
            )

        extensions = {} if timeout is None else {"timeout": timeout}

        try:
            return await self.http.request(
                method=method,
                url=url,
                headers=headers,
                content=body,
                extensions=extensions,
            )

        except (httpcore.ProtocolError, httpcore.NetworkError, httpcore.TimeoutException) as exc:
            self.logger.info(f"HTTP {method} request to {url} failed with error {exc}.")
            return None

    async def _trigger_event(self, event, *args, **kwargs):
        """Invoke an event handler."""
        self.logger.debug(f"Triggering event: {event}")
        run_async = kwargs.pop("run_async", False)
        if event in self.handlers:
            if inspect.iscoroutinefunction(self.handlers[event]) is True:
                if run_async:
                    async with trio.open_nursery() as nursery:
                        r = ResultCapture.start_soon(nursery, self.handlers[event], *args)
                    return r.result
                else:
                    try:
                        return await self.handlers[event](*args)
                    except Exception:
                        self.logger.exception(f"{event} async handler error")
                        if event == 'connect':
                            # if connect handler raised error we reject the connection
                            return False
            else:
                if run_async:
                    async def async_handler():
                        return self.handlers[event](*args)

                    async with trio.open_nursery() as nursery:
                        r = ResultCapture.start_soon(nursery, async_handler)
                    return r.result
                else:
                    try:
                        return self.handlers[event](*args)
                    except Exception:
                        self.logger.exception(f"{event} handler error")
                        if event == 'connect':
                            # if connect handler raised error we reject the connection
                            return False

    def _get_engineio_url(self, url, engineio_path, transport):
        """Generate the Engine.IO connection URL."""
        engineio_path = engineio_path.strip('/')
        parsed_url = urllib.parse.urlparse(url)

        if transport == 'polling':
            scheme = 'http'
        elif transport == 'websocket':
            scheme = 'ws'
        else:  # pragma: no cover
            raise ValueError('invalid transport')
        if parsed_url.scheme in ['https', 'wss']:
            scheme += 's'

        return (
            f"{scheme}://{parsed_url.netloc}/{engineio_path}/?{parsed_url.query}"
            f"{'&' if parsed_url.query else ''}transport={transport}&EIO=3"
        )

    async def _ping_loop(self, task_status=trio.TASK_STATUS_IGNORED):
        """This background task sends a PING to the server at the requested
        interval.
        """
        with trio.CancelScope() as scope:
            self.pong_received = True
            # if self.ping_loop_event is None:
            #     self.ping_loop_event = trio.Event()
            task_status.started(scope)

            while self.state == "connected":
                if not self.pong_received:
                    self.logger.info("Ping loop: PONG response has not been received, aborting")
                    if self.ws:
                        await self.ws.aclose()
                    self.logger.info("Ping loop: Canceling write loop task")
                    self._write_task_scope.cancel()
                    # self.logger.info("Ping loop: Canceling read loop task")
                    # self._read_task_scope.cancel()
                    # await self.send_channel.send(None)
                    break

                self.pong_received = False
                await self._send_packet(packet.Packet(packet.PING))
                await trio.sleep(self.ping_interval)
                # with trio.move_on_after(self.ping_interval):
                #     await self.ping_loop_event.wait()

        # self.logger.info("Ping loop: Canceling write loop task")
        # self._write_task_scope.cancel()
        self.logger.info("Ping loop: Waiting before cancelling read loop task")
        await trio.sleep(0.05)
        await trio.sleep(0.05)
        self.logger.info("Ping loop: Canceling read loop task")
        self._read_task_scope.cancel()
        self.logger.info("Ping loop: Exiting ping task")

    async def _read_loop_polling(self, task_status=trio.TASK_STATUS_IGNORED):
        """Read packets by polling the Engine.IO server."""
        with trio.CancelScope() as scope:
            t_out = max(self.ping_interval, self.ping_timeout) + 5
            timeout = {
                "connect": t_out,
                "read": t_out,
                "write": t_out,
                "pool": t_out,
            }
            task_status.started(scope)
            while self.state == "connected":
                self.logger.info(
                    f"Polling read loop: Sending polling GET request to {self.base_url}")
                r = await self._send_request(
                    "GET", f"{self.base_url}&t={time.time()}",
                    timeout=timeout
                    )
                if r is None:
                    self.logger.warning("Polling read loop: Connection refused by the server, aborting")
                    # self._write_task_scope.cancel()
                    # await self.send_channel.send(None)
                    break
                if r.status < 200 or r.status >= 300:
                    self.logger.warning(f"Polling read loop: Unexpected status code {r.status} in server "
                                        "response, aborting")
                    # self._write_task_scope.cancel()
                    # await self.send_channel.send(None)
                    break
                try:
                    p = payload.Payload(encoded_payload=await r.aread())
                except ValueError:
                    self.logger.warning("Polling read loop: Unexpected packet from server, aborting")
                    # self._write_task_scope.cancel()
                    # await self.send_channel.send(None)
                    break
                for pkt in p.packets:
                    await self._receive_packet(pkt)

        self.logger.info("Polling read loop: Waiting before cancelling tasks")
        await trio.sleep(0.05)
        self.logger.info("Polling read loop: Canceling write loop task")
        # await self.write_loop_task
        self._write_task_scope.cancel()
        # await self.send_channel.send(None)
        self.logger.info('Polling read loop: Cancelling ping loop task')
        self._ping_task_scope.cancel()
        # self.logger.info("Waiting for ping loop task to end")
        # if self.ping_loop_event:
        #     self.ping_loop_event.set()
        # await self.ping_loop_task
        if self.state == "connected":
            await self._trigger_event("disconnect", run_async=False)
            try:
                connected_clients.remove(self)
            except ValueError:
                pass
            await self._reset()
        if self.http:
            await self._reset()
            # self.logger.debug(self.http.connections)
        self.logger.info("Polling read loop: Exiting read loop task")

    async def _read_loop_websocket(self, task_status=trio.TASK_STATUS_IGNORED):
        """Read packets from the Engine.IO WebSocket connection."""
        with trio.CancelScope() as scope:
            timeout = max(self.ping_interval, self.ping_timeout) + 5
            task_status.started(scope)
            while self.state == "connected":
                try:
                    with trio.fail_after(timeout) as cancel_scope:
                        p = await self.ws.get_message()
                except trio_ws.ConnectionClosed:
                    self.logger.info("Websocket read loop: WebSocket connection was closed, aborting")
                    # self._write_task_scope.cancel()
                    # await self.send_channel.send(None)
                    break
                except Exception as e:
                    self.logger.info(f"Websocket read loop: Unexpected error receiving packet: {e}, aborting")
                    # self._write_task_scope.cancel()
                    # await self.send_channel.send(None)
                    break
                else:
                    if cancel_scope.cancelled_caught:
                        self.logger.info("Websocket read loop: WebSocket connection timeout, aborting")
                        break
                    if p is None:
                        raise RuntimeError("WebSocket read returned None")

                if isinstance(p, str):
                    p = p.encode("utf-8")

                try:
                    pkt = packet.Packet(encoded_packet=p)
                except Exception as e:
                    self.logger.info(f"Websocket read loop: Unexpected error decoding packet: {e}, aborting")
                    # self._write_task_scope.cancel()
                    # await self.send_channel.send(None)
                    break

                await self._receive_packet(pkt)

        self.logger.info("Websocket read loop: Waiting before cancelling tasks")
        await trio.sleep(0.05)
        self.logger.info("Websocket read loop: Cancelling write loop task")
        # await self.write_loop_task
        self._write_task_scope.cancel()
        self.logger.info('Websocket read loop: Cancelling ping loop task')
        self._ping_task_scope.cancel()
        # self.logger.info('Waiting for ping loop task to end')
        # if self.ping_loop_event:
        #     self.ping_loop_event.set()
        # await self.ping_loop_task
        # with trio.fail_after(self.request_timeout["connect"]):
        #     await self.ws.aclose()
        #     self.logger.info(f"Websocket read loop: Websocket closed: {self.ws.closed}")
        # if self.http:
        #     await self._reset()
        await self._reset()
        if self.state == "connected":
            await self._trigger_event("disconnect", run_async=False)
            try:
                connected_clients.remove(self)
            except ValueError:
                pass
            await self._reset()
        self.logger.info("Websocket read loop: Exiting read loop task")

    async def _write_loop(self, task_status=trio.TASK_STATUS_IGNORED):
        """This background task sends packages to the server as they are
        pushed to the send queue.
        """
        # to simplify the timeout handling, use the maximum of the
        # ping interval and ping timeout as timeout, with an extra 5
        # seconds grace period
        with trio.CancelScope() as scope:
            task_status.started(scope)

            timeout = max(self.ping_interval, self.ping_timeout) + 5

            while self.state == "connected":

                with trio.move_on_after(timeout) as cancel_scope:
                    pkt = await self.receive_channel.receive()
                    self.logger.debug(f"Write loop: Get packet to write: {pkt.packet_type}")
                if cancel_scope.cancelled_caught:
                    self.logger.error("Write loop: packet queue is empty, aborting")
                    break

                if pkt is None:
                    packets = []
                else:
                    packets = [pkt]
                    while True:
                        try:
                            pkt = self.receive_channel.receive_nowait()
                            self.logger.debug(f"Write loop: Get other packet to write: {pkt}")
                        except (trio.WouldBlock, trio.EndOfChannel):
                            break
                        except (trio.BrokenResourceError, trio.ClosedResourceError):
                            self.logger.error("Write loop: packet queue is closed, aborting")
                            break
                        else:
                            if pkt is not None:
                                packets.append(pkt)

                if not packets:
                    # empty packet list returned -> connection closed
                    self.logger.error("Write loop: No packet to send, aborting")
                    break

                if self.current_transport == "polling":
                    p = payload.Payload(packets=packets)
                    r = await self._send_request(
                        'POST', self.base_url, body=p.encode(),
                        headers={"Content-Type": "application/octet-stream"},
                        timeout=self.request_timeout)
                    if r is None:
                        self.logger.warning("Write loop: Connection refused by the server, aborting")
                        break
                    if r.status < 200 or r.status >= 300:
                        self.logger.warning(
                            f"Write loop: Unexpected status code {r.status} in server response, "
                            f"aborting"
                        )
                        await self._reset()
                        break
                    self.logger.debug("Write loop: Packet(s) written")
                else:
                    # websocket
                    try:
                        for pkt in packets:
                            await self.ws.send_message(pkt.encode(always_bytes=False))
                    except trio_ws.ConnectionClosed:
                        self.logger.info(
                            "Write loop: WebSocket connection was closed, aborting"
                        )
                        break
                    self.logger.debug("Write loop: Packet(s) written")

        self.logger.info("Write loop: Exiting write loop task")
