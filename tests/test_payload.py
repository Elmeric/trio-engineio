# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import pytest

from trio_engineio import packet, payload


def test_encode_empty_payload():
    p = payload.Payload()
    assert p.packets == []
    assert p.encode() == b""


def test_decode_empty_payload():
    p = payload.Payload(encoded_payload=b"")
    assert p.encode() == b""


def test_encode_payload_xhr2():
    pkt = packet.Packet(packet.MESSAGE, data=str("abc"))
    p = payload.Payload([pkt])
    assert p.packets == [pkt]
    assert p.encode() == b"\x00\x04\xff4abc"


def test_decode_payload_xhr2():
    p = payload.Payload(encoded_payload=b"\x00\x04\xff4abc")
    assert p.encode() == b"\x00\x04\xff4abc"


def test_encode_payload_xhr_text():
    pkt = packet.Packet(packet.MESSAGE, data=str("abc"))
    p = payload.Payload([pkt])
    assert p.packets == [pkt]
    assert p.encode(b64=True) == b"4:4abc"


def test_decode_payload_xhr_text():
    p = payload.Payload(encoded_payload=b"4:4abc")
    assert p.encode() == b"\x00\x04\xff4abc"


def test_encode_payload_xhr_binary():
    pkt = packet.Packet(packet.MESSAGE, data=b"\x00\x01\x02", binary=True)
    p = payload.Payload([pkt])
    assert p.packets == [pkt]
    assert p.encode(b64=True) == b"6:b4AAEC"


def test_decode_payload_xhr_binary():
    p = payload.Payload(encoded_payload=b"6:b4AAEC")
    assert p.encode() == b"\x01\x04\xff\x04\x00\x01\x02"


def test_encode_jsonp_payload():
    pkt = packet.Packet(packet.MESSAGE, data=str("abc"))
    p = payload.Payload([pkt])
    assert p.packets == [pkt]
    assert p.encode(jsonp_index=233) == b'___eio[233]("\x00\x04\xff4abc");'
    assert p.encode(jsonp_index=233, b64=True) == b'___eio[233]("4:4abc");'


def test_decode_jsonp_payload():
    p = payload.Payload(encoded_payload=b"d=4:4abc")
    assert p.encode() == b"\x00\x04\xff4abc"


def test_decode_invalid_payload():
    with pytest.raises(ValueError):
        payload.Payload(encoded_payload=b"bad payload")


def test_decode_multi_binary_payload():
    p = payload.Payload(encoded_payload=b"\x00\x04\xff4abc\x00\x04\xff4def")
    assert len(p.packets) == 2
    assert p.packets[0].data == "abc"
    assert p.packets[1].data == "def"


def test_decode_multi_text_payload():
    p = payload.Payload(encoded_payload=b"4:4abc4:4def")
    assert len(p.packets) == 2
    assert p.packets[0].data == "abc"
    assert p.packets[1].data == "def"


def test_decode_multi_binary_payload_with_too_many_packets():
    with pytest.raises(ValueError):
        payload.Payload(encoded_payload=b"\x00\x04\xff4abc\x00\x04\xff4def" * 9)


def test_decode_multi_text_payload_with_too_many_packets():
    with pytest.raises(ValueError):
        payload.Payload(encoded_payload=b"4:4abc4:4def" * 9)
