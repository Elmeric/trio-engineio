import sys
import logging

import trio

from trio_engineio.trio_client import TrioClient, EngineIoConnectionError

logger = logging.getLogger(__name__)


def commands():
    ''' Print the supported commands. '''
    print('Commands: ')
    print('send <MESSAGE>   -> send message')
    print('close  -> politely close connection')
    print()


def on_connect():
    print(f"***** Connected")


def on_message(msg):
    print(f"***** Received message: {msg}")


def on_disconnect():
    print(f"***** Disconnected")


async def get_commands(eio: TrioClient):
    ''' In a loop: get a command from the user and execute it. '''
    while True:
        cmd = await trio.to_thread.run_sync(input, 'cmd> ', cancellable=True)
        if cmd.startswith('send'):
            message = cmd[5:] or None
            if message is None:
                logging.error('The "send" command requires a message.')
            else:
                await eio.send(message)
        elif cmd.startswith('close'):
            await eio.disconnect()
            break
        else:
            commands()
        # Allow time to receive response and log print logs:
        await trio.sleep(0.25)


async def main():
    eio = TrioClient(logger=False)
    # eio = TrioClient(logger=logger)

    eio.on("connect", on_connect)
    eio.on("message", on_message)
    eio.on("disconnect", on_disconnect)

    async with trio.open_nursery() as nursery:
        try:
            await eio.connect(
                nursery,
                "http://192.168.0.39:3000",
                # transports=["websocket"],
                # transports=["polling"],
                transports=["polling", "websocket"],
                engineio_path="/socket.io"
            )
        except (EngineIoConnectionError, ValueError) as e:
            print(f"Connection refused by server: {e}")
            return False
        print("______________________________________")
        nursery.start_soon(get_commands, eio)
        # send 2["getState",""]
        # close
    return True


if __name__ == '__main__':
    try:
        if not trio.run(main):
            sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("Stopped by users")
