#!/usr/bin/env python3
"""Build the release package for the Yoshi's Cookie high-score-save hack.

Usage (patch_yoshiscookie.py must be in the same folder):
    python make_release_yoshiscookie.py "roms\\Yoshi's Cookie (U) [!].gb" --author "Your Name"
        [--version 1.0] [--repo https://github.com/<you>/<repo>]

Creates  release\\Yoshis Cookie - High-score Save v<version>.zip  containing
  - a BPS patch (recommended: checks that you have the right base ROM)
  - an IPS patch (for tools that only know IPS)
  - README.txt (with the base ROM's size, CRC32, MD5 and SHA-1)
The zip contains only what is needed to patch the game: neither the ROM nor the
Python scripts are included (the scripts belong in the repository).
Also writes release\\README.md, the same information as a GitHub README.
Both patches are applied again to the original and compared with the real output
before the zip is written. (File names leave out the apostrophe of the title.)
"""
import argparse
import datetime
import hashlib
import shutil
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import patch_yoshiscookie as pb  # noqa: E402

NAME = "Yoshi's Cookie - High-score Save"
FILE_NAME = "Yoshis Cookie - High-score Save"      # no apostrophe in file names
# Edit this to name the hardware you tested on (it appears in both READMEs):
TESTED_ON = ("Tested in BGB and on real hardware, on cartridges with and without save RAM: saving, loading,\n"
             "high-score reset and soft resets. With save RAM the high-score is kept; without it the game\n"
             "plays normally and the high-score is not kept.")


# ---------------------------------------------------------------- IPS
def make_ips(src, dst, gap=6):
    assert len(src) == len(dst) <= 0x1000000
    runs, i, n = [], 0, len(dst)
    while i < n:
        if src[i] == dst[i]:
            i += 1
            continue
        j = i + 1
        last = i                      # last differing byte seen
        while j < n and j - last <= gap:
            if src[j] != dst[j]:
                last = j
            j += 1
        runs.append((i, last + 1))
        i = last + 1
    out = bytearray(b"PATCH")
    for a, b in runs:
        while a < b:
            size = min(b - a, 0xFFFF)
            assert a != 0x454F46, "offset collides with the IPS 'EOF' marker"
            out += a.to_bytes(3, "big") + size.to_bytes(2, "big") + dst[a:a + size]
            a += size
    return bytes(out + b"EOF")


def apply_ips(src, patch):
    assert patch[:5] == b"PATCH" and patch[-3:] == b"EOF"
    out, p = bytearray(src), 5
    while patch[p:p + 3] != b"EOF" or p + 3 != len(patch):
        off = int.from_bytes(patch[p:p + 3], "big")
        size = int.from_bytes(patch[p + 3:p + 5], "big")
        p += 5
        if size == 0:                                   # RLE record
            run = int.from_bytes(patch[p:p + 2], "big")
            out[off:off + run] = bytes([patch[p + 2]]) * run
            p += 3
        else:
            out[off:off + size] = patch[p:p + size]
            p += size
    return bytes(out)


# ---------------------------------------------------------------- BPS
def bps_num(n):
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            return bytes(out)
        out.append(x)
        n -= 1


def make_bps(src, dst, metadata=b""):
    assert len(src) == len(dst)
    out = bytearray(b"BPS1")
    out += bps_num(len(src)) + bps_num(len(dst)) + bps_num(len(metadata)) + metadata
    i, n = 0, len(dst)
    while i < n:
        j = i
        same = src[i] == dst[i]
        while j < n and (src[j] == dst[j]) == same:
            j += 1
        length = j - i
        if same:                                        # SourceRead
            out += bps_num(((length - 1) << 2) | 0)
        else:                                           # TargetRead
            out += bps_num(((length - 1) << 2) | 1) + dst[i:j]
        i = j
    out += zlib.crc32(src).to_bytes(4, "little") + zlib.crc32(dst).to_bytes(4, "little")
    out += zlib.crc32(out).to_bytes(4, "little")
    return bytes(out)


def apply_bps(src, patch):
    assert patch[:4] == b"BPS1"
    assert zlib.crc32(patch[:-4]) == int.from_bytes(patch[-4:], "little"), "patch CRC"
    assert zlib.crc32(src) == int.from_bytes(patch[-12:-8], "little"), "wrong base ROM"

    def num(p):
        data, shift = 0, 1
        while True:
            x = patch[p]
            p += 1
            data += (x & 0x7F) * shift
            if x & 0x80:
                return data, p
            shift <<= 7
            data += shift

    p = 4
    ssize, p = num(p)
    tsize, p = num(p)
    msize, p = num(p)
    p += msize
    out = bytearray()
    while p < len(patch) - 12:
        data, p = num(p)
        cmd, length = data & 3, (data >> 2) + 1
        if cmd == 0:
            out += src[len(out):len(out) + length]
        elif cmd == 1:
            out += patch[p:p + length]
            p += length
        else:
            raise NotImplementedError("this encoder only emits SourceRead/TargetRead")
    assert len(out) == tsize
    assert zlib.crc32(out) == int.from_bytes(patch[-8:-4], "little"), "target CRC"
    return bytes(out)


