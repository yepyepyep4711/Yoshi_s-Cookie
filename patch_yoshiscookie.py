#!/usr/bin/env python3
"""Add high-score saving (cartridge RAM) to "Yoshi's Cookie (U) [!].gb".

Usage:  python patch_yoshiscookie.py "roms\\Yoshi's Cookie (U) [!].gb"
Output: same folder, "<name> (high-score save).gb". The original is not modified.

Diagnostics (to find out which part of the patch causes a problem):
  --header-only   only the cartridge header is changed (RAM declared), no code at all.
  --load-only     header + boot hook + RAM-clear fix; a saved high-score is restored
                  but nothing is ever saved.
  --save-only     header + update hook + RAM-clear fix; the high-score is saved but
                  nothing is ever restored.

What it changes
  header 0x147   01 -> 03   MBC1 -> MBC1+RAM+BATTERY
  header 0x149   00 -> 02   8 KB cartridge RAM
  0x0163         the big boot clear of $8000-$DFFB becomes "call ClearSplit", which
                 clears $8000-$9FFF and $C000-$DFFB and so leaves $A000-$BFFF (the
                 cartridge RAM) alone, ending in exactly the same register state and
                 with the stack no deeper than before
  0x0178         "jp nz,$0150" -> "jp nz,HoldHook"   (the game's wait-for-no-buttons loop:
                 where the reset combination is detected, see below)
  0x0192         "ld [$C13C],a" -> "call Load"   (boot: restore the high-score)
  bank 1 0x5965  "scf ; ccf ; ret" at the end of the game's high-score copy routine
                 -> "jp NewTail"   (save it)
  0x3F04         SaveHigh, CheckCombo, Load, HoldHook, ClearSplit and NewTail
                 (padding at the end of bank 0)

Game RAM
  score        $C16D-$C174: eight bytes, one decimal digit per byte, least significant
               digit first. $C16D is the hidden units digit (always 0).
  high-score   $C176-$C17D: same layout ($C177-$C17D are the seven digits shown on
               screen). The game's routine at bank 1 $5943 compares score and
               high-score from the top digit down and, when the score is higher,
               copies all eight bytes ($595F loop). The default (all zeros) is set at
               $0182-$018E, so Load runs after it.

How saving works
  The game updates the high-score in one place only. The last instructions of that
  copy routine (scf ; ccf ; ret, which leave carry clear to say "updated") are replaced
  by a jump to NewTail, which calls SaveHigh and then repeats scf ; ccf ; ret, so the
  caller sees exactly the state the game left. Nothing runs every frame and no
  interrupt is hooked.

Soft reset: the game's soft reset re-runs the boot code (jp $0150), which wipes all
RAM, so the original loses the high-score. Load runs again at that boot, so with a
patched ROM and save RAM the high-score survives a soft reset.

Reset: the game refuses to start while any button is held: its boot code ($0175-$0178)
reads the joypad and jumps back to $0150 until nothing is pressed (the screen stays
white meanwhile, exactly as in the original). So the reset combination has to be
detected inside that loop, not later in Load: HoldHook runs on every pass, and when
Down + Select is held it writes the default high-score (0; all RAM has just been
cleared) to the save. When the buttons are released the game continues and Load
restores that default. Change RESET_BUTTONS / RESET_DPAD to pick others.

SRAM layout at $A000:  8 high-score bytes, then a check byte = $A5 + their sum.
Load also rejects a record whose seven digit bytes ($C177-$C17D) are not 0-9; the
first byte ($C176) is not checked.
"""
import sys
from pathlib import Path

HIGH_SCORE = 0xC176              # first byte of the high-score block
HIGH_LEN = 8                     # bytes saved ($C176-$C17D)
DIGITS = 7                       # the last 7 of them must be decimal digits
SRAM = 0xA000
BOOT_HOOK = 0x0192               # ld [$C13C],a  (EA 3C C1), A = 1 at this point
CLEAR_AT = 0x0163                # ld hl,$8000 ; ld bc,$5FFC ; call $035D   (9 bytes)
CLEAR_ORIG = bytes.fromhex("21 00 80 01 FC 5F CD 5D 03".replace(" ", ""))
CLEAR_ROUTINE = 0x035D           # the game's zero-fill: ld a,0 ; ldi (hl),a ; dec bc ; ...
HOLD_CHECK = (0x0172, bytes.fromhex("CD C7 02 F0 8B A7 C2 50 01".replace(" ", "")))   # ... jp nz,$0150
BOOT_START = 0x0150
UPDATE_END = 0x5965              # bank 1: scf ; ccf ; ret  (37 3F C9)
UPDATE_ROUTINE = (0x5943, bytes.fromhex(
    "21 7D C1 11 74 C1 06 08 1A BE D8 20 07 1B 2B 05 20 F6 37 C9 "
    "21 6D C1 11 76 C1 06 08 2A 12 13 05 20 FA 37 3F C9".replace(" ", "")))
