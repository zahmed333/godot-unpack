"""Godot readers, tested against files built here byte by byte from the format specs."""
import hashlib
import io
import struct

import pytest

from godot_unpack import pck, resource, variant


def _pad(b):
    return b + b"\0" * (-len(b) % 4)


def make_pck(files, fmt=2, encrypted_dir=False, encrypted_file=False):
    """Build a pack: format 1 (Godot 3), 2 (4.0-4.3) or 3 (4.4+)."""
    head = b"GDPC" + struct.pack("<4I", fmt, 4 if fmt > 1 else 3, 2, 0)
    flags = (1 if encrypted_dir else 0) | (2 if fmt >= 2 else 0)   # Godot 4 sets PACK_REL_FILEBASE
    if fmt == 1:
        head_len = len(head) + 64
    elif fmt == 2:
        head_len = len(head) + 4 + 8 + 64
    else:
        head_len = len(head) + 4 + 8 + 8 + 64
    blobs, offsets, o = b"", [], 0
    for _, data in files:
        offsets.append(o)
        blobs += _pad(data)
        o = len(blobs)

    def directory(base):
        d = struct.pack("<I", len(files))
        for (path, data), off in zip(files, offsets):
            p = _pad(path.encode() + b"\0")
            d += struct.pack("<I", len(p)) + p + struct.pack("<QQ", off + base, len(data)) + hashlib.md5(data).digest()
            if fmt >= 2:
                d += struct.pack("<I", 1 if encrypted_file else 0)
        return d

    if fmt == 1:
        d = directory(0)
        base = head_len + len(d)
        return head + b"\0" * 64 + directory(base) + blobs
    if fmt == 2:
        d = directory(0)
        base = head_len + len(d)
        return head + struct.pack("<IQ", flags, base) + b"\0" * 64 + d + blobs
    base = head_len
    return head + struct.pack("<IQQ", flags, base, base + len(blobs)) + b"\0" * 64 + blobs + directory(0)


FILES = [("res://project.binary", b"ECFG\0\0\0\0"), ("res://data/units.res", b"hello units")]


@pytest.mark.parametrize("fmt", [1, 2, 3, 4])
def test_pck_round_trip(fmt, tmp_path):
    raw = make_pck(FILES, fmt)
    p = pck.read_pack(io.BytesIO(raw))
    assert p.format == fmt and [e.path for e in p.entries] == [f for f, _ in FILES]
    f = io.BytesIO(raw)
    assert [pck.read_file(f, e) for e in p.entries] == [d for _, d in FILES]
    path = tmp_path / "x.pck"
    path.write_bytes(raw)
    assert pck.extract(path, tmp_path / "out", ["units"]) == (1, 0)
    assert (tmp_path / "out/data/units.res").read_bytes() == b"hello units"


def test_pck_encrypted_is_refused(tmp_path):
    with pytest.raises(pck.EncryptedPackError):
        pck.read_pack(io.BytesIO(make_pck(FILES, encrypted_dir=True)))
    path = tmp_path / "e.pck"
    path.write_bytes(make_pck(FILES, encrypted_file=True))
    assert pck.extract(path, tmp_path / "out") == (0, 2)


def test_pck_path_traversal_is_refused(tmp_path):
    path = tmp_path / "evil.pck"
    path.write_bytes(make_pck([("res://../../evil.txt", b"x")]))
    with pytest.raises(ValueError, match="unsafe"):
        pck.extract(path, tmp_path / "out")


# ---- encode_variant / project.binary

def v_str(s, t=4):
    b = s.encode()
    return struct.pack("<II", t, len(b)) + _pad(b)