# ---------------------------------------------------------------- README
README = """\
{name}
Version {version}  -  by {author}  -  {date}
==============================================================================

DESCRIPTION
  Adds saving of the high-score to Yoshi's Cookie (Game Boy).
  The game itself is unchanged: no new screens, no new menus. The high-score you
  saw last time is simply still there the next time you play, instead of being
  reset to 0 every time the console is switched on. It also survives the game's
  soft reset, which wiped it in the original game.

BASE ROM
  The patch applies to this exact file and no other:
      Name  : {rom_name}
      Size  : {size} bytes
      CRC32 : {crc32}
      MD5   : {md5}
      SHA-1 : {sha1}
  Check your copy before patching. A different dump (or one with a header) will
  not work. The BPS patch refuses to apply to a wrong ROM; the IPS patch cannot
  tell and would produce a broken game.

PATCHING
  Use the online patcher at https://www.romhacking.net/patch/ (it works in your
  browser, so there is nothing to install) or any other IPS/BPS patcher. Apply
  the BPS patch if you can (it verifies the base ROM); the IPS patch is identical
  in effect. Keep your original ROM; the patcher gives you a new file.

REQUIREMENTS
  Saving needs cartridge RAM that keeps its contents when the power is off:
    - Emulators: any emulator that supports MBC1 + RAM + battery. A .sav file is
      created next to the ROM (BGB, SameBoy, mGBA, etc.).
    - Real hardware: a flash cart or rewritable cartridge that supports MBC1 with
      8 KB of save RAM. Battery-backed RAM and non-volatile FRAM both qualify, for
      example the DMG-GBWR-50 rewritable cartridge.
  On hardware without save RAM the game still plays normally, but, as in the
  original game, the high-score is lost when the console is switched off or
  soft-reset.

HOW IT WORKS FOR THE PLAYER
  - Nothing to do. The high-score is stored automatically whenever it changes.
  - With save RAM, a soft reset no longer loses the high-score (it did in the
    original game).
  - To reset the high-score: switch the console on while holding DOWN + SELECT.
    The screen stays white while any button is held (that is the game waiting for
    all buttons to be released, exactly as in the original); keep them held for a
    moment, then release them. The high-score is back to 0.

KNOWN LIMITATIONS
  - Only the high-score is saved; nothing else.
  - A save that is damaged (wrong check byte, or digits that are not valid
    decimal digits) is ignored and the game starts with a high-score of 0.

TECHNICAL NOTES
  Changes to the ROM (all in unused padding at the end of bank 0 or at hook points):
    Header          0x147 01 -> 03 (MBC1+RAM+BATTERY), 0x149 00 -> 02 (8 KB RAM),
                    header and global checksums recalculated.
    0x0163          the boot's single clear of $8000-$DFFB replaced by
                    "call ${clear_addr:04X}", which clears $8000-$9FFF and $C000-$DFFB
                    and leaves $A000-$BFFF (the cartridge RAM) alone.
    0x0178          "jp nz,$0150" in the game's wait-until-no-button-is-held loop
                    replaced by "jp nz,${hold_addr:04X}" (reset combination).
    0x0192          "ld [$C13C],a" replaced by "call ${load_addr:04X}" (boot hook; the
                    replaced instruction is the first thing the new code runs).
    bank 1 0x5965   "scf ; ccf ; ret" at the end of the game's high-score copy
                    routine replaced by "jp ${tail_addr:04X}" (save hook).
    0x{save_addr:04X}-0x{end_addr:04X}  the new code, {total_len} bytes (save {save_len}, joypad check {combo_len},
                    load {load_len}, reset hook {hold_len}, clear {clear_len}, save hook {tail_len}), in the zero
                    padding at the end of bank 0.
  Game RAM:   score = $C16D-$C174, high-score = $C176-$C17D: eight bytes, one
              decimal digit per byte, least significant digit first ($C176 is the
              hidden units digit, always 0).
  Save RAM at $A000: the eight high-score bytes + check byte ($A5 + their sum).
  Load runs once per boot, right after the game sets its default high-score (0),
  so a valid save replaces the default and a missing or damaged save leaves it
  alone. It ignores records with a bad check byte or with a non-digit among the
  seven digit bytes.
  Save runs only when the game itself updates the high-score: the routine in bank 1
  at $5943 compares the score with the high-score from the top digit down and, if
  higher, copies it with a small loop. The end of that routine now calls the save
  routine, then returns exactly as the game did (carry clear means "updated").
  Nothing runs every frame and no interrupt is hooked.
  The game's boot code ($0175-$0178) reads the joypad and restarts until no button is
  held, so the reset combination is detected inside that loop: with Down + Select
  held, the default high-score is written to the save; when the buttons are released
  the game continues and the load routine restores it.
  The game's soft reset re-runs the boot code, which wipes all RAM; the load routine
  runs again then, which is why the saved high-score now survives it.
  If you have Python, you can try this yourself using the scripts from the
  repository{repo_note}:
  {patcher_script} rebuilds the patched ROM from your original ROM, and
  {release_script} builds the patches, READMEs and zip.

TESTING
  {tested_on}

VERSION HISTORY
  {version}  Initial release.

LEGAL
  This package contains no game data, only the differences. You need your own
  copy of the original game. Yoshi's Cookie is the property of its respective
  owners; this is an unofficial fan modification and is not affiliated with them.
"""

