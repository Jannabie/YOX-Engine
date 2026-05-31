#!/usr/bin/env python3
"""
YOX Translation Tool — untuk MUSICUS (Overdrive)
=================================================
Tool untuk extract teks dari file .dec hasil unpack YOX .dat,
lalu repack setelah ditranslasi.

Struktur file YOX .dec:
  [0x00 - 0x2F]  Header (48 bytes)
    - 0x00: Magic "YOX\0"
    - 0x04: flags (2 bytes)
    - 0x06: sub-type (2 bytes, biasanya 0x00 0x10)
    - 0x08: bytecode_size  → ukuran Section 1 (uint32 LE)
    - 0x0C: strpool_size   → ukuran Section 2 (uint32 LE)
    - 0x10: reftable_size  → ukuran Section 3 (uint32 LE)
    - 0x14: zeros (12 bytes)
    - 0x20: SYSTEMTIME (16 bytes: year, month, DOW, day, hour, min, sec, ms)

  [0x30 ...]     Section 1 — Bytecode (bytecode_size bytes)
  [...]           Section 2 — String Pool (strpool_size bytes, null-terminated strings)
  [...]           Section 3 — Reference Table (reftable_size bytes, array of uint32 offsets ke Section 2)

Cara pakai:
  # Extract teks ke JSON
  python3 yox_tool.py extract 0103.dec 0103_strings.json

  # Extract semua .dec dalam folder
  python3 yox_tool.py extract_all ./dec_folder ./json_folder

  # Repack setelah translasi
  python3 yox_tool.py pack 0103.dec 0103_strings.json 0103_patched.dec

  # Repack semua
  python3 yox_tool.py pack_all ./dec_folder ./json_folder ./out_folder
"""

import struct
import json
import os
import sys
from pathlib import Path

MAGIC = b'YOX\x00'
HEADER_SIZE = 48

# Kode khusus dalam teks — jangan ditranslasi, pertahankan apa adanya
# @I@L = line break (ganti baris)
# @I@P = page pause (tunggu klik, layar baru)
# @I@K = clear (bersihkan teks)
# @P    = pause/klik
SPECIAL_TAGS = ['@I@L', '@I@P', '@I@K', '@K@P', '@K', '@P']


# ────────────────────────────────────────────────────────
# PARSE / READ
# ────────────────────────────────────────────────────────

def parse_header(data: bytes) -> dict:
    """Baca header 48-byte YOX."""
    if data[:4] != MAGIC:
        raise ValueError(f"Bukan file YOX valid (magic salah: {data[:4]!r})")
    flags        = struct.unpack_from('<H', data, 4)[0]
    sub_type     = struct.unpack_from('<H', data, 6)[0]
    bytecode_sz  = struct.unpack_from('<I', data, 8)[0]
    strpool_sz   = struct.unpack_from('<I', data, 12)[0]
    reftable_sz  = struct.unpack_from('<I', data, 16)[0]
    # SYSTEMTIME di offset 32
    year, month, dow, day, hour, minute, second, ms = struct.unpack_from('<8H', data, 32)
    return {
        'flags':        flags,
        'sub_type':     sub_type,
        'bytecode_sz':  bytecode_sz,
        'strpool_sz':   strpool_sz,
        'reftable_sz':  reftable_sz,
        'timestamp': {
            'year': year, 'month': month, 'day': day,
            'hour': hour, 'minute': minute, 'second': second
        }
    }


def parse_sections(data: bytes, hdr: dict) -> tuple:
    """Kembalikan (bytecode_bytes, pool_bytes, ref_offsets_list)."""
    sec1_start = HEADER_SIZE
    sec1_end   = sec1_start + hdr['bytecode_sz']
    sec2_start = sec1_end
    sec2_end   = sec2_start + hdr['strpool_sz']
    sec3_start = sec2_end
    sec3_end   = sec3_start + hdr['reftable_sz']

    bytecode = data[sec1_start:sec1_end]
    pool     = data[sec2_start:sec2_end]
    ref_raw  = data[sec3_start:sec3_end]

    n_refs   = len(ref_raw) // 4
    refs     = list(struct.unpack_from(f'<{n_refs}I', ref_raw)) if n_refs else []
    return bytecode, pool, refs


