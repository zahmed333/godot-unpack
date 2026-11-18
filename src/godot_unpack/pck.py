"""Read Godot ``.pck`` packs (format versions 1-4: Godot 3.x, 4.0-4.3, 4.4, 4.5+).

For inspecting packs of your own projects (lost source, CI checks of what gets shipped,
diffing two exports). Encrypted packs are reported, not decrypted.

Works on standalone ``.pck`` files and on executables exported with "Embed PCK".

    godot-unpack pck list game.pck [substring]
    godot-unpack pck extract game.exe out_dir [substring ...]
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass

MAGIC = b"GDPC"
PACK_DIR_ENCRYPTED = 1
PACK_REL_FILEBASE = 2
FILE_ENCRYPTED = 1
FILE_REMOVAL = 2   # Godot 4.4+ patch packs: entry deletes a file from an earlier pack


class EncryptedPackError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    path: str
    offset: int        # absolute offset in the pack file
    size: int
    md5: bytes
    flags: int = 0

    @property
    def encrypted(self):
        return bool(self.flags & FILE_ENCRYPTED)

    @property
    def removal(self):
        return bool(self.flags & FILE_REMOVAL)


@dataclass(frozen=True)
class Pack:
    format: int
    godot_version: tuple
    entries: list


def _u32(f):
    return struct.unpack("<I", f.read(4))[0]


def _u64(f):
    return struct.unpack("<Q", f.read(8))[0]


def locate(f) -> int:
    """Offset of the pack inside ``f``: 0 for a .pck, or the start of the pack appended to an
    executable exported with "Embed PCK" (which ends with ``u64 pack size, "GDPC"``)."""
    f.seek(0)
    if f.read(4) == MAGIC:
        return 0
    f.seek(0, os.SEEK_END)
    end = f.tell()
    if end >= 12:
        f.seek(end - 12)
        size = _u64(f)
        if f.read(4) == MAGIC and 0 < size <= end - 12:
            start = end - 12 - size
            f.seek(start)
            if f.read(4) == MAGIC:
                return start
    raise ValueError("no Godot pack found (not a .pck, and no embedded pack at the end of the file)")


def open_pack(path):
    """Open a .pck or an executable with an embedded pack; returns (file, Pack)."""
    f = open(path, "rb")
    try:
        f.seek(locate(f))
        return f, read_pack(f)
    except Exception:
        f.close()
        raise


def read_pack(f) -> Pack:
    """Parse the header and directory of an open pack file."""
    start = f.tell()
    if f.read(4) != MAGIC:
        raise ValueError("not a Godot pack (missing GDPC magic)")
    fmt = _u32(f)
    version = (_u32(f), _u32(f), _u32(f))
    if fmt not in (1, 2, 3, 4):   # 4 (Godot 4.5+) keeps the v3 header and directory layout
        raise ValueError(f"unsupported pack format {fmt}")
    flags, file_base = 0, 0
    if fmt >= 2:
        flags = _u32(f)
        base = _u64(f)
        file_base = base + start if flags & PACK_REL_FILEBASE else base
    if fmt >= 3:
        f.seek(start + _u64(f))
    else:
        f.read(16 * 4)   # reserved
    if flags & PACK_DIR_ENCRYPTED:
        raise EncryptedPackError("the pack's directory is encrypted; this tool reads unencrypted packs only")
    count = _u32(f)
    entries = []
    for _ in range(count):
        n = _u32(f)
        path = f.read(n).rstrip(b"\0").decode("utf-8")
        off, size = _u64(f), _u64(f)
        md5 = f.read(16)
        eflags = _u32(f) if fmt >= 2 else 0
        entries.append(Entry(path, (file_base + off) if fmt >= 2 else start + off, size, md5, eflags))
    return Pack(fmt, version, entries)


def read_file(f, entry: Entry) -> bytes:
    if entry.encrypted:
        raise EncryptedPackError(f"{entry.path} is encrypted")
    f.seek(entry.offset)
    return f.read(entry.size)


def extract(pack_path, out_dir, patterns=()):
    """Extract entries whose path contains any of ``patterns`` (all if empty). Returns
    (extracted, skipped_encrypted)."""
    n = skipped = 0
    root = os.path.abspath(out_dir)
    f, pack = open_pack(pack_path)
    with f:
        for e in pack.entries:
            if patterns and not any(p in e.path for p in patterns):
                continue
            if e.removal:
                continue
            if e.encrypted:
                skipped += 1
                continue
            rel = e.path.replace("res://", "").lstrip("/")
            dst = os.path.abspath(os.path.join(root, rel))
            if not dst.startswith(root + os.sep):   # refuse ../ paths from a crafted pack
                raise ValueError(f"unsafe path in pack: {e.path!r}")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as o:
                o.write(read_file(f, e))
            n += 1
    return n, skipped


def summary(pack: Pack) -> dict:
    """Counts and sizes by file type, plus what kind of export this looks like."""
    by_ext = {}
    for e in pack.entries:
        ext = os.path.splitext(e.path)[1].lower() or "(none)"
        n, size = by_ext.get(ext, (0, 0))
        by_ext[ext] = (n + 1, size + e.size)
    paths = {e.path for e in pack.entries}
    scripts = "binary tokens (.gdc)" if by_ext.get(".gdc") else "text (.gd)" if by_ext.get(".gd") else "none found"
    return {"format": pack.format, "godot": ".".join(map(str, pack.godot_version)),
            "files": len(pack.entries), "bytes": sum(e.size for e in pack.entries),
            "encrypted_files": sum(e.encrypted for e in pack.entries), "scripts": scripts,
            "has_project_settings": "res://project.binary" in paths,
            "by_extension": dict(sorted(by_ext.items(), key=lambda kv: -kv[1][1]))}


def diff(a: Pack, b: Pack) -> dict:
    """Files added, removed and changed between two packs (by MD5 when both have one, else size)."""
    ea, eb = {e.path: e for e in a.entries}, {e.path: e for e in b.entries}
    zero = bytes(16)

    def changed(x, y):
        if x.md5 != zero and y.md5 != zero:
            return x.md5 != y.md5
        return x.size != y.size
    return {"added": sorted(set(eb) - set(ea)), "removed": sorted(set(ea) - set(eb)),
            "changed": sorted(p for p in set(ea) & set(eb) if changed(ea[p], eb[p]))}
