"""Decode Godot 4 ``encode_variant`` values (``project.binary`` settings, network packets,
``var_to_bytes`` output) into plain Python values."""
from __future__ import annotations

import struct

# Variant::Type ids (Godot 4)
NIL, BOOL, INT, FLOAT, STRING = 0, 1, 2, 3, 4
_VECTORS = {5: ("Vector2", 2, "f"), 6: ("Vector2i", 2, "i"), 7: ("Rect2", 4, "f"), 8: ("Rect2i", 4, "i"),
            9: ("Vector3", 3, "f"), 10: ("Vector3i", 3, "i"), 11: ("Transform2D", 6, "f"),
            12: ("Vector4", 4, "f"), 13: ("Vector4i", 4, "i"), 14: ("Plane", 4, "f"),
            15: ("Quaternion", 4, "f"), 16: ("AABB", 6, "f"), 17: ("Basis", 9, "f"),
            18: ("Transform3D", 12, "f"), 19: ("Projection", 16, "f"), 20: ("Color", 4, "c")}
STRING_NAME, NODE_PATH = 21, 22
DICTIONARY, ARRAY = 27, 28
_PACKED = {29: ("B", 1), 30: ("i", 4), 31: ("q", 8), 32: ("f", 4), 33: ("d", 8)}
PACKED_STRING_ARRAY = 34
ENCODE_FLAG_64 = 1 << 16


class StringName(str):
    """A decoded StringName (``&"name"`` in GDScript); behaves as a plain ``str``."""


class NodePath(str):
    """A decoded NodePath (``^"path"`` in GDScript); behaves as a plain ``str``."""


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _string(b, o):
    n = _u32(b, o)
    s = b[o + 4:o + 4 + n].decode("utf-8", "replace")
    return s, o + 4 + n + (-n % 4)


def decode_variant(b: bytes, o: int = 0):
    """Decode one value at offset ``o``; returns ``(value, next_offset)``.

    Scalars become Python scalars, strings ``str``, arrays ``list``, dictionaries ``dict``
    (keys converted with ``repr`` when not hashable). Math types become ``(TypeName, tuple)``
    pairs, e.g. ``("Vector2", (1.0, 2.0))``.
    """
    h = _u32(b, o)
    t, wide = h & 0xFF, bool(h & ENCODE_FLAG_64)
    o += 4
    if t == NIL:
        return None, o
    if t == BOOL:
        return bool(_u32(b, o)), o + 4
    if t == INT:
        return (struct.unpack_from("<q", b, o)[0], o + 8) if wide else (struct.unpack_from("<i", b, o)[0], o + 4)
    if t == FLOAT:
        return (struct.unpack_from("<d", b, o)[0], o + 8) if wide else (struct.unpack_from("<f", b, o)[0], o + 4)
    if t == STRING:
        return _string(b, o)
    if t == STRING_NAME:
        s, o = _string(b, o)
        return StringName(s), o
    if t in _VECTORS:
        name, n, kind = _VECTORS[t]
        code = "i" if kind == "i" else "d" if (wide and kind == "f") else "f"
        size = 8 if code == "d" else 4
        return (name, struct.unpack_from("<" + code * n, b, o)), o + n * size
    if t == NODE_PATH:
        n = _u32(b, o)
        if not n & 0x80000000:   # legacy form: plain string
            s, o = _string(b, o)
            return NodePath(s), o
        names, subnames = n & 0x7FFFFFFF, _u32(b, o + 4)
        o += 12  # name count, subname count, flags
        parts = []
        for _ in range(names + (subnames & 0x7FFFFFFF)):
            s, o = _string(b, o)
            parts.append(s)
        return NodePath("/".join(parts[:names]) + "".join(":" + s for s in parts[names:])), o
    if t == ARRAY:
        n = _u32(b, o) & 0x7FFFFFFF
        o += 4
        out = []
        for _ in range(n):
            v, o = decode_variant(b, o)
            out.append(v)
        return out, o
    if t == DICTIONARY:
        n = _u32(b, o) & 0x7FFFFFFF
        o += 4
        out = {}
        for _ in range(n):
            k, o = decode_variant(b, o)
            v, o = decode_variant(b, o)
            out[k if isinstance(k, (str, int, float, bool, type(None))) else repr(k)] = v
        return out, o
    if t in _PACKED:
        code, size = _PACKED[t]
        n = _u32(b, o)
        o += 4
        if code == "B":
            return bytes(b[o:o + n]), o + n + (-n % 4)
        return list(struct.unpack_from(f"<{n}{code}", b, o)), o + n * size
    if t == PACKED_STRING_ARRAY:
        n = _u32(b, o)
        o += 4
        out = []
        for _ in range(n):
            s, o = _string(b, o)
            out.append(s)
        return out, o
    raise ValueError(f"unsupported variant type {t} at offset {o - 4}")


def read_project_binary(data: bytes) -> dict:
    """Parse an exported ``project.binary`` (ECFG) into ``{setting: value}``."""
    if data[:4] != b"ECFG":
        raise ValueError("not a project.binary (missing ECFG magic)")
    count, o, out = _u32(data, 4), 8, {}
    for _ in range(count):
        n = _u32(data, o)
        key = data[o + 4:o + 4 + n].decode("utf-8")
        o += 4 + n
        vlen = _u32(data, o)
        out[key], _ = decode_variant(data, o + 4)
        o += 4 + vlen
    return out
