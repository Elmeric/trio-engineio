
# Trio-Engine.IO

[![Tests](https://github.com/Elmeric/trio-engineio/actions/workflows/test.yml/badge.svg)](https://github.com/Elmeric/trio-engineio/actions/workflows/test.yml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![license](https://img.shields.io/badge/license-BSD--3--Clause-green)](https://github.com/Elmeric/trio-engineio/blob/master/LICENSE)
[![python](https://img.shields.io/badge/python-3.7%2B-blue)](https://github.com/Elmeric/trio-engineio)

An asynchronous **[Engine.IO](https://github.com/socketio/engine.io-protocol/tree/v3)** client using the [`trio`](https://trio.readthedocs/en/latest) framework.

Only the revision **3** of the Engine.IO protocol is supported.

## Requirements

- Python 3.7+
- [`trio`](https://trio.readthedocs.io/)
- [`httpcore`](https://www.encode.io/httpcore/)
- [`trio-websocket`](https://trio-websocket.readthedocs.io/)

## Usage

```Python
import trio

from trio_engineio.trio_client import EngineIoClient, EngineIoConnectionError


def on_connect():
    print(f"***** Connected")


def on_message(msg):
    print(f"***** Received message: {msg}")


def on_disconnect():
    print(f"***** Disconnected")

    
async def main():
    eio = EngineIoClient(logger=False)

    eio.on("connect", on_connect)
    eio.on("message", on_message)
    eio.on("disconnect", on_disconnect)

    async with trio.open_nursery() as nursery:
        try:
            await eio.connect(nursery, "http://127.0.0.1:1234")
        except EngineIoConnectionError:
            return False
    return True


trio.run(main)
```
