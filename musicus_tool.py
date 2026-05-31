#!/usr/bin/env python3
"""
MUSICUS YOX Archive Tool
Unpack and repack .dat files from the game MUSICUS (KiriKiri/YOX format).

Supported archives: config_en.dat, font_en.dat, script_en.dat, ui_en.dat

Usage:
  python musicus_tool.py unpack <file.dat> <output_dir>
  python musicus_tool.py repack <input_dir>  <file.dat>
  python musicus_tool.py list   <file.dat>
"""

import struct
import zlib
import json
import os
import sys
import argparse
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
MAGIC        = b"YOX\x00"
HEADER_SIZE  = 0x20          # outer archive header block (padded to DATA_ALIGN)
DATA_ALIGN   = 0x800         # first entry always starts here
ENTRY_ALIGN  = 0x20          # sub-entry alignment (32 bytes)

ENTRY_TYPE_RAW     = 0x00000000   # raw uncompressed data
ENTRY_TYPE_ZLIB    = 0x00000002   # zlib-compressed data
ENTRY_TYPE_CONFIG  = 0x00000200   # raw config/binary blob (no inner compression)
ENTRY_TYPE_NESTED  = 0x01000000   # nested YOX archive

SUBHDR_SIZE = 16  # inner YOX sub-header: magic(4) + type(4) + decomp_sz(4) + pad(4)

# ──────────────────────────────────────────────────────────────────────────────
# Low-level helpers
# ──────────────────────────────────────────────────────────────────────────────
def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)

def read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]

def pack_u32(value: int) -> bytes:
    return struct.pack("<I", value)

def read_sub_header(data: bytes, offset: int) -> dict | None:
    """Parse the 16-byte inner YOX sub-file header at *offset*."""
    if data[offset:offset+4] != MAGIC:
        return None
    return {
        "type":     read_u32(data, offset + 4),
        "decomp_sz": read_u32(data, offset + 8),
        "pad":      read_u32(data, offset + 12),
    }

