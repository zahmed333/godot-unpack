import io
import json
import struct

from test_gdscript import SCRIPT, build
from test_godot import make_pck, make_res

from godot_unpack import pck
from godot_unpack.__main__ import main
from godot_unpack.recover import recover


def embed(pack):
    return b"MZ fake executable" + b"\0" * 100 + pack + struct.pack("<Q", len(pack)) + b"GDPC"


def project():
    remap = b'[remap]\n\npath="res://player.gdc"\n'
    settings = b"ECFG" + struct.pack("<I", 1) + struct.pack("<I", 4) + b"name" + struct.pack("<I", 12) + \
        struct.pack("<II", 4, 4) + b"Demo"
    return [("res://player.gdc", build(SCRIPT)), ("res://player.gd.remap", remap),
            ("res://.godot/exported/133200997/export-1f2e-unit.res", make_res()),
            ("res://data/unit.tres.remap", b'[remap]\n\npath="res://.godot/exported/133200997/export-1f2e-unit.res"\n'),
            ("res://project.binary", settings)]


def test_embedded_pack(tmp_path):
    for fmt in (1, 2, 3):
        raw = make_pck(project(), fmt)
        exe = tmp_path / f"game{fmt}.exe"
        exe.write_bytes(embed(raw))
        f, p = pck.open_pack(exe)
        with f:
            assert [pck.read_file(f, e) for e in p.entries] == [d for _, d in project()]


def test_not_a_pack(tmp_path):
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"MZ" + b"\0" * 64)
    assert main(["info", str(bad)]) == 1


def test_summary_and_diff():
    a = pck.read_pack(io.BytesIO(make_pck(project())))
    changed = [(p, d + b"!" if p.endswith(".res") else d) for p, d in project()[1:]] + [("res://new.txt", b"n")]
    b = pck.read_pack(io.BytesIO(make_pck(changed)))
    s = pck.summary(a)
    assert s["files"] == 5 and s["scripts"] == "binary tokens (.gdc)" and s["has_project_settings"]
    assert pck.diff(a, b) == {"added": ["res://new.txt"], "removed": ["res://player.gdc"], "changed": ["res://.godot/exported/133200997/export-1f2e-unit.res"]}


def test_recover(tmp_path):
    exe = tmp_path / "game.exe"
    exe.write_bytes(embed(make_pck(project(), 3)))
    out = tmp_path / "out"
    r = recover(exe, out)
    assert r["scripts"] == 1 and r["resources"] == 1 and r["remapped"] == 1 and not r["failures"]
    assert (out / "player.gd").read_text(encoding="utf-8").startswith("extends Node")
    assert json.loads((out / "data/unit.tres.json").read_text())["resources"][1]["props"]["name"] == "Ember Pup"
    assert (out / "project_settings.txt").read_text().strip() == "name = Demo"
    assert (out / "RECOVERY.md").exists()


def test_cli(tmp_path, capsys):
    p = tmp_path / "g.pck"
    p.write_bytes(make_pck(project()))
    assert main(["info", str(p)]) == 0
    assert "5 files" in capsys.readouterr().out
    assert main(["list", str(p), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 5
    assert main(["diff", str(p), str(p)]) == 0
    assert "0 added, 0 removed, 0 changed" in capsys.readouterr().out