DEFAULT_COPY = (0x0182, bytes.fromhex(
    "21 08 38 11 7E C1 06 09 2A 12 1B 05 20 FA 3E 01 EA 3C C1".replace(" ", "")))
REGION = (0x3F04, 0x4000)        # padding (all zeros) at the end of bank 0

# Held together at boot -> ignore the save. Bit values:
RESET_BUTTONS = 0x04             # A=1, B=2, Select=4, Start=8   (here: Select)
RESET_DPAD = 0x08                # Right=1, Left=2, Up=4, Down=8 (here: Down; 0 = unused)
VALIDATE_DIGITS = True           # set False if a legitimate high-score ever fails to restore


def le(v):
    return bytes([v & 0xFF, v >> 8])


def asm(org, prog):
    """Tiny two-pass assembler: bytes, 'label:' strings, ('jr', opcode, label)."""
    labels, pos = {}, org
    for it in prog:
        if isinstance(it, bytes):
            pos += len(it)
        elif isinstance(it, str):
            labels[it.rstrip(":")] = pos
        else:
            pos += 2
    out, pos = bytearray(), org
    for it in prog:
        if isinstance(it, bytes):
            out += it
            pos += len(it)
        elif isinstance(it, tuple):
            _, op, lab = it
            off = labels[lab] - (pos + 2)
            assert -128 <= off <= 127, "jr out of range"
            out += bytes([op, off & 0xFF])
            pos += 2
    return bytes(out)


def build_savehigh(org):
    """Write the high-score ($C176-$C17D) and its check byte to cartridge RAM.
    Preserves every register and the flags."""
    return asm(org, [
        b"\xF5", b"\xC5", b"\xD5", b"\xE5",   # push af / bc / de / hl
        b"\x3E\x0A",                          # ld a,$0A
        b"\xEA\x00\x00",                      # ld [$0000],a      enable SRAM
        b"\x21" + le(HIGH_SCORE),             # ld hl,$C176
        b"\x11" + le(SRAM),                   # ld de,$A000
        b"\x06" + bytes([HIGH_LEN]),          # ld b,8
        b"\x0E\xA5",                          # ld c,$A5          running check byte
        "w:",
        b"\x2A",                              # ld a,[hl+]
        b"\x12",                              # ld [de],a
        b"\x13",                              # inc de
        b"\x81",                              # add a,c
        b"\x4F",                              # ld c,a
        b"\x05",                              # dec b
        ("jr", 0x20, "w"),                    # jr nz,w
        b"\x79",                              # ld a,c
        b"\x12",                              # ld [de],a         check byte
        b"\xAF",                              # xor a
        b"\xEA\x00\x00",                      # ld [$0000],a      disable SRAM
        b"\xE1", b"\xD1", b"\xC1", b"\xF1",   # pop hl / de / bc / af
        b"\xC9",                              # ret
    ])


def build_checkcombo(org):
    """Read the joypad; return with Z set if the reset combination is held. Clobbers A and B."""
    code = [
        b"\x3E\x10",                          # ld a,$10          select buttons
        b"\xE0\x00",                          # ldh [$FF00],a
        b"\xF0\x00",                          # ldh a,[$FF00]     (read twice: lets the
        b"\xF0\x00",                          # ldh a,[$FF00]      lines settle on hardware)
        b"\xE6" + bytes([RESET_BUTTONS]),     # and RESET_BUTTONS  pressed reads as 0
    ]
    if RESET_DPAD:
        code += [
            b"\x47",                          # ld b,a
            b"\x3E\x20",                      # ld a,$20          select d-pad
            b"\xE0\x00",                      # ldh [$FF00],a
            b"\xF0\x00",                      # ldh a,[$FF00]
            b"\xF0\x00",                      # ldh a,[$FF00]
            b"\xE6" + bytes([RESET_DPAD]),    # and RESET_DPAD
            b"\xB0",                          # or b              zero only if all held
        ]
    code += [
        b"\x3E\x30",                          # ld a,$30          deselect (flags untouched)
        b"\xE0\x00",                          # ldh [$FF00],a
        b"\xC9",                              # ret
    ]
    return asm(org, code)


