"""One-step recovery: turn an exported pack (or an exe with an embedded pack) into a folder
you can browse and grep.

* every file is extracted;
* ``.gdc`` scripts are decompiled to ``.gd``;
* binary resources (``.res``, ``.scn``) get a ``.json`` dump beside them;
* ``project.binary`` becomes ``project_settings.txt``;
* ``*.remap`` files (which point an original path at its exported form) are followed, so
  recovered scripts and resources also appear under their original names;
* ``RECOVERY.md`` lists what was done and anything that failed.

    godot-unpack recover game.exe out_dir
"""
from __future__ import annotations

import json
import os
import re

from . import gdscript, pck, resource, variant

_REMAP_PATH = re.compile(r'^path="(res://[^"]+)"', re.M)


def _safe_join(root, res_path):
    dst = os.path.abspath(os.path.join(root, res_path.replace("res://", "").lstrip("/")))
    if not dst.startswith(root + os.sep):
        raise ValueError(f"unsafe path: {res_path!r}")
    return dst


def recover(pack_path, out_dir) -> dict:
    root = os.path.abspath(out_dir)
    n, skipped = pck.extract(pack_path, root)
    report = {"extracted": n, "encrypted_skipped": skipped, "scripts": 0, "resources": 0,
              "remapped": 0, "failures": []}
    converted = {}   # res:// path of an exported file -> path of its readable form
    for base, _, files in os.walk(root):
        for fn in files:
            p = os.path.join(base, fn)
            res = "res://" + os.path.relpath(p, root).replace(os.sep, "/")
            try:
                if fn.endswith(".gdc"):
                    out = p[:-1]
                    with open(p, "rb") as f:
                        text = gdscript.decompile(f.read())
                    with open(out, "w", encoding="utf-8") as f:
                        f.write(text)
                    converted[res] = out
                    report["scripts"] += 1
                elif fn.endswith((".res", ".scn")):
                    with open(p, "rb") as f:
                        data = resource.parse(f.read())
                    with open(p + ".json", "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=1, default=str)
                    converted[res] = p + ".json"
                    report["resources"] += 1
                elif fn == "project.binary":
                    with open(p, "rb") as f:
                        settings = variant.read_project_binary(f.read())
                    with open(os.path.join(base, "project_settings.txt"), "w", encoding="utf-8") as f:
                        f.writelines(f"{k} = {v}\n" for k, v in settings.items())
            except (ValueError, RuntimeError, IndexError) as e:
                report["failures"].append(f"{res}: {e}")
    for base, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".remap"):
                continue
            with open(os.path.join(base, fn), encoding="utf-8", errors="replace") as f:
                m = _REMAP_PATH.search(f.read())
            if not m or m.group(1) not in converted:
                continue
            src = converted[m.group(1)]
            original = os.path.join(base, fn[:-len(".remap")])
            dst = original if src.endswith(".gd") else original + ".json"
            try:
                dst = _safe_join(root, "res://" + os.path.relpath(dst, root).replace(os.sep, "/"))
            except ValueError as e:
                report["failures"].append(str(e))
                continue
            if not os.path.exists(dst):
                with open(src, "rb") as a, open(dst, "wb") as b:
                    b.write(a.read())
                report["remapped"] += 1
    with open(os.path.join(root, "RECOVERY.md"), "w", encoding="utf-8") as f:
        f.write(f"# Recovered from {os.path.basename(pack_path)}\n\n")
        for k in ("extracted", "encrypted_skipped", "scripts", "resources", "remapped"):
            f.write(f"- {k.replace('_', ' ')}: {report[k]}\n")
        if report["failures"]:
            f.write("\n## Failures\n\n" + "".join(f"- {x}\n" for x in report["failures"]))
    return report
