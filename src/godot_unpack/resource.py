"""Parse Godot 4 binary resources (``.res``, ``.scn`` and imported ``.*.res`` files, magic
``RSRC``) into JSON-friendly dictionaries. Useful for pulling data tables (items, units,
balance numbers) out of a project's exported resources into a simulator or a notebook.

Supports little-endian files; uncompressed only (``RSCC`` compressed files are rejected).
"""
from __future__ import annotations

import struct

FLAG_UIDS = 2
FLAG_REAL_IS_DOUBLE = 4
FLAG_HAS_SCRIPT_CLASS = 8
RESERVED_FIELDS = 11

_MATH = {10: ("Vector2", 2, "r"), 45: ("Vector2i", 2, "i"), 11: ("Rect2", 4, "r"), 46: ("Rect2i", 4, "i"),
         12: ("Vector3", 3, "r"), 47: ("Vector3i", 3, "i"), 50: ("Vector4", 4, "r"), 51: ("Vector4i", 4, "i"),
         13: ("Plane", 4, "r"), 14: ("Quaternion", 4, "r"), 15: ("AABB", 6, "r"), 16: ("Basis", 9, "r"),
         17: ("Transform3D", 12, "r"), 18: ("Transform2D", 6, "r"), 52: ("Projection", 16, "r"),
         20: ("Color", 4, "f")}
_PACKED = {32: "i", 48: "q", 33: "f", 49: "d"}
_PACKED_VEC = {37: 2, 35: 3, 36: 4, 53: 4}   # Vector2/Vector3/Color/Vector4 arrays (Color is always float)


class _Reader:
    def __init__(self, b):
        self.b, self.p, self.real = b, 0, "f"

    def unpack(self, fmt):
        v = struct.unpack_from("<" + fmt, self.b, self.p)
        self.p += struct.calcsize("<" + fmt)
        return v

    def u32(self):
        return self.unpack("I")[0]

    def u64(self):
        return self.unpack("Q")[0]

    def ustr(self):
        n = self.u32()
        s = self.b[self.p:self.p + n].rstrip(b"\0").decode("utf-8", "replace")
        self.p += n
        return s


def parse(data: bytes) -> dict:
    """Return ``{"type", "uid", "script_class", "external": [...], "resources": [...]}`` where
    each resource is ``{"path", "type", "props"}``. Object references become
    ``{"ext": path}`` or ``{"internal": local path}``."""
    if data[:4] == b"RSCC":
        raise ValueError("compressed resource (RSCC) not supported")
    if data[:4] != b"RSRC":
        raise ValueError("not a binary resource (missing RSRC magic)")
    r = _Reader(data)
    r.p = 4
    big_endian, _use_real64, ver_major, _ver_minor, ver_format = r.unpack("5I")
    if big_endian:
        raise ValueError("big-endian resources not supported")
    rtype = r.ustr()
    r.u64()   # import metadata offset
    flags = r.u32()
    uid = r.u64()
    if flags & FLAG_REAL_IS_DOUBLE:
        r.real = "d"
    script_class = r.ustr() if flags & FLAG_HAS_SCRIPT_CLASS else None
    r.unpack(f"{RESERVED_FIELDS}I")
    strings = [r.ustr() for _ in range(r.u32())]
    external = []
    for _ in range(r.u32()):
        ext = {"type": r.ustr(), "path": r.ustr()}
        if flags & FLAG_UIDS:
            ext["uid"] = r.u64()
        external.append(ext)
    internal = [(r.ustr(), r.u64()) for _ in range(r.u32())]

    def get_string():
        i = r.u32()
        if i & 0x80000000:
            n = i & 0x7FFFFFFF
            s = r.b[r.p:r.p + n].rstrip(b"\0").decode("utf-8", "replace")
            r.p += n
            return s
        return strings[i]

    def var():
        t = r.u32()
        if t == 1:
            return None
        if t == 2:
            return bool(r.u32())
        if t == 3:
            return r.unpack("i")[0]
        if t == 40:
            return r.unpack("q")[0]
        if t == 4:
            return r.unpack("f")[0]
        if t == 41:
            return r.unpack("d")[0]
        if t in (5, 44):   # String, StringName
            return r.ustr()
        if t in _MATH:
            name, n, kind = _MATH[t]
            code = {"r": r.real, "i": "i", "f": "f"}[kind]
            return [name, list(r.unpack(code * n))]
        if t == 22:   # NodePath
            names = r.unpack("H")[0]
            sub = r.unpack("H")[0]
            absolute, sub = bool(sub & 0x8000), sub & 0x7FFF
            parts = [get_string() for _ in range(names + sub)]
            return "^" + ("/" if absolute else "") + "/".join(parts[:names]) + "".join(":" + s for s in parts[names:])
        if t == 23:   # RID
            return ["RID", r.u32()]
        if t in (42, 43):   # Callable, Signal: not serialised
            return None
        if t == 24:   # Object
            kind = r.u32()
            if kind == 0:
                return None
            if kind == 1:
                return {"ext": (r.ustr(), r.ustr())[1]}
            if kind == 2:
                return {"internal": internal[r.u32()][0]}
            if kind == 3:
                i = r.u32()
                return {"ext": external[i]["path"] if i < len(external) else i}
            raise ValueError(f"unknown object reference kind {kind}")
        if t == 26:   # Dictionary
            n = r.u32() & 0x7FFFFFFF
            out = {}
            for _ in range(n):
                k = var()
                out[k if isinstance(k, (str, int, float, bool, type(None))) else repr(k)] = var()
            return out
        if t == 30:   # Array
            return [var() for _ in range(r.u32() & 0x7FFFFFFF)]
        if t == 31:   # PackedByteArray
            n = r.u32()
            r.p += n + (-n % 4)
            return f"<{n} bytes>"
        if t in _PACKED:
            n = r.u32()
            return list(r.unpack(_PACKED[t] * n)) if n else []
        if t == 34:
            return [r.ustr() for _ in range(r.u32())]
        if t in _PACKED_VEC:
            n, k = r.u32(), _PACKED_VEC[t]
            code = "f" if t == 36 else r.real
            flat = r.unpack(code * (n * k)) if n else ()
            return [list(flat[i:i + k]) for i in range(0, len(flat), k)]
        raise ValueError(f"unsupported variant type {t} at offset {r.p - 4}")

    resources = []
    for path, off in internal:
        r.p = off
        typ = r.ustr()
        props = {}
        for _ in range(r.u32()):
            name = strings[r.u32()]
            props[name] = var()
        resources.append({"path": path, "type": typ, "props": props})
    return {"type": rtype, "format": ver_format, "godot_major": ver_major, "uid": uid,
            "script_class": script_class, "external": external, "resources": resources}