def pool_to_strings(pool: bytes) -> dict:
    """Konversi byte string pool ke dict {byte_offset: string}."""
    result = {}
    i = 0
    while i < len(pool):
        end = pool.find(b'\x00', i)
        if end == -1:
            s = pool[i:]
            off = i
            i = len(pool)
        else:
            s = pool[i:end]
            off = i
            i = end + 1
        result[off] = s.decode('utf-8', errors='replace')
    return result


def extract_strings(dec_path: str) -> list:
    """
    Kembalikan list of dict:
      {
        "index": int,         # indeks di Reference Table
        "offset": int,        # byte offset di String Pool
        "original": str,      # teks asli
        "translation": str,   # (kosong, diisi penerjemah)
        "is_dialogue": bool   # apakah ini teks dialog/narasi
      }
    """
    data = Path(dec_path).read_bytes()
    hdr  = parse_header(data)

    if hdr['strpool_sz'] == 0:
        return []  # file ini tidak punya string pool

    bytecode, pool, refs = parse_sections(data, hdr)
    strings_at = pool_to_strings(pool)

    entries = []
    for idx, offset in enumerate(refs):
        text = strings_at.get(offset, '')
        is_dialogue = len(text) > 10 and any(c.isalpha() for c in text[:20])
        entries.append({
            "index":       idx,
            "offset":      offset,
            "original":    text,
            "translation": text if not is_dialogue else "",
            "is_dialogue": is_dialogue,
            "note":        ""  # komentar opsional buat penerjemah
        })
    return entries


# ────────────────────────────────────────────────────────
# PACK / WRITE
# ────────────────────────────────────────────────────────

def build_pool_and_refs(entries: list) -> tuple:
    """
    Buat string pool dan reference table baru dari entries.
    Kembalikan (pool_bytes, ref_table_bytes).
    Urutan string di pool mengikuti urutan entries.
    """
    pool = bytearray()
    offsets = []  # offset tiap string dalam pool baru

    # Kumpulkan semua string unik dalam urutan kemunculan pertama
    # (beberapa index mungkin menunjuk ke offset yang sama)
    offset_map = {}  # offset_lama → offset_baru

    # Sort entries by original offset agar string dibangun berurutan
    sorted_entries = sorted(entries, key=lambda e: e['offset'])

    for e in sorted_entries:
        orig_off = e['offset']
        if orig_off in offset_map:
            continue  # sudah diproses (shared string)
        
        text = e['translation'] if e['translation'] else e['original']
        encoded = text.encode('utf-8') + b'\x00'
        offset_map[orig_off] = len(pool)
        pool.extend(encoded)

    # Bangun reference table sesuai urutan index
    sorted_by_idx = sorted(entries, key=lambda e: e['index'])
    ref_list = []
    for e in sorted_by_idx:
        new_off = offset_map.get(e['offset'], 0)
        ref_list.append(new_off)

    ref_table = struct.pack(f'<{len(ref_list)}I', *ref_list)
    return bytes(pool), ref_table


def pack_file(original_dec: str, json_path: str, output_dec: str):
    """
    Baca file .dec asli + JSON translasi, hasilkan .dec baru.
    Section 1 (bytecode) tidak diubah sama sekali.
    Section 2 & 3 dibangun ulang dari data translasi.
    """
    data = Path(original_dec).read_bytes()
    hdr  = parse_header(data)

    with open(json_path, 'r', encoding='utf-8') as f:
        entries = json.load(f)

    if hdr['strpool_sz'] == 0 or not entries:
        # Tidak ada string pool → copy langsung
        Path(output_dec).write_bytes(data)
        print(f"[SKIP] {Path(original_dec).name} — tidak ada string pool, dicopy langsung.")
        return

    bytecode, _, _ = parse_sections(data, hdr)
    new_pool, new_refs = build_pool_and_refs(entries)

    # Bangun header baru
    new_hdr = bytearray(data[:HEADER_SIZE])
    struct.pack_into('<I', new_hdr, 8,  len(bytecode))
    struct.pack_into('<I', new_hdr, 12, len(new_pool))
    struct.pack_into('<I', new_hdr, 16, len(new_refs))

    result = bytes(new_hdr) + bytecode + new_pool + new_refs
    Path(output_dec).write_bytes(result)
    print(f"[OK]   {Path(original_dec).name} → {Path(output_dec).name} "
          f"({len(data)} → {len(result)} bytes)")


