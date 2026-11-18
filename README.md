# godot-unpack

Pure-Python readers for the files a Godot export produces. There are no required
dependencies; `zstandard` is only needed for compressed `.gdc` scripts.

It works on standalone `.pck` files and on games exported with **Embed PCK**, where the pack
is appended to the `.exe`.

| format | what you get |
|---|---|
| `.pck` packs, format v1–v4 (Godot 3.x, 4.0–4.3, 4.4, 4.5+) | list, extract, summarise and diff, with offsets and MD5s |
| binary resources (`.res`, `.scn`, imported `*.res`) | every internal resource and property as JSON |
| `project.binary` | the exported project settings as `key = value` |
| `var_to_bytes` / `encode_variant` data | plain Python values |
| binary-token GDScript (`.gdc`, tokenizer v100/v101) | readable `.gd` source |

## Install

```bash
pip install "godot-unpack[zstd] @ git+https://github.com/zahmed333/godot-unpack"
```

## Use

```bash
godot-unpack recover MyGame.exe recovered/     # everything in one step (see below)
godot-unpack info    MyGame.pck                # format, Godot version, size by file type, script mode
godot-unpack list    MyGame.pck [substring] [--json]
godot-unpack extract MyGame.pck out/ [substring ...]
godot-unpack diff    build_1.0.pck build_1.1.pck   # added / removed / changed files between two builds
godot-unpack gdc     out/ out_gd/              # every .gdc under a folder -> .gd
godot-unpack res     out/data/ data.json       # every .res/.scn under a folder -> one JSON file
godot-unpack settings out/project.binary
```

### `recover`

This turns an export back into a folder you can browse and grep:

* every file is extracted;
* `.gdc` scripts are decompiled to `.gd`;
* binary resources get a `.json` dump beside them;
* `project.binary` becomes `project_settings.txt`;
* `*.remap` files are followed, so exported resources (`.godot/exported/...`) also show up
  under their original names, e.g. `data/unit.tres.json`;
* `RECOVERY.md` lists what was done and anything that failed.

### `diff`

`diff` is handy in CI. Run it on the last release and the new build to see exactly which
files a patch ships. It compares by MD5 where the pack stores one, and by size otherwise.

Every command is also a library call: `pck.open_pack(path)`, `pck.summary(pack)`,
`pck.diff(a, b)`, `resource.parse(bytes)`, `variant.decode_variant(bytes)`,
`gdscript.decompile(bytes)` and `recover.recover(pack, out)`.

## The GDScript decompiler

Exports using the "Binary tokens" script mode ship `.gdc` files. A `.gdc` file is a token
stream: a table of identifiers (XOR-obfuscated), a table of constants (encoded Variants), a
line map, a column map, and the tokens themselves.

The decompiler:

* reads both token encodings: 5-byte short tokens and 8-byte long tokens with a payload;
* restores `StringName` (`&"x"`) and `NodePath` (`^"x"`) literals;
* rebuilds indentation from the column map, so tab- and space-indented scripts both work;
* lines up continuation lines inside brackets.

Comments and original spacing are not in the file, so they cannot come back. Everything
else is exact.

It was checked against a real Godot 4 export (tokenizer v101), where its output matched a known-good
decompilation. It is also tested on synthetic files assembled from the format spec
([`tests/test_gdscript.py`](tests/test_gdscript.py)).

## Scope

* **Encryption.** Encrypted packs, and encrypted files inside packs, are detected and
  skipped. This tool does not decrypt, and it does not look for keys.
* **Path safety.** Extraction refuses entry paths that would escape the output folder, as a
  guard against crafted packs.
* **Intended use.** Your own projects: recovering lost source from an export, auditing in CI
  what a build ships, diffing two exports, or pulling balance data into notebooks. Respect
  the license and terms of any game you didn't make.

## License

MIT