README_MD = """\
# {name}

**High-score saving** for *Yoshi's Cookie* (Game Boy). The game is unchanged: no new
screens or menus. The high-score you saw last time is simply still there next time,
instead of resetting to 0 whenever the console is switched off. It also survives the
game's soft reset, which wiped it in the original game.

**Download:** see the [latest release]({releases_url}) (BPS and IPS patches).
This repository contains no game data; you need your own copy of the ROM.

## Base ROM

The patch applies to exactly this file:

| This | Should be this |
|-------|-----|
| Name | `{rom_name}` |
| Size | {size} bytes |
| CRC32 | `{crc32}` |
| MD5 | `{md5}` |
| SHA-1 | `{sha1}` |

The BPS patch verifies this and refuses to apply to anything else; the IPS patch cannot.

## Patching

Apply the `.bps` patch (preferred) or the `.ips` patch to your ROM with the online patcher at
<https://www.romhacking.net/patch/> (it works in your browser, so there is nothing to
install) or any other IPS/BPS patcher. Your original is not modified.

## Requirements

Saving needs cartridge RAM:

- **Emulators:** anything that supports MBC1 + RAM + battery (BGB, SameBoy, mGBA, ...).
  A `.sav` file appears next to the ROM.
- **Hardware:** a flash cart or rewritable cartridge supporting MBC1 with 8 KB of save
  RAM. Battery-backed RAM and non-volatile FRAM both qualify; the DMG-GBWR-50 rewritable
  cartridge uses FRAM.

Without save RAM the game still plays, but, as in the original game, the high-score is
lost when the console is switched off or soft-reset.

## Using it

- Nothing to do: the high-score is stored automatically whenever it changes.
- With save RAM, a soft reset no longer loses the high-score (it did in the original game).
- **Reset the high-score:** switch the console on while holding **Down + Select**. The screen
  stays white while any button is held (that is the game waiting for all buttons to be
  released, exactly as in the original); keep them held for a moment, then release them.
  The high-score is back to 0.

## Limitations

- Only the high-score is saved.
- A damaged save (wrong check byte, or digits that are not valid decimal digits) is
  ignored and the game starts with a high-score of 0.

## How it works

Everything goes into unused padding at the end of bank 0 or hook points; the header is changed
to MBC1+RAM+BATTERY (`0x147` 01 to 03, `0x149` 00 to 02) and its checksums are recalculated.

| Where | What |
|---|---|
| `0x0163` | the boot's single clear of `$8000`-`$DFFB` becomes `call ${clear_addr:04X}`, which clears `$8000`-`$9FFF` and `$C000`-`$DFFB` and leaves `$A000`-`$BFFF` (the cartridge RAM) alone |
| `0x0178` | `jp nz,$0150` in the game's wait-until-no-button-is-held loop becomes `jp nz,${hold_addr:04X}` (reset combination) |
| `0x0192` | boot hook: `call ${load_addr:04X}` (replaces `ld [$C13C],a`, which the new code runs first) |
| bank 1 `0x5965` | save hook: the end of the game's high-score copy routine becomes `jp ${tail_addr:04X}` |
| `0x{save_addr:04X}`-`0x{end_addr:04X}` | the new code, {total_len} bytes (save {save_len}, joypad check {combo_len}, load {load_len}, reset hook {hold_len}, clear {clear_len}, save hook {tail_len}), in the zero padding at the end of bank 0 |

Game RAM: score `$C16D-$C174`, high-score `$C176-$C17D`: eight bytes, one decimal digit per
byte, least significant digit first (`$C176` is the hidden units digit, always 0).
Save RAM at `$A000`: the eight high-score bytes plus a check byte (`$A5` + their sum).

- The **load routine** runs once per boot, right after the game sets its default high-score (0).
  A valid save replaces the default; a missing or damaged save leaves it alone.
- The **save routine** runs only when the game itself updates the high-score. The routine in bank 1
  at `$5943` compares the score with the high-score from the top digit down and, if higher, copies
  it with a small loop; the end of that routine now calls the save routine and returns exactly as
  the game did (carry clear means "updated"). Nothing runs every frame and no interrupt is hooked.
- The game's boot code (`$0175`-`$0178`) reads the joypad and restarts until no button is held, so
  the **reset combination** is detected inside that loop: with Down + Select held, the default
  high-score is written to the save; when the buttons are released the game continues and the load
  routine restores it.
- The game's soft reset re-runs the boot code, which wipes all RAM; the load routine runs again
  then, which is why the saved high-score now survives it.

If you have Python, you can try this yourself using the scripts from the repository:
`{patcher_script}` rebuilds the patched ROM from your original ROM, and
`{release_script}` builds the patches, READMEs and zip.

## Tested

{tested_on}

## Legal

No game data is included, only the differences. *Yoshi's Cookie* belongs to its respective
owners; this is an unofficial fan modification and is not affiliated with them.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--author", default="Your Name")
    ap.add_argument("--version", default="1.0")
    ap.add_argument("--outdir", default="release")
    ap.add_argument("--repo", default="", help="repository URL, used in README.txt and for the release link in README.md")
    a = ap.parse_args()

    src_path = Path(a.rom)
    src = src_path.read_bytes()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_src = Path(tmp) / src_path.name
        shutil.copy(src_path, tmp_src)
        patched_path = pb.main(str(tmp_src))      # the patcher says where it wrote the ROM
        dst = Path(patched_path).read_bytes()

    ips, bps = make_ips(src, dst), make_bps(src, dst)
    assert apply_ips(src, ips) == dst, "IPS round trip failed"
    assert apply_bps(src, bps) == dst, "BPS round trip failed"
    print(f"IPS: {len(ips)} bytes, BPS: {len(bps)} bytes (both verified against the real output)")
    print(f"Patched ROM: CRC32 {zlib.crc32(dst):08X}  SHA-1 {hashlib.sha1(dst).hexdigest().upper()}")

    base = f"{FILE_NAME} v{a.version}"
    # layout numbers come from the patcher, so the README can never drift from the code
    save_addr = pb.REGION[0]
    combo_addr = save_addr + len(pb.build_savehigh(save_addr))
    load_addr = combo_addr + len(pb.build_checkcombo(combo_addr))
    hold_addr = load_addr + len(pb.build_load(load_addr, save_addr, combo_addr))
    clear_addr = hold_addr + len(pb.build_holdhook(hold_addr, save_addr, combo_addr))
    tail_addr = clear_addr + len(pb.build_clearsplit(clear_addr))
    end_addr = tail_addr + len(pb.build_newtail(tail_addr, save_addr)) - 1
    names = dict(patcher_script=Path(pb.__file__).name, release_script=Path(__file__).name,
                 save_addr=save_addr, load_addr=load_addr, hold_addr=hold_addr, clear_addr=clear_addr,
                 tail_addr=tail_addr, end_addr=end_addr, total_len=end_addr + 1 - save_addr,
                 save_len=combo_addr - save_addr, combo_len=load_addr - combo_addr,
                 load_len=hold_addr - load_addr, hold_len=clear_addr - hold_addr,
                 clear_len=tail_addr - clear_addr, tail_len=end_addr + 1 - tail_addr,
                 tested_on=TESTED_ON)
    hashes = dict(rom_name=src_path.name, size=len(src), crc32=f"{zlib.crc32(src):08X}",
                  md5=hashlib.md5(src).hexdigest().upper(), sha1=hashlib.sha1(src).hexdigest().upper())
    readme = README.format(repo_note=f" ({a.repo})" if a.repo else "", **names, **hashes,
                           name=NAME, version=a.version, author=a.author,
                           date=datetime.date.today().isoformat())
    releases_url = f"{a.repo.rstrip('/')}/releases/latest" if a.repo else "../../releases/latest"
    readme_md = README_MD.format(**names, **hashes, releases_url=releases_url, name=NAME)

    outdir = Path(a.outdir)
    outdir.mkdir(exist_ok=True)
    (outdir / "README.md").write_text(readme_md, encoding="utf-8")
    zpath = outdir / f"{base}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{base}.bps", bps)
        z.writestr(f"{base}.ips", ips)
        z.writestr("README.txt", readme)
    print(f"Written: {zpath}")
    print(f"Base ROM CRC32 {hashes['crc32']}  SHA-1 {hashes['sha1']}")


if __name__ == "__main__":
    main()