# ────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────

def cmd_extract(dec_path: str, json_path: str):
    entries = extract_strings(dec_path)
    if not entries:
        print(f"[SKIP] {Path(dec_path).name} — tidak ada string pool.")
        return

    translatable = sum(1 for e in entries if e['is_dialogue'])
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    print(f"[OK]   {Path(dec_path).name}: {len(entries)} string, "
          f"{translatable} butuh terjemahan → {json_path}")


def cmd_extract_all(dec_folder: str, json_folder: str):
    dec_folder = Path(dec_folder)
    json_folder = Path(json_folder)
    json_folder.mkdir(parents=True, exist_ok=True)

    dec_files = sorted(dec_folder.glob('*.dec'))
    print(f"Ditemukan {len(dec_files)} file .dec")
    for dec_file in dec_files:
        json_out = json_folder / (dec_file.stem + '.json')
        try:
            cmd_extract(str(dec_file), str(json_out))
        except Exception as e:
            print(f"[ERROR] {dec_file.name}: {e}")


def cmd_pack(original_dec: str, json_path: str, output_dec: str):
    pack_file(original_dec, json_path, output_dec)


def cmd_pack_all(dec_folder: str, json_folder: str, out_folder: str):
    dec_folder  = Path(dec_folder)
    json_folder = Path(json_folder)
    out_folder  = Path(out_folder)
    out_folder.mkdir(parents=True, exist_ok=True)

    for dec_file in sorted(dec_folder.glob('*.dec')):
        json_file = json_folder / (dec_file.stem + '.json')
        out_file  = out_folder / dec_file.name
        if json_file.exists():
            try:
                pack_file(str(dec_file), str(json_file), str(out_file))
            except Exception as e:
                print(f"[ERROR] {dec_file.name}: {e}")
        else:
            # Tidak ada translasi → copy as-is
            out_file.write_bytes(dec_file.read_bytes())
            print(f"[COPY] {dec_file.name} (tidak ada JSON translasi)")


def print_help():
    print(__doc__)


def main():
    args = sys.argv[1:]
    if not args:
        print_help()
        return

    cmd = args[0]

    if cmd == 'extract' and len(args) == 3:
        cmd_extract(args[1], args[2])

    elif cmd == 'extract_all' and len(args) == 3:
        cmd_extract_all(args[1], args[2])

    elif cmd == 'pack' and len(args) == 4:
        cmd_pack(args[1], args[2], args[3])

    elif cmd == 'pack_all' and len(args) == 4:
        cmd_pack_all(args[1], args[2], args[3])

    elif cmd == 'info' and len(args) == 2:
        # Info singkat tentang satu file
        data = Path(args[1]).read_bytes()
        hdr  = parse_header(data)
        ts   = hdr['timestamp']
        print(f"File     : {args[1]}")
        print(f"Timestamp: {ts['year']}-{ts['month']:02d}-{ts['day']:02d} "
              f"{ts['hour']:02d}:{ts['minute']:02d}:{ts['second']:02d}")
        print(f"Bytecode : {hdr['bytecode_sz']} bytes")
        print(f"StrPool  : {hdr['strpool_sz']} bytes")
        print(f"RefTable : {hdr['reftable_sz']} bytes ({hdr['reftable_sz']//4} entries)")
        if hdr['strpool_sz']:
            entries = extract_strings(args[1])
            translatable = [e for e in entries if e['is_dialogue']]
            print(f"Strings  : {len(entries)} total, {len(translatable)} teks dialog")
            print()
            print("Preview teks (5 pertama):")
            count = 0
            for e in entries:
                if e['is_dialogue']:
                    print(f"  [{e['index']:3d}] {e['original'][:100]!r}")
                    count += 1
                    if count >= 5:
                        break

    else:
        print_help()


if __name__ == '__main__':
    main()
