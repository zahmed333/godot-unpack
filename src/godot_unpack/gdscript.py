"""Decompile Godot 4 binary-tokenized GDScript (``.gdc``, "GDSC" tokenizer versions 100/101)
back to readable ``.gd`` source.

Exports made with "Binary tokens" script mode ship ``.gdc`` files instead of text. This is a
token-level decompiler: identifiers, literals and keywords come back exactly, while comments
and original spacing do not exist in the file and cannot be recovered. Indentation is
rebuilt from the per-line column table.

    python -m godot_unpack gdc <file.gdc | dir> [out_dir]
"""
from __future__ import annotations

import os
import struct

from .variant import NodePath, StringName, decode_variant

# GDScriptTokenizer::Token::Type, in enum order (Godot 4.4+, tokenizer version 101).
TOKENS_101 = """EMPTY ANNOTATION IDENTIFIER LITERAL < <= > >= == != and or not && || ! & | ~ ^ << >>
+ - * ** / % = += -= *= **= /= %= <<= >>= &= |= ^= if elif else for while break continue pass
return match when as assert await breakpoint class class_name const enum extends func in is
namespace preload self signal static super trait var void yield [ ] { } ( ) , ; . .. ... : $ ->
_ NEWLINE INDENT DEDENT PI TAU INF NAN VCS_CONFLICT ` ? ERROR EOF""".split()
# Version 100 (Godot 4.3) predates the "..." token.
TOKENS = {101: TOKENS_101, 100: [t for t in TOKENS_101 if t != "..."]}

TOKEN_BYTE_MASK = 0x80
TOKEN_BITS = 8
IDENT_XOR = 0xB6B6B6B6
NO_SPACE_BEFORE = {")", "]", ",", ".", ":", "(", "[", "}", ";"}
NO_SPACE_AFTER = {"(", "[", ".", "$", "{", "!", "~"}
OPEN, CLOSE = {"(", "[", "{"}, {")", "]", "}"}


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _decompress(raw):
    version, size = _u32(raw, 4), _u32(raw, 8)
    if not size:
        return version, raw[12:]
    try:
        import zstandard
    except ImportError as e:   # optional dependency: pip install godot-unpack[zstd]
        raise RuntimeError("this .gdc is zstd-compressed; install the 'zstandard' package") from e
    return version, zstandard.ZstdDecompressor().decompress(raw[12:], max_output_size=size)


def _literal(v):
    if isinstance(v, tuple):   # math types from decode_variant: ("Vector2", (x, y))
        return f"{v[0]}({', '.join(_num(x) for x in v[1])})"
    if isinstance(v, str):
        prefix = "&" if isinstance(v, StringName) else "^" if isinstance(v, NodePath) else ""
        return prefix + '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return _num(v)
    if isinstance(v, list):
        return "[" + ", ".join(_literal(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_literal(k)}: {_literal(x)}" for k, x in v.items()) + "}"
    if isinstance(v, bytes):
        return f"PackedByteArray({list(v)})"
    return str(v)


def _num(x):
    if isinstance(x, float):
        if x != x:
            return "NAN"
        if x in (float("inf"), float("-inf")):
            return "INF" if x > 0 else "-INF"
        r = repr(round(x, 6))
        return r if ("." in r or "e" in r) else r + ".0"
    return str(x)


def read_tokens(raw: bytes):
    """Parse a .gdc file. Returns ``(version, tokens)`` where each token is ``(text, line,
    column or None)``."""
    if raw[:4] != b"GDSC":
        raise ValueError("not a binary GDScript file (missing GDSC magic)")
    version, b = _decompress(raw)
    if version not in TOKENS:
        raise ValueError(f"unsupported tokenizer version {version}")
    table = TOKENS[version]
    n_ident, n_const, n_lines, n_tokens = (_u32(b, 4 * i) for i in range(4))
    o = 16
    idents = []
    for _ in range(n_ident):
        n = _u32(b, o)
        o += 4
        idents.append("".join(chr(_u32(b, o + 4 * i) ^ IDENT_XOR) for i in range(n)))
        o += 4 * n
    consts = []
    for _ in range(n_const):
        v, o = decode_variant(b, o)
        consts.append(v)
    o += n_lines * 8   # (token index, line) map; every token also carries its line
    columns = {_u32(b, o + 8 * i): _u32(b, o + 8 * i + 4) for i in range(n_lines)}
    o += n_lines * 8
    tokens = []
    for i in range(n_tokens):
        if b[o] & TOKEN_BYTE_MASK:   # long form: u32 type | payload << 8, then u32 line
            tok = _u32(b, o) & ~TOKEN_BYTE_MASK
            o += 4
        else:                        # short form: one byte type, then u32 line
            tok = b[o]
            o += 1
        line = _u32(b, o)
        o += 4
        kind, payload = tok & 0x7F, tok >> TOKEN_BITS
        name = table[kind]
        if name in ("IDENTIFIER", "ANNOTATION"):
            text = idents[payload]
        elif name == "LITERAL":
            text = _literal(consts[payload])
        else:
            text = name
        tokens.append((text, line, columns.get(i)))
    return version, tokens


def decompile(raw: bytes) -> str:
    _, tokens = read_tokens(raw)
    out, line_parts, cur_line, prev = [], [], None, None
    stack, depth, indent = [1], 0, 0
    for text, line, col in tokens:
        if text in ("NEWLINE", "INDENT", "DEDENT", "EMPTY"):
            continue
        if text == "EOF":
            break
        if line != cur_line:
            if line_parts:
                out.append("\t" * indent + "".join(line_parts))
            if cur_line is not None and line > cur_line + 1:
                out.extend([""] * min(line - cur_line - 1, 2))
            line_parts, prev, cur_line = [], None, line
            if col is not None and depth == 0:
                while col < stack[-1] and len(stack) > 1:
                    stack.pop()
                if col > stack[-1]:
                    stack.append(col)
                indent = len(stack) - 1
            elif depth > 0:   # continuation line inside brackets; a closing bracket lines up with its opener
                indent = len(stack) - (text in CLOSE)
        if line_parts and text not in NO_SPACE_BEFORE and prev not in NO_SPACE_AFTER:
            line_parts.append(" ")
        if text in ("(", "[") and line_parts and line_parts[-1] == " " and prev and \
                (prev[-1].isalnum() or prev[-1] in "_)]\""):
            line_parts.pop()
        line_parts.append(text)
        depth += text in OPEN
        depth -= text in CLOSE
        depth = max(depth, 0)
        prev = text
    if line_parts:
        out.append("\t" * indent + "".join(line_parts))
    return "\n".join(out) + "\n"


def decompile_tree(src, dst):
    """Decompile every .gdc under ``src`` into ``dst`` (same layout). Returns (ok, failed)."""
    ok = failed = 0
    root = os.path.abspath(dst)
    for base, _, files in os.walk(src):
        for fn in files:
            if not fn.endswith(".gdc"):
                continue
            p = os.path.join(base, fn)
            q = os.path.abspath(os.path.join(root, os.path.relpath(p, src)))[:-1]
            os.makedirs(os.path.dirname(q), exist_ok=True)
            try:
                text = decompile(open(p, "rb").read())
                ok += 1
            except (ValueError, IndexError, struct.error) as e:
                text = f"# decompile failed: {e}\n"
                failed += 1
            with open(q, "w", encoding="utf-8") as f:
                f.write(text)
    return ok, failed