def build_load(org, savehigh_addr, checkcombo_addr):
    code = [
        b"\xEA\x3C\xC1",                      # ld [$C13C],a      the instruction we replaced (A=1)
        b"\xF5", b"\xC5", b"\xD5", b"\xE5",   # push af / bc / de / hl   (A must come back as 1)
        b"\xCD" + le(checkcombo_addr),         # call CheckCombo   Z = reset combination held
        ("jr", 0x20, "normal"),               # jr nz,normal      not held
        b"\xCD" + le(savehigh_addr),          # call SaveHigh     held (pressed just after the
        ("jr", 0x18, "done"),                 #                   game's own wait): write the default
        "normal:",
        b"\x3E\x0A",                          # ld a,$0A
        b"\xEA\x00\x00",                      # ld [$0000],a      enable SRAM
        b"\x21" + le(SRAM),                   # ld hl,$A000
        b"\x0E\xA5",                          # ld c,$A5
        b"\x06" + bytes([HIGH_LEN]),          # ld b,8
        "sum:",
        b"\x2A",                              # ld a,[hl+]
        b"\x81",                              # add a,c
        b"\x4F",                              # ld c,a
        b"\x05",                              # dec b
        ("jr", 0x20, "sum"),
        b"\x7E",                              # ld a,[hl]         stored check byte
        b"\xB9",                              # cp c
        ("jr", 0x20, "done"),                 # jr nz,done        invalid -> keep default
    ]
    if VALIDATE_DIGITS:
        code += [
            b"\x21" + le(SRAM + HIGH_LEN - DIGITS),   # ld hl,$A001   first digit byte
            b"\x06" + bytes([DIGITS]),        # ld b,7
            "dig:",
            b"\x2A",                          # ld a,[hl+]
            b"\xFE\x0A",                      # cp 10
            ("jr", 0x30, "done"),             # jr nc,done        not a digit -> reject
            b"\x05",                          # dec b
            ("jr", 0x20, "dig"),
        ]
    code += [
        b"\x21" + le(SRAM),                   # ld hl,$A000
        b"\x11" + le(HIGH_SCORE),             # ld de,$C176
        b"\x06" + bytes([HIGH_LEN]),          # ld b,8
        "cp:",
        b"\x2A",                              # ld a,[hl+]
        b"\x12",                              # ld [de],a
        b"\x13",                              # inc de
        b"\x05",                              # dec b
        ("jr", 0x20, "cp"),
        "done:",
        b"\xAF",                              # xor a
        b"\xEA\x00\x00",                      # ld [$0000],a      disable SRAM
        b"\xE1", b"\xD1", b"\xC1", b"\xF1",   # pop hl / de / bc / af   (A = 1 again)
        b"\xC9",                              # ret
    ]
    return asm(org, code)


def build_holdhook(org, savehigh_addr, checkcombo_addr):
    """Replaces "jp nz,$0150" in the game's wait-until-no-button-is-held loop. All RAM
    has just been cleared there, so SaveHigh writes the default high-score (zeros)."""
    return asm(org, [
        b"\xCD" + le(checkcombo_addr),         # call CheckCombo
        ("jr", 0x20, "back"),                 # jr nz,back        combination not held
        b"\xCD" + le(savehigh_addr),          # call SaveHigh     held: wipe the saved high-score
        "back:",
        b"\xC3" + le(BOOT_START),             # jp $0150          the game's own jump, unchanged
    ])


def build_clearsplit(org):
    """Same result as "ld hl,$8000 ; ld bc,$5FFC ; call $035D", but skips $A000-$BFFF.

    The second clear is a JUMP, not a call: the stack starts at $DFFF and the game's clear
    stops at $DFFB to protect it, so the stack must be no deeper than in the original
    (one return address at $DFFD-$DFFE). A second call level would put a return address
    at $DFFB, right where the clear writes zeros."""
    return asm(org, [
        b"\x21\x00\x80", b"\x01\x00\x20", b"\xCD" + le(CLEAR_ROUTINE),   # $8000-$9FFF
        b"\x21\x00\xC0", b"\x01\xFC\x1F", b"\xC3" + le(CLEAR_ROUTINE),   # $C000-$DFFB, then
                                              # the game's own ret returns to $0166 (HL=$DFFC,
                                              # BC=0, A=0, Z set, as before)
    ])


def build_newtail(org, savehigh_addr):
    """Replaces "scf ; ccf ; ret" at the end of the game's copy routine."""
    return asm(org, [
        b"\xCD" + le(savehigh_addr),          # call SaveHigh     (preserves AF, BC, DE, HL)
        b"\x37",                              # scf
        b"\x3F",                              # ccf               carry clear = "high-score updated"
        b"\xC9",                              # ret
    ])


def fix_checksums(rom):
    c = 0
    for b in rom[0x134:0x14D]:
        c = (c - b - 1) & 0xFF
    rom[0x14D] = c
    g = (sum(rom) - rom[0x14E] - rom[0x14F]) & 0xFFFF
    rom[0x14E], rom[0x14F] = g >> 8, g & 0xFF


