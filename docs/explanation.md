# Additional notes

About Engine.IO and Trio-Engineio...

---

## Why another Engine.IO client in Python?

When I look for a Python implementation of a Engine.IO client for one of my personal
project, I naturally found the [python-socketio] from Miguel Grinberg.

This library supports the standard [asyncio] framework but not the [trio] framework which 
was a strong requirement for me.

After some infructuous research on the Web, it appears that I will have to do my own
port of the python-socketio client to trio ([1]).

As Engine.IO is the low-level transport engine of the Socket.IO protocol, my firts
task was to port the [python-engineio] client to trio: Trio-Engineio was born... and
a Trio-Socketio library is comming!

[python-engineio]: https://github.com/miguelgrinberg/python-engineio
[python-socketio]: https://github.com/miguelgrinberg/python-socketio
[asyncio]: https://docs.python.org/3/library/asyncio.html
[trio]: https://trio.readthedocs.io/en/stable
[1]: https://stackoverflow.com/a/62753938/20078583
