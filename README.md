# MUSICUS Translation Tools

Tools for extracting and reinserting dialogue from **MUSICUS** (Overdrive, 2019). The game uses a proprietary archive format (YOX) that stores script files in compressed `.dat` archives.

There are two tools here that work in sequence.

## Tools

| Tool | Purpose |
|---|---|
| `musicus_tool.py` | Unpack/repack `.dat` archives into individual entry files (`.dec`, `.bin`) |
| `yox_tool.py` | Parse `.dec` script files, extract dialogue strings to JSON, repack after editing |

**Supported archives:** `script_en.dat`, `ui_en.dat`, `config_en.dat`, `font_en.dat`

## Requirements

Python 3.9 or newer. No external dependencies.

## Full Workflow

### Step 1 — Unpack the `.dat` archive

```bash
python musicus_tool.py unpack script_en.dat ./script_en/
```

This produces a folder containing:
- `.dec` files — decompressed script entries (these hold the dialogue)
- `.bin` files — raw binary entries that are not text
- `manifest.json` — archive metadata required for repacking

**Do not delete `.bin` files or `manifest.json`.** They are needed to rebuild the archive correctly.

### Step 2 — Extract strings from `.dec` files

```bash
python yox_tool.py extract_all ./script_en/ ./json_folder/
```

Each `.dec` with dialogue produces a corresponding `.json` in `./json_folder/`. Files with no dialogue are skipped.

You can also inspect a single file:

```bash
python yox_tool.py info ./script_en/0049.dec
```

### Step 3 — Translate

Open the JSON files in any editor. Each dialogue entry looks like this:

```json
{
  "index": 7,
  "offset": 16,
  "original": " The white-lace curtains swayed gently in the wind...@I@L The smell of toast...@I@P",
  "translation": "",
  "is_dialogue": true,
  "note": ""
}
```

Fill in the `"translation"` field. Leave it empty (`""`) if you want the original to be used as-is.

**Preserve the inline tags** — they control text rendering and must stay in the translation:

| Tag | Function |
|---|---|
| `@I@L` | Line break within a box |
| `@I@P` | Clear box and wait for input |
| `@I@K` | Clear box immediately |
| `@K@P` | Clear then wait |

### Step 4 — Repack `.dec` files

```bash
python yox_tool.py pack_all ./script_en/ ./json_folder/ ./script_en/
```

> **Important:** output the repacked `.dec` files back into the **same folder where you unpacked** (e.g. `./script_en/`). The `manifest.json` in that folder is required for the next step. If you repack to a different folder without `manifest.json`, the archive rebuild will fail.

### Step 5 — Rebuild the `.dat` archive

```bash
python musicus_tool.py repack ./script_en/ script_en_patched.dat
```

Replace the original `script_en.dat` in the game directory with `script_en_patched.dat`.

## Rendering Note

Translated text only renders correctly when the game engine loads the script fresh. **Do not load a save that was made mid-scene.** If you save at a line, then change that line's translation, loading that save will still show the old text. To see updated text, either:

- Start a new game from the main menu, or
- Load a save from **before** the changed line was first displayed

## Proof of concept

| Result |
|:---:|
| ![Translation working in-game](https://i.imgur.com/Poc2KYX.png) |