def test_decode_variants():
    cases = [
        (struct.pack("<I", 0), None),
        (struct.pack("<II", 1, 1), True),
        (struct.pack("<Ii", 2, -5), -5),
        (struct.pack("<Iq", 2 | 1 << 16, 2 ** 40), 2 ** 40),
        (struct.pack("<Id", 3 | 1 << 16, 0.25), 0.25),
        (v_str("héllo"), "héllo"),
        (struct.pack("<I2f", 5, 1.5, -2.0), ("Vector2", (1.5, -2.0))),
        (struct.pack("<I4f", 20, 1, 0, 0, 1), ("Color", (1.0, 0.0, 0.0, 1.0))),
        (struct.pack("<II", 28, 2) + struct.pack("<Ii", 2, 7) + v_str("a"), [7, "a"]),
        (struct.pack("<II", 27, 1) + v_str("k") + struct.pack("<Ii", 2, 3), {"k": 3}),
        (struct.pack("<II3i", 30, 3, 1, 2, 3), [1, 2, 3]),
        (struct.pack("<II", 34, 2) + v_str("x")[4:] + v_str("yz")[4:], ["x", "yz"]),
    ]
    for raw, want in cases:
        got, end = variant.decode_variant(raw)
        assert got == want and end == len(raw)


def test_project_binary():
    def entry(key, value):
        k = key.encode()
        return struct.pack("<I", len(k)) + k + struct.pack("<I", len(value)) + value
    raw = b"ECFG" + struct.pack("<I", 2) + entry("application/config/name", v_str("Demo")) + \
        entry("display/window/size/viewport_width", struct.pack("<Ii", 2, 1280))
    assert variant.read_project_binary(raw) == {"application/config/name": "Demo",
                                                "display/window/size/viewport_width": 1280}


# ---- binary resources

def ustr(s):
    b = s.encode() + b"\0"
    return struct.pack("<I", len(b)) + b


def make_res():
    strings = ["name", "cost", "tags", "stats", "icon", "child"]
    head = b"RSRC" + struct.pack("<5I", 0, 0, 4, 3, 5) + ustr("Resource") + struct.pack("<Q", 0)
    head += struct.pack("<IQ", 2, 1234) + b"\0" * 44
    head += struct.pack("<I", len(strings)) + b"".join(ustr(s) for s in strings)
    head += struct.pack("<I", 1) + ustr("Texture2D") + ustr("res://icon.png") + struct.pack("<Q", 99)
    sub = ustr("Resource") + struct.pack("<I", 1) + struct.pack("<I", 1) + struct.pack("<Ii", 3, 4)
    main_props = [
        (0, struct.pack("<I", 5) + ustr("Ember Pup")),
        (1, struct.pack("<Ii", 3, 3)),
        (2, struct.pack("<II", 34, 2) + ustr("fire") + ustr("pet")),
        (3, struct.pack("<I2f", 10, 3.0, 6.0)),
        (4, struct.pack("<III", 24, 3, 0)),
        (5, struct.pack("<III", 24, 2, 0)),
    ]
    main = ustr("Resource") + struct.pack("<I", len(main_props)) + b"".join(struct.pack("<I", i) + v for i, v in main_props)
    table_len = 4 + 2 * (len(ustr("local://1")) + 8)
    base = len(head) + table_len
    table = struct.pack("<I", 2) + ustr("local://1") + struct.pack("<Q", base) + \
        ustr("local://0") + struct.pack("<Q", base + len(sub))
    return head + table + sub + main


def test_parse_resource():
    r = resource.parse(make_res())
    assert r["uid"] == 1234 and r["external"] == [{"type": "Texture2D", "path": "res://icon.png", "uid": 99}]
    child, main = r["resources"]
    assert child["props"] == {"cost": 4}
    assert main["props"] == {"name": "Ember Pup", "cost": 3, "tags": ["fire", "pet"], "stats": ["Vector2", [3.0, 6.0]],
                             "icon": {"ext": "res://icon.png"}, "child": {"internal": "local://1"}}


def test_parse_rejects_other_files():
    with pytest.raises(ValueError):
        resource.parse(b"RSCC....")
    with pytest.raises(ValueError):
        resource.parse(b"PNG.....")
