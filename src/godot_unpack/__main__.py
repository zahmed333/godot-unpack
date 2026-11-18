"""godot-unpack: read what a Godot export ships.

    godot-unpack info     <game.pck | game.exe>
    godot-unpack list     <pack> [substring] [--json]
    godot-unpack extract  <pack> <out_dir> [substring ...]
    godot-unpack recover  <pack> <out_dir>        extract + decompile + dump, in one step
    godot-unpack diff     <old pack> <new pack>   what changed between two builds
    godot-unpack gdc      <file.gdc | dir> [out_dir]
    godot-unpack res      <file.res | dir> [out.json]
    godot-unpack settings <project.binary>
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, gdscript, pck, resource, variant
from .recover import recover


def _size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def cmd_info(a):
    f, p = pck.open_pack(a.pack)
    f.close()
    s = pck.summary(p)
    if a.json:
        return print(json.dumps(s, indent=1))
    print(f"pack format {s['format']}, Godot {s['godot']}: {s['files']} files, {_size(s['bytes'])}")
    print(f"scripts: {s['scripts']}; project settings: {'yes' if s['has_project_settings'] else 'no'}"
          + (f"; {s['encrypted_files']} encrypted files" if s["encrypted_files"] else ""))
    for ext, (n, size) in list(s["by_extension"].items())[:15]:
        print(f"  {ext:12} {n:6} files  {_size(size):>10}")


def cmd_list(a):
    f, p = pck.open_pack(a.pack)
    f.close()
    rows = [e for e in p.entries if not a.substring or a.substring in e.path]
    if a.json:
        return print(json.dumps([{"path": e.path, "size": e.size, "md5": e.md5.hex(), "encrypted": e.encrypted}
                                 for e in rows], indent=1))
    for e in rows:
        print(f"{e.size:>10} {'E' if e.encrypted else ' '} {e.path}")


def cmd_extract(a):
    n, skipped = pck.extract(a.pack, a.out, a.substrings)
    print(f"extracted {n} files" + (f", skipped {skipped} encrypted" if skipped else ""))


def cmd_recover(a):
    r = recover(a.pack, a.out)
    print(f"extracted {r['extracted']} files, decompiled {r['scripts']} scripts, dumped {r['resources']} resources, "
          f"restored {r['remapped']} original names; {len(r['failures'])} failures. See {os.path.join(a.out, 'RECOVERY.md')}")


def cmd_diff(a):
    fa, pa = pck.open_pack(a.old)
    fb, pb = pck.open_pack(a.new)
    fa.close()
    fb.close()
    d = pck.diff(pa, pb)
    if a.json:
        return print(json.dumps(d, indent=1))
    for key, mark in (("added", "+"), ("removed", "-"), ("changed", "~")):
        for p in d[key]:
            print(f"{mark} {p}")
    print(f"{len(d['added'])} added, {len(d['removed'])} removed, {len(d['changed'])} changed")


def cmd_gdc(a):
    if os.path.isfile(a.path):
        with open(a.path, "rb") as f:
            sys.stdout.write(gdscript.decompile(f.read()))
    else:
        ok, failed = gdscript.decompile_tree(a.path, a.out or a.path.rstrip("/\\") + "_gd")
        print(f"decompiled {ok} scripts, {failed} failed")


def cmd_res(a):
    paths = [a.path] if os.path.isfile(a.path) else [
        os.path.join(root, f) for root, _, fs in os.walk(a.path) for f in sorted(fs) if f.endswith((".res", ".scn"))]
    out, errors = {}, 0
    for p in paths:
        key = os.path.relpath(p, a.path) if len(paths) > 1 else p
        try:
            with open(p, "rb") as f:
                out[key] = resource.parse(f.read())
        except ValueError as e:
            out[key], errors = {"error": str(e)}, errors + 1
    text = json.dumps(out if len(paths) > 1 else next(iter(out.values()), {}), indent=1, default=str)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{len(paths)} files, {errors} errors -> {a.out}")
    else:
        print(text)


def cmd_settings(a):
    with open(a.path, "rb") as f:
        for k, v in variant.read_project_binary(f.read()).items():
            print(f"{k} = {v}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="godot-unpack", description="Read what a Godot export ships.")
    ap.add_argument("--version", action="version", version=f"godot-unpack {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        return p
    p = add("info", cmd_info, "summary of a .pck or an exe with an embedded pack")
    p.add_argument("pack")
    p.add_argument("--json", action="store_true")
    p = add("list", cmd_list, "list files in a pack")
    p.add_argument("pack")
    p.add_argument("substring", nargs="?")
    p.add_argument("--json", action="store_true")
    p = add("extract", cmd_extract, "extract files from a pack")
    p.add_argument("pack")
    p.add_argument("out")
    p.add_argument("substrings", nargs="*")
    p = add("recover", cmd_recover, "extract, decompile scripts and dump resources in one step")
    p.add_argument("pack")
    p.add_argument("out")
    p = add("diff", cmd_diff, "files added, removed and changed between two builds")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--json", action="store_true")
    p = add("gdc", cmd_gdc, "decompile .gdc scripts")
    p.add_argument("path")
    p.add_argument("out", nargs="?")
    p = add("res", cmd_res, "dump binary resources as JSON")
    p.add_argument("path")
    p.add_argument("out", nargs="?")
    p = add("settings", cmd_settings, "print project.binary settings")
    p.add_argument("path")
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except (ValueError, OSError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
