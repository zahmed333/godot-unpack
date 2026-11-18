"""Decompiler tests on .gdc files assembled here from the tokenizer-buffer format."""
import struct

import pytest

from godot_unpack.gdscript import TOKENS_101, decompile, read_tokens

T = {t: i for i, t in enumerate(TOKENS_101)}


def build(lines, long_form=True, version=101):
    """lines: [(column, [token, ...])]; a token is a keyword/symbol, ("id", name),
    ("@", annotation) or ("lit", encoded variant bytes)."""
    idents, consts, toks, line_map = [], [], [], []
    for ln, (col, line) in enumerate(lines, 1):
        line_map.append((len(toks), ln, col))
        for t in line:
            if isinstance(t, tuple):
                kind, val = t
                pool = consts if kind == "lit" else idents
                if val not in pool:
                    pool.append(val)
                code = T["LITERAL" if kind == "lit" else "ANNOTATION" if kind == "@" else "IDENTIFIER"] | pool.index(val) << 8
            else:
                code = T[t]
            toks.append((code, ln))
    b = struct.pack("<4I", len(idents), len(consts), len(line_map), len(toks))
    for name in idents:
        b += struct.pack("<I", len(name)) + b"".join(struct.pack("<I", ord(c) ^ 0xB6B6B6B6) for c in name)
    b += b"".join(consts)
    b += b"".join(struct.pack("<II", i, ln) for i, ln, _ in line_map)
    b += b"".join(struct.pack("<II", i, col) for i, _, col in line_map)
    for code, ln in toks:
        if long_form or code > 0x7F:
            b += struct.pack("<II", code | 0x80, ln)
        else:
            b += bytes([code]) + struct.pack("<I", ln)
    return b"GDSC" + struct.pack("<II", version, 0) + b


def s(text):
    raw = text.encode()
    return struct.pack("<II", 4, len(raw)) + raw + b"\0" * (-len(raw) % 4)


SCRIPT = [
    (1, ["extends", ("id", "Node")]),
    (1, [("@", "@export"), "var", ("id", "speed"), ":", ("id", "float"), "=", ("lit", struct.pack("<If", 3, 2.5))]),
    (1, ["func", ("id", "_ready"), "(", ")", "->", "void", ":"]),
    (5, ["if", ("id", "speed"), ">", ("lit", struct.pack("<Ii", 2, 1)), ":"]),
    (9, [("id", "print"), "(", ("lit", s('hi "there"')), ",", ("lit", struct.pack("<II", 21, 3) + b"abc\0"), ")"]),
    (5, ["return"]),
]
EXPECTED = '''extends Node
@export var speed: float = 2.5
func _ready() -> void:
\tif speed > 1:
\t\tprint("hi \\"there\\"", &"abc")
\treturn
'''


@pytest.mark.parametrize("long_form", [True, False])
def test_decompile_script(long_form):
    assert decompile(build(SCRIPT, long_form)) == EXPECTED


def test_version_100_has_no_ellipsis_token():
    v, toks = read_tokens(build([(1, ["pass"])], version=100))
    assert v == 100 and toks[0][0] == "pass"


def test_rejects_other_files():
    with pytest.raises(ValueError):
        decompile(b"RSRC" + b"\0" * 20)
    with pytest.raises(ValueError, match="version"):
        decompile(b"GDSC" + struct.pack("<II", 7, 0) + b"\0" * 16)
