# Yoshi's Cookie - High-score Save

**High-score saving** for *Yoshi's Cookie* (Game Boy). The game is unchanged: no new
screens or menus. The high-score you saw last time is simply still there next time,
instead of resetting to 0 whenever the console is switched off. It also survives the
game's soft reset, which wiped it in the original game.

**Download:** see the [latest release](https://github.com/yepyepyep4711/Yoshi_s-Cookie/releases/latest) (BPS and IPS patches).
This repository contains no game data; you need your own copy of the ROM.

## Base ROM

The patch applies to exactly this file:

| This | Should be this |
|-------|-----|
| Name | `Yoshi's Cookie (U) [!].gb` |
| Size | 131072 bytes |
| CRC32 | `75336712` |
| MD5 | `BC1A3848092BDC900C157996C29A7783` |
| SHA-1 | `233DCC94D86951DDF167AAA82EC9B94B87B29E2D` |

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
| `0x0163` | the boot's single clear of `$8000`-`$DFFB` becomes `call $3F9B`, which clears `$8000`-`$9FFF` and `$C000`-`$DFFB` and leaves `$A000`-`$BFFF` (the cartridge RAM) alone |
| `0x0178` | `jp nz,$0150` in the game's wait-until-no-button-is-held loop becomes `jp nz,$3F90` (reset combination) |
| `0x0192` | boot hook: `call $3F45` (replaces `ld [$C13C],a`, which the new code runs first) |
| bank 1 `0x5965` | save hook: the end of the game's high-score copy routine becomes `jp $3FAD` |
| `0x3F04`-`0x3FB2` | the new code, 175 bytes (save 38, joypad check 27, load 75, reset hook 11, clear 18, save hook 6), in the zero padding at the end of bank 0 |

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
`patch_yoshiscookie.py` rebuilds the patched ROM from your original ROM, and
`make_release_yoshiscookie.py` builds the patches, READMEs and zip.

## Tested

BGB and real hardware: DMG-GBWR-50 rewritable cartridge and EZ Flash Jr, played on Game Boy,
Game Boy Color, Analogue Pocket and ModRetro Chromatic. Tested: saving, loading, high-score reset
and soft resets.

## Legal

No game data is included, only the differences. *Yoshi's Cookie* belongs to its respective
owners; this is an unofficial fan modification and is not affiliated with them.
