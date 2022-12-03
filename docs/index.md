# **Trio-Engineio**

**The `trio` asynchronous Engine.IO client.**

---

**[Trio-Engineio]** is an asynchronous **[Engine.IO]** client using the [`trio`][trio]
framework.

Engine.IO is the implementation of a robust bidirectional communication layer for
[Socket.IO].

Start by reading the [introductory tutorial], then check the
[User's Guide] for more information.
The rest of the docs describes each component of Trio-Engineio in detail, with a full
reference in the [API] section.
Finally, you may find some [Additional notes]

Trio-Engineio depends on the trio asynchronous framework.
Refer to [trio documentation] for usage information. 

<div align="center" markdown>

[Getting Started]{ .md-button .md-button--primary align=center}
[User's Guide]{ .md-button .md-button--primary align=center}

</div>

<div align="center" markdown>

[API Reference]{ .md-button .md-button--primary align=center}
[Additional notes]{ .md-button .md-button--primary align=center}

</div>

INFO: Only the revision **3** of the [Engine.IO protocol]
is supported. It allows connection to a v2.x Socket.IO server.

[Trio-Engineio]: https://github.com/Elmeric/trio-engineio
[Engine.IO]: https://github.com/socketio/engine.io
[trio]: https://trio.readthedocs.io/en/stable
[Socket.IO]: https://socket.io/docs/v2/
[introductory tutorial]: tutorials.md
[Getting Started]: tutorials.md
[User's Guide]: how-to-guides.md
[API]: reference.md
[API Reference]: reference.md
[Additional notes]: explanation.md
[trio documentation]: https://trio.readthedocs.io/en/stable
[Engine.IO protocol]: https://github.com/socketio/engine.io-protocol/tree/v3
