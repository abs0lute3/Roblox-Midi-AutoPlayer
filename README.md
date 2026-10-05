# Roblox Midi AutoPlayer

Plays sheet music **and MIDI files** on Roblox pianos (and any Virtual Piano-style
keyboard) by sending keystrokes. Automatically focuses the Roblox window when the
music starts, so you don't have to race it.

Built on the original work of [maksimKorzh](https://github.com/maksimKorzh) and
[Xi-v](https://github.com/Xi-v).

## Setup

```
pip install -r requirements.txt
```

## Usage

Run `python auto.py` (or double-click `RobloxMidiAutoPlayer.exe`) and a small picker
window opens with two tabs:

- **Files in this folder** - every `.mid`/`.midi` file in that folder plus `sheet.txt`.
  Select one and hit **Start**.
- **Paste a sheet** - paste or type any Virtual Piano sheet
  (lowercase = white keys, UPPERCASE & `!@$%^*()` = black keys, `[abc]` = chord,
  `|` = pause) and hit **Play pasted sheet**.

Or from the command line:

```
python auto.py                    # open the file picker UI
python auto.py song.mid           # play a MIDI file (no UI)
python auto.py sheet.txt          # play a Virtual Piano sheet (no UI)
python auto.py song.mid --list    # show the tracks inside a MIDI file
python auto.py song.mid --track 2 # play only track 2
python auto.py song.mid --dry-run # show what would be played, press no keys
```

A 3-second count-in starts after the Roblox window is focused.
**Press ESC at any time to stop.**

## How it works

- **MIDI files** are parsed with [mido](https://mido.readthedocs.io/). Note timing
  and tempo changes are preserved, drum tracks are skipped, and every note is
  mapped onto the standard Virtual Piano layout (C2..C7). Notes outside that
  range are folded in by octaves so no note is silently dropped.
- **Sheets** (`.txt`) use the classic Virtual Piano format: lowercase letters are
  white keys, uppercase letters and `!@$%^*(` are black keys, `[abc]` chords are
  struck together and `|` is a pause. Sheets can also be pasted straight into
  the picker UI's **Paste a sheet** tab without saving a file.
- **Autofocus** finds the Roblox window (`RobloxWindow` class, with a title-search
  fallback) and brings it to the foreground right before playback.

## Tuning

Open `auto.py` and edit the config block at the top:

| Setting           | Default | What it does                                    |
|-------------------|---------|-------------------------------------------------|
| `DELAY`           | `0.095` | Speed of sheet-music playback (lower = faster)  |
| `START_DELAY`     | `3.0`   | Count-in seconds before playback begins         |
| `CHORD_THRESHOLD` | `0.03`  | MIDI notes within this window are hit together  |
| `KEY_SPACING`     | `0.012` | Pause between individual keystrokes             |
| `SAME_KEY_GAP`    | `0.02`  | Extra gap when the same key repeats             |
| `HUMANIZE`        | `0.0`   | Random timing jitter for a less robotic sound   |

For MIDI playback the timing comes from the file itself, so `DELAY` mostly affects
sheets; `KEY_SPACING` and `CHORD_THRESHOLD` shape how MIDI songs sound.

## Tips

- Fast/complex MIDIs can outrun what Roblox registers; if notes get swallowed,
  increase `KEY_SPACING` slightly.
- If a MIDI sounds muddy, play only the melody: `python auto.py song.mid --list`,
  then `--track N` on the melodic track.
- Antivirus software may flag synthetic keystrokes - that's a false positive.

## Rebuilding the EXE

`RobloxMidiAutoPlayer.exe` is a standalone build of `auto.py` (PyInstaller onefile),
so the picker UI, MIDI support, autofocus and ESC-stop all work in the exe too.
Just put the exe in a folder with your `.mid` files and/or `sheet.txt` and
double-click it.

To rebuild it yourself after changing `auto.py`:

```
pip install pyinstaller
pyinstaller --onefile --name RobloxMidiAutoPlayer auto.py
```

The finished exe appears in the `dist/` folder.
Note: antivirus software sometimes flags PyInstaller exes as false positives.