# ──────────────────────────────────────────────────────────────────────────────
# Archive parser
# ──────────────────────────────────────────────────────────────────────────────
def parse_archive(data: bytes) -> dict:
    """Return the parsed archive structure as a dict."""
    if data[:4] != MAGIC:
        raise ValueError(f"Not a YOX archive (magic mismatch)")

    outer_flags  = read_u32(data, 4)
    table_offset = read_u32(data, 8)
    num_files    = read_u32(data, 12)

    # Bytes [0x10..0x1F]: timestamp / version fields
    raw_header_extra = data[0x10:0x20]

    entries = []
    for i in range(num_files):
        toff      = table_offset + i * 16
        e_offset  = read_u32(data, toff)
        e_packed  = read_u32(data, toff + 4)
        e_hint    = read_u32(data, toff + 8)   # 0xFFFFFFFF or 0x9 etc.
        e_pad     = read_u32(data, toff + 12)

        # Parse the inner sub-header (always starts with YOX magic)
        sub_hdr = read_sub_header(data, e_offset)
        if sub_hdr is None:
            raise ValueError(f"Entry {i}: missing inner YOX magic at {e_offset:#x}")

        inner_type    = sub_hdr["type"]
        inner_decomp  = sub_hdr["decomp_sz"]
        raw_payload   = data[e_offset + SUBHDR_SIZE : e_offset + e_packed]

        entries.append({
            "index":       i,
            "offset":      e_offset,
            "packed_size": e_packed,
            "hint":        e_hint,
            "inner_type":  inner_type,
            "decomp_size": inner_decomp,
            "_payload":    raw_payload,   # compressed/raw bytes (no sub-header)
        })

    return {
        "outer_flags":       outer_flags,
        "table_offset":      table_offset,
        "num_files":         num_files,
        "raw_header_extra":  raw_header_extra,
        "entries":           entries,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Unpack
# ──────────────────────────────────────────────────────────────────────────────
def unpack(dat_path: str, out_dir: str) -> None:
    dat_path = Path(dat_path)
    out_dir  = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[unpack] Reading {dat_path} …")
    data    = dat_path.read_bytes()
    archive = parse_archive(data)

    manifest_entries = []
    ok = err = 0

    for e in archive["entries"]:
        idx       = e["index"]
        itype     = e["inner_type"]
        payload   = e["_payload"]
        fname_raw = f"{idx:04d}.bin"
        fname_dec = f"{idx:04d}.dec"

        meta = {
            "index":       idx,
            "hint":        e["hint"],
            "inner_type":  itype,
            "inner_type_name": _type_name(itype),
            "decomp_size": e["decomp_size"],
        }

        if itype == ENTRY_TYPE_ZLIB:
            # Decompress and save the actual content
            try:
                content = zlib.decompress(payload)
            except zlib.error as ex:
                print(f"  [!] Entry {idx:04d}: zlib error ({ex}), saving raw payload")
                (out_dir / fname_raw).write_bytes(payload)
                meta["file"]      = fname_raw
                meta["is_raw"]    = True
                err += 1
                manifest_entries.append(meta)
                continue

            (out_dir / fname_dec).write_bytes(content)
            meta["file"]      = fname_dec
            meta["is_raw"]    = False
            meta["orig_compressed_size"] = len(payload)
            ok += 1
            print(f"  [{idx:04d}] zlib  {len(payload):>8,} → {len(content):>8,} bytes → {fname_dec}")

        else:
            # Raw / config / nested archive — save the whole entry (sub-header + payload) verbatim
            full_entry = (MAGIC
                          + pack_u32(itype)
                          + pack_u32(e["decomp_size"])
                          + pack_u32(0)
                          + payload)
            (out_dir / fname_raw).write_bytes(full_entry)
            meta["file"]   = fname_raw
            meta["is_raw"] = True
            ok += 1
            print(f"  [{idx:04d}] raw   {len(full_entry):>8,} bytes          → {fname_raw}  (type={_type_name(itype)})")

        manifest_entries.append(meta)

    # Save manifest
    manifest = {
        "source_file":    dat_path.name,
        "outer_flags":    archive["outer_flags"],
        "raw_header_extra": archive["raw_header_extra"].hex(),
        "num_files":      archive["num_files"],
        "entries":        manifest_entries,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(f"\n[unpack] Done. {ok} OK, {err} errors. Manifest: {manifest_path}")

def _type_name(t: int) -> str:
    return {
        ENTRY_TYPE_RAW:    "raw",
        ENTRY_TYPE_ZLIB:   "zlib",
        ENTRY_TYPE_CONFIG: "config",
        ENTRY_TYPE_NESTED: "nested-yox",
    }.get(t, f"unknown-{t:#010x}")

# ──────────────────────────────────────────────────────────────────────────────
# Repack
# ──────────────────────────────────────────────────────────────────────────────
def repack(in_dir: str, out_path: str) -> None:
    in_dir   = Path(in_dir)
    out_path = Path(out_path)

    manifest_path = in_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.json not found in {in_dir}")

    manifest = json.loads(manifest_path.read_text())
    entries  = manifest["entries"]
    num_files = manifest["num_files"]

    print(f"[repack] Packing {num_files} entries into {out_path} …")

    # ── Step 1: build each sub-file blob ──────────────────────────────────────
    blobs = []   # list of (inner_type, full_entry_bytes)
    for meta in entries:
        idx      = meta["index"]
        itype    = meta["inner_type"]
        fpath    = in_dir / meta["file"]

        if not fpath.exists():
            raise FileNotFoundError(f"Entry {idx}: file not found: {fpath}")

        content = fpath.read_bytes()

        if meta.get("is_raw", True):
            # File was saved with its sub-header intact; use verbatim
            if content[:4] != MAGIC:
                raise ValueError(f"Entry {idx}: raw file missing YOX magic")
            blob = content                # includes sub-header
        else:
            # File was saved as decompressed content; re-compress
            compressed = zlib.compress(content, level=6)
            blob = (MAGIC
                    + pack_u32(ENTRY_TYPE_ZLIB)
                    + pack_u32(len(content))   # decompressed size
                    + pack_u32(0)
                    + compressed)

        blobs.append(blob)
        verb = "re-zlib" if not meta.get("is_raw") else "verbatim"
        print(f"  [{idx:04d}] {verb:>9}  {len(blob):>8,} bytes")

    # ── Step 2: assign offsets ─────────────────────────────────────────────────
    # First entry always starts at DATA_ALIGN (0x800).
    # Subsequent entries are packed with ENTRY_ALIGN (32-byte) padding.
    offsets = []
    cursor  = DATA_ALIGN
    for blob in blobs:
        offsets.append(cursor)
        cursor = align_up(cursor + len(blob), ENTRY_ALIGN)

    # ── Step 3: file table offset (right after last blob, align to ENTRY_ALIGN) ─
    table_offset = cursor

    # ── Step 4: build binary ───────────────────────────────────────────────────
    # Outer header (0x20 bytes)
    outer_flags      = manifest["outer_flags"]
    raw_hdr_extra    = bytes.fromhex(manifest["raw_header_extra"])
    outer_header = (MAGIC
                    + pack_u32(outer_flags)
                    + pack_u32(table_offset)
                    + pack_u32(num_files)
                    + raw_hdr_extra)          # bytes [0x10..0x1F]
    assert len(outer_header) == HEADER_SIZE

    # Allocate buffer
    # Table size: num_files * 16 bytes
    table_size    = num_files * 16
    total_size    = table_offset + table_size
    buf           = bytearray(total_size)

    # Write outer header
    buf[:HEADER_SIZE] = outer_header

    # Write blobs
    for i, (blob, off) in enumerate(zip(blobs, offsets)):
        buf[off : off + len(blob)] = blob

    # Write file table
    for i, (meta, blob, off) in enumerate(zip(entries, blobs, offsets)):
        toff = table_offset + i * 16
        packed_size = len(blob)
        hint        = meta.get("hint", 0)
        struct.pack_into("<IIII", buf, toff, off, packed_size, hint, 0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(buf))
    print(f"\n[repack] Done. Written {len(buf):,} bytes to {out_path}")

# ──────────────────────────────────────────────────────────────────────────────
# List
# ──────────────────────────────────────────────────────────────────────────────
def list_archive(dat_path: str) -> None:
    data    = Path(dat_path).read_bytes()
    archive = parse_archive(data)

    print(f"Archive: {dat_path}")
    print(f"  Files  : {archive['num_files']}")
    print(f"  Table  : {archive['table_offset']:#010x}")
    print(f"  Flags  : {archive['outer_flags']:#010x}")
    print()
    print(f"{'#':>5}  {'Offset':>12}  {'Packed':>10}  {'Decomp':>10}  {'Type'}")
    print("-" * 65)
    for e in archive["entries"]:
        iname = _type_name(e["inner_type"])
        decomp = f"{e['decomp_size']:>10,}" if e["inner_type"] == ENTRY_TYPE_ZLIB else "          -"
        print(f"{e['index']:5d}  {e['offset']:#012x}  {e['packed_size']:>10,}  {decomp}  {iname}")

# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="MUSICUS YOX archive tool — unpack / repack / list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python musicus_tool.py unpack script_en.dat  ./script_en/
  python musicus_tool.py repack ./script_en/   script_en_new.dat
  python musicus_tool.py list   ui_en.dat
""")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_unpack = sub.add_parser("unpack", help="Extract all entries from a .dat archive")
    p_unpack.add_argument("dat",     help="Source .dat file")
    p_unpack.add_argument("out_dir", help="Output directory")

    p_repack = sub.add_parser("repack", help="Rebuild a .dat archive from an unpacked directory")
    p_repack.add_argument("in_dir",  help="Directory produced by 'unpack'")
    p_repack.add_argument("out_dat", help="Destination .dat file")

    p_list = sub.add_parser("list", help="List entries in a .dat archive")
    p_list.add_argument("dat", help=".dat file to inspect")

    args = parser.parse_args()

    if args.cmd == "unpack":
        unpack(args.dat, args.out_dir)
    elif args.cmd == "repack":
        repack(args.in_dir, args.out_dat)
    elif args.cmd == "list":
        list_archive(args.dat)

if __name__ == "__main__":
    main()
