# Getting started

You're at the right place to start!

---

## What is Engine.IO?

Engine.IO is a transport protocol that enables event-based bidirectional communication
between clients and a server.

The official implementations of the client and server components are written in
JavaScript.
python-engineio is a complete Python implementation of both, each with standard and
`asyncio` variants.
This library provides a Python implementations of the client only, but using the `trio`
asynchronous framework.

The Engine.IO protocol is extremely simple. Once a connection between a client and a
server is established, either side can send “messages” to the other side. Event handlers
provided by the applications on both ends are invoked when a message is received,
or when a connection is established or dropped.

## Installing trio-engineio

`trio-engineio` requires Python 3.7.2 or greater to be installed on your computer.

Install trio-engineio using pip or your favorite Python packages manager:

=== "Linux / macOS"
    ```shell
    $ python3 -m pip install trio_engineio
    ```
===+ "Windows"
    ```shell
    $ py -3 -m pip install trio_engineio
    ```
## Creating a client instance

To instantiate an Engine.IO client, simply create an instance of the
[`EngineIoClient`][EngineIoClient] class:

```{.python title="test_trio_client.py"}
from trio_engineio import EngineIoClient

eio = EngineIoClient()
```

## Registering event Handlers

Any function decorated with the [`EngineIoClient.on`][on] decorator is registered as an event
handler and is called when the corresponding event is triggered by the server.

The decorator's argument is the name of the event. Three events are supported:

- connect
- message
- disconnect


```{.python title="test_trio_client.py"}
@eio.on("connect")
def on_connect():
    print("Connected!")

@eio.on("message")
def on_message(data):
    print(f"Received a message: {data}")

@eio.on("disconnect")
def on_disconnect():
    print("Disconnected!")
```

The "message" event handler shall accept a unique argument, allowing the server to pass
application-specific data with the message.

The "connect" and "message" events are always sent by the server, but the "disconnect"
event can be raised on client initiated disconnection, server initiated disconnection
or unexpected disconnection, for example due to a network failure.

Event handlers can be regular functions as above, or can also be coroutines:

```{.python title="test_trio_client.py"}
@eio.on("message")
async def on_message(data):
    print(f"Received a message: {data}")
```

## Connecting to a server

The connection to a server is performed asynchronously using the
[`EngineIoClient.connect`][connect] coroutine. As it launches several background
trio tasks to keep the connection alive and handle incoming events, it requires
a trio nursery to host them. The second argument of `EngineIoClient.connect` is
the url of an active Engine.IO server.

You should take care of the [`EngineIoConnectionError`][EngineIoConnectionError]
exception.

```{.python title="test_trio_client.py"}
import trio
from trio_engineio import EngineIoConnectionError

async def main():
    async with trio.open_nursery() as nursery:
        try:
            await eio.connect(nursery, "http://127.0.0.1:3000")
        except EngineIoConnectionError:
            print("Connection refused by the server!")

trio.run(main)
```

## Sending messages

The client can send a message to the server by calling the [`EngineIoClient.send`][send]
coroutine and providing it with the data to be passed to the server.
`str`, `bytes`, `dict` or `list` are accepted. Data included in dictionaries and lists
shall also be of these types.

```Python
await eio.send({"foo": "bar"})
```

You can call the `EngineIoClient.send` coroutine inside an event handler to answer to a
server event or in any other part of your application.

## Disconnecting from the server

The client can disconnect from the server using the [`EngineIoClient.disconnect`][disconnect]
coroutine. It will trigger any registered "disconnect" event handler.

```Python
await eio.disconnect()
```

## Debugging and Troubleshooting

The [`EngineIoClient`][EngineIoClient] constructor accepts a "logger" argument to help
you debug issues into your application such as connection problems, unexpected
disconnection,...

Possible values are:

- `False` to disable logging. Note that fatal errors will still be logged on stderr.
- `True` to enable logging at INFO log level on stderr.
- a custom `logging.Logger` object.

```Python
from trio_engineio import EngineIoClient

eio = EngineIoClient(logger=True)
```

[EngineIoClient]: reference.md#trio_engineio.trio_client.EngineIoClient
[on]: reference.md#trio_engineio.trio_client.EngineIoClient.on
[connect]: reference.md#trio_engineio.trio_client.EngineIoClient.connect
[send]: reference.md#trio_engineio.trio_client.EngineIoClient.send
[disconnect]: reference.md#trio_engineio.trio_client.EngineIoClient.disconnect
[EngineIoConnectionError]: reference.md#trio_engineio.exceptions.EngineIoConnectionError