def expect(rom, addr, want, what):
    got = bytes(rom[addr:addr + len(want)])
    if got != want:
        sys.exit(f"Refusing to patch: {what} at 0x{addr:04X} is {got.hex(' ')}, "
                 f"expected {want.hex(' ')}. Wrong ROM version?")


def main(path, header_only=False, only=None):
    """only: None (full patch) or a diagnostic build: 'load' or 'save'."""
    src = Path(path)
    rom = bytearray(src.read_bytes())
    if len(rom) != 0x20000:
        sys.exit("Expected a 128 KB ROM.")

    expect(rom, 0x134, b"YOSHI'S COOKIE", "title")
    expect(rom, 0x147, b"\x01", "cartridge type")
    expect(rom, 0x148, b"\x02", "ROM size")
    expect(rom, 0x149, b"\x00", "RAM size")
    expect(rom, 0x14D, b"\x02", "header checksum")
    expect(rom, 0x14E, b"\xAF\x21", "global checksum")
    expect(rom, CLEAR_AT, CLEAR_ORIG, "boot clear")
    expect(rom, DEFAULT_COPY[0], DEFAULT_COPY[1], "default high-score copy")
    expect(rom, UPDATE_ROUTINE[0], UPDATE_ROUTINE[1], "high-score update routine")
    expect(rom, HOLD_CHECK[0], HOLD_CHECK[1], "boot wait-for-no-buttons loop")

    if header_only:
        rom[0x147] = 0x03
        rom[0x149] = 0x02
        fix_checksums(rom)
        dst = src.with_name(src.stem + " (header only).gb")
        dst.write_bytes(rom)
        print(f"Written: {dst}  (header only, no save code)")
        return dst

    savehigh_addr = REGION[0]
    savehigh = build_savehigh(savehigh_addr)
    combo_addr = savehigh_addr + len(savehigh)
    combo = build_checkcombo(combo_addr) if only in (None, "load") else b""
    load_addr = combo_addr + len(combo)
    load = build_load(load_addr, savehigh_addr, combo_addr) if combo else b""
    hold_addr = load_addr + len(load)
    hold = build_holdhook(hold_addr, savehigh_addr, combo_addr) if combo else b""
    clear_addr = hold_addr + len(hold)
    clear = build_clearsplit(clear_addr)
    tail_addr = clear_addr + len(clear)
    tail = build_newtail(tail_addr, savehigh_addr) if only in (None, "save") else b""
    end = tail_addr + len(tail)
    if end > REGION[1]:
        sys.exit(f"The new code needs {end - savehigh_addr} bytes, only {REGION[1] - savehigh_addr} available.")
    if any(rom[REGION[0]:REGION[1]]):
        sys.exit(f"The area 0x{REGION[0]:04X}-0x{REGION[1] - 1:04X} is not all zeros; not safe to use.")
    print(f"SaveHigh  : {len(savehigh)} bytes at 0x{savehigh_addr:04X}")
    if combo:
        print(f"CheckCombo: {len(combo)} bytes at 0x{combo_addr:04X}")
        print(f"Load      : {len(load)} bytes at 0x{load_addr:04X}")
        print(f"HoldHook  : {len(hold)} bytes at 0x{hold_addr:04X}")
    print(f"ClearSplit: {len(clear)} bytes at 0x{clear_addr:04X}")
    if tail:
        print(f"NewTail   : {len(tail)} bytes at 0x{tail_addr:04X}  ({REGION[1] - end} bytes of the area left)")

    rom[savehigh_addr:end] = savehigh + combo + load + hold + clear + tail
    rom[CLEAR_AT:CLEAR_AT + 9] = b"\xCD" + le(clear_addr) + b"\x00" * 6              # call ClearSplit
    if load:
        rom[BOOT_HOOK:BOOT_HOOK + 3] = b"\xCD" + le(load_addr)                        # call Load
        rom[HOLD_CHECK[0] + 6:HOLD_CHECK[0] + 9] = b"\xC2" + le(hold_addr)             # jp nz,HoldHook
    if tail:
        rom[UPDATE_END:UPDATE_END + 3] = b"\xC3" + le(tail_addr)                      # jp NewTail
    rom[0x147] = 0x03        # MBC1+RAM+BATTERY
    rom[0x149] = 0x02        # 8 KB RAM
    fix_checksums(rom)

    suffix = {None: "high-score save", "load": "load only", "save": "save only"}[only]
    dst = src.with_name(f"{src.stem} ({suffix}).gb")
    dst.write_bytes(rom)
    print(f"Written: {dst}" + ("" if only is None else "  (diagnostic build, not the full patch)"))
    return dst


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    flags = {"--load-only": "load", "--save-only": "save"}
    only = next((v for k, v in flags.items() if k in sys.argv), None)
    main(args[0], header_only="--header-only" in sys.argv, only=only)
