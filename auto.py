"""
Roblox Midi AutoPlayer
Original code by maksimKorzh (https://github.com/maksimKorzh)
Adjusted by Xi-v (https://github.com/Xi-v)
Upgraded: MIDI file support, Virtual Piano note mapping, Roblox autofocus,
a global Escape hotkey to stop playback at any time, an option to turn the
keyboard off while a song plays (on by default), and Ctrl+E to close
the command window.

Usage:
    python auto.py (or double-click the exe)
                                       -> opens a picker listing the .mid files
                                          and sheet.txt found in this folder
    python auto.py song.mid            -> play a MIDI file
    python auto.py sheet.txt           -> play a Virtual Piano sheet (letters + [chords])
    python auto.py song.mid --list     -> list the tracks inside a MIDI file
    python auto.py song.mid --track 2  -> play only track 2
    python auto.py song.mid --dry-run  -> show what would be played, press no keys
    python auto.py --allow-keyboard    -> keep your keyboard on while playing

    Ctrl+E (any time)                  -> close the command window for good
"""

import os
import sys
import time
import ctypes
import random
import threading

from pynput.keyboard import Controller, Key, Listener

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

# Base delay between keystrokes for sheet music, in seconds (lower = faster).
# 0.095 is the classic default.
DELAY = 0.095

# Count-in before the music starts (the Roblox window is focused automatically).
START_DELAY = 3.0

# Extra random timing jitter in seconds (0 to disable). Makes playback less robotic.
HUMANIZE = 0.0

# MIDI notes starting within this many seconds of each other are struck together
# as one chord. Keep small so fast melody runs are not merged.
CHORD_THRESHOLD = 0.03

# Small pause between keystrokes so Roblox registers every key.
KEY_SPACING = 0.012

# Gap inserted before a repeated identical key so both hits are registered.
SAME_KEY_GAP = 0.02

# While a song plays, turn off the physical keyboard so typing can't clash
# with the automatic keys. The player's own synthetic keystrokes still reach
# Roblox, ESC still stops playback, and the keyboard comes back afterwards.
# On by default; uncheck it in the picker or run with --allow-keyboard.
BLOCK_KEYBOARD = True

# Playback speed multiplier (1.0 = normal). The picker's Speed slider sets
# this: 2.0 plays twice as fast, 0.5 at half speed.
SPEED = 1.0

# Human-error slips: every N key presses, hit the key one over instead (like
# meaning E but hitting R), so playback sounds like a real person. The
# picker's Mistakes box chooses it: Beginner = every 12 keys, Legit = every
# 24 (default), Pro = every 45.
MISTAKE_MODE = "Legit"
MISTAKE_MAP = {"Beginner": 12, "Legit": 24, "Pro": 45}
MISTAKE_EVERY = MISTAKE_MAP[MISTAKE_MODE]

# Global safety stop: press Escape at any time to abort playback.
STOP_REQUESTED = False
DRY_RUN = False


def _on_press(key):
    global STOP_REQUESTED
    if key == Key.esc:
        STOP_REQUESTED = True


_listener = Listener(on_press=_on_press)
_listener.daemon = True
_listener.start()

# ----------------------------------------------------------------------------
# Virtual Piano layout: one chromatic string spanning C2 (MIDI 36) to C7 (MIDI
# 96), exactly as virtualpiano.net and Roblox pianos lay it out. The number row
# starts two octaves below middle C, so middle C (MIDI 60) = 't'. Lowercase =
# white keys, uppercase letters and shifted symbols = black keys.
# ----------------------------------------------------------------------------
VP_LAYOUT = "1!2@34$5%6^78*9(0qQwWeErtTyYuiIoOpPasSdDfgGhHjJklLzZxcCvVbBnm"
VP_LOWEST = 36   # MIDI note of VP_LAYOUT[0]  (C2, key '1')
VP_HIGHEST = 96  # MIDI note of VP_LAYOUT[-1] (C7, key 'm')

# Characters that need Shift held down, mapped to the key they sit on.
# Covers the layout's black-key symbols plus extra symbols seen in hand-made sheets.
SHIFT_BASE = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
    "&": "7", "*": "8", "(": "9", ")": "0",
    "_": "-", "+": "=", "{": "[", "}": "]", ":": ";",
    '"': "'", "<": ",", ">": ".", "?": "/",
}

# ----------------------------------------------------------------------------
# MIDI -> Virtual Piano conversion
# ----------------------------------------------------------------------------


def midi_number_to_vp(midi_number):
    """Convert a MIDI note number (0-127) to a Virtual Piano key character.

    Notes outside the 61-key layout (C2..C6) are folded in by whole octaves,
    keeping the pitch class, which is what most MIDI-to-VP converters do.
    """
    n = midi_number
    while n > VP_HIGHEST:
        n -= 12
    while n < VP_LOWEST:
        n += 12
    return VP_LAYOUT[n - VP_LOWEST]

def load_midi_events(path, track=None):
    """Parse a MIDI file into absolute-time events.

    Returns a list of (seconds, kind, value):
      kind 'on'   -> value is a Virtual Piano key char
      kind 'off'  -> value is the original MIDI note number (release, unused)
      kind 'meta' -> value is a track name string
    Tempo changes are honored; channel 10 / drum tracks and notes outside the
    keyboard range are skipped.
    """
    import mido

    mid = mido.MidiFile(path)
    tpb = mid.ticks_per_beat

    # Tempo is global in a MIDI file, so collect tempo changes from ALL tracks.
    tempo_map = [(0, 500000)]  # (tick, microseconds per beat) - default 120 BPM
    notes = []                 # (tick, kind, midi note number)
    names = {}                 # track index -> name
    for i, tr in enumerate(mid.tracks):
        if tr.name and tr.name.strip():
            names[i] = tr.name.strip()
        tick = 0
        for msg in tr:
            tick += msg.time
            if msg.type == "set_tempo":
                tempo_map.append((tick, msg.tempo))
            elif track is None or i == track:
                if msg.type == "note_on" and msg.velocity > 0:
                    notes.append((tick, "on", msg.note))
                elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                    notes.append((tick, "off", msg.note))

    tempo_map.sort(key=lambda x: x[0])

    def tick_to_seconds(tick):
        seconds = 0.0
        prev_tick, prev_tempo = tempo_map[0]
        for t, tempo in tempo_map[1:]:
            if t >= tick:
                break
            seconds += (t - prev_tick) * prev_tempo / 1e6 / tpb
            prev_tick, prev_tempo = t, tempo
        seconds += (tick - prev_tick) * prev_tempo / 1e6 / tpb
        return seconds

    events = [(0.0, "meta", f"track {i}: {name}") for i, name in sorted(names.items())]
    for tick, kind, note in notes:
        t = tick_to_seconds(tick)
        if kind == "on":
            char = midi_number_to_vp(note)
            if char:
                events.append((t, "on", char))
        else:
            events.append((t, "off", note))

    events.sort(key=lambda e: e[0])
    return events


def events_to_steps(events, chord_threshold=CHORD_THRESHOLD):
    """Collapse timed events into playable steps of [time, [key chars]].

    Notes starting within chord_threshold seconds of the step's first note are
    struck together as a chord; duplicate keys inside one chord are kept once.
    Meta and release events are dropped.
    """
    steps = []
    for t, kind, value in events:
        if kind != "on":
            continue
        if steps and t - steps[-1][0] <= chord_threshold and value not in steps[-1][1]:
            steps[-1][1].append(value)
        else:
            steps.append([t, [value]])
    return steps


def steps_to_sequence(steps):
    """Convert steps into a play list of (delay_before, [chars]) tuples."""
    seq = []
    for i, (t, chars) in enumerate(steps):
        delay = t - steps[i - 1][0] if i else t
        seq.append((delay, chars))
    return seq


# ----------------------------------------------------------------------------
# Playback
# ----------------------------------------------------------------------------

keyboard = Controller()


def press_vp_key(char):
    """Press a single Virtual Piano key, holding Shift for black keys."""
    if DRY_RUN:
        return
    if char in SHIFT_BASE:
        base = SHIFT_BASE[char]
        with keyboard.pressed(Key.shift):
            keyboard.press(base)
            keyboard.release(base)
    elif char.isupper():
        with keyboard.pressed(Key.shift):
            keyboard.press(char.lower())
            keyboard.release(char.lower())
    else:
        keyboard.press(char)
        keyboard.release(char)


# Keyboard rows used for human-error slips: the neighbor of a key is the key
# right next to it on the same row (E slips to R, per the Mistakes setting).
_QWERTY_ROWS = ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm")


def _neighbor_key(char):
    """The key right next to `char` on the keyboard (one key over)."""
    base = SHIFT_BASE.get(char, char.lower())
    for row in _QWERTY_ROWS:
        if base in row:
            i = row.index(base)
            nxt = row[i + 1] if i + 1 < len(row) else row[i - 1]
            return nxt.upper() if char.isupper() else nxt
    return char


def _interruptible_sleep(duration):
    """Sleep for `duration` seconds, returning early (False) if Escape is pressed."""
    end = time.perf_counter() + duration
    while True:
        remaining = end - time.perf_counter()
        if remaining <= 0:
            return True
        if STOP_REQUESTED:
            return False
        time.sleep(min(0.05, remaining))


def play_sequence(seq, label="", char_spacing=KEY_SPACING, step_gap=0.0):
    """Play a song, honoring the speed, keyboard-off and slip settings."""
    if SPEED != 1.0:
        seq = [(d / SPEED, chars) for d, chars in seq]
        char_spacing = char_spacing / SPEED
        step_gap = step_gap / SPEED
    blocker = None
    if BLOCK_KEYBOARD and not DRY_RUN and os.name == "nt":
        blocker = KeyboardBlocker()
        if blocker.start():
            print("[Keyboard off while playing - ESC still stops, Ctrl+E still works]")
        else:
            blocker = None
    try:
        return _play_sequence_inner(seq, label, char_spacing, step_gap)
    finally:
        if blocker:
            blocker.stop()


def _play_sequence_inner(seq, label="", char_spacing=KEY_SPACING, step_gap=0.0):
    """Play a sequence of (delay, [chars]) steps with Escape-to-stop support.

    delay waits before the step, chars are pressed char_spacing apart,
    and step_gap is added after every step.
    """
    # Disarm the ESC hotkey when playback starts: keys typed while filling in
    # the picker (e.g. Escape in the paste box) must not abort a fresh song.
    global STOP_REQUESTED
    STOP_REQUESTED = False
    keys = sum(len(c) for _, c in seq)
    est = sum(d for d, _ in seq) + keys * char_spacing + len(seq) * step_gap
    if DRY_RUN:
        # Show the sequence instantly instead of playing it in real time.
        char_spacing = step_gap = 0.0
        delay_scale = 0.0
    else:
        delay_scale = 1.0
    print(f"\nPlaying {label}: {len(seq)} steps, {keys} keys, ~{est:.0f}s.")
    print("The Roblox window will be focused automatically. Press ESC to stop.\n")

    for remaining in range(int(START_DELAY), 0, -1):
        print(f"  starting in {remaining}...", end="\r", flush=True)
        if not _interruptible_sleep(1.0):
            print("\n[Stopped before start]          ")
            return False
    print(" " * 30, end="\r")

    last_char = None
    key_count = 0
    for delay, chars in seq:
        wait = (delay + (random.uniform(0, HUMANIZE) if HUMANIZE else 0.0)) * delay_scale
        if wait > 0 and not _interruptible_sleep(wait):
            print("\n[Stopped by user]")
            return False
        for c in chars:
            if STOP_REQUESTED:
                print("\n[Stopped by user]")
                return False
            key_count += 1
            play_char = c
            if MISTAKE_EVERY and key_count % MISTAKE_EVERY == 0:
                # Human-error slip: hit the key one over by mistake.
                play_char = _neighbor_key(c)
            if c == last_char and SAME_KEY_GAP:
                time.sleep(SAME_KEY_GAP / SPEED)
            press_vp_key(play_char)
            last_char = play_char
            if not DRY_RUN:
                print(f"  {play_char}" + ("" if play_char == c else f"  (slip: meant {c})"))
            time.sleep(char_spacing)
        time.sleep(step_gap)
    print("\n[Finished]")
    return True


# ----------------------------------------------------------------------------
# Sheet music (.txt) playback - original Virtual Piano sheet format
# ----------------------------------------------------------------------------


def parse_sheet_text(notes):
    """Parse Virtual Piano sheet text into a (delay, [chars]) step list.
    Chords [abc] are struck together, '|' rests DELAY*8, other characters
    (spaces/newlines) rest one DELAY - matching the original script."""

    # Delays land after each step (step_gap=DELAY below), matching the original
    # script: note chars follow one another every DELAY, chords are instant.
    seq = []
    index = 0
    while index < len(notes):
        ch = notes[index]
        if ch.isalnum() or ch in SHIFT_BASE:
            seq.append((0.0, [ch]))
            index += 1
        elif ch == "|":
            seq.append((DELAY * 8, []))
            index += 1
        elif ch == "[":
            index += 1
            chord = []
            while index < len(notes) and notes[index] != "]":
                if notes[index].isalnum() or notes[index] in SHIFT_BASE:
                    chord.append(notes[index])
                index += 1
            index += 1  # skip ']'
            seq.append((0.0, chord))
        else:
            # spaces, newlines etc. rest one beat, like the original script
            seq.append((0.0, []))
            index += 1
    return seq


def play_sheet(path=None, text=None):
    """Play a Virtual Piano sheet from a file path or a raw text string."""
    if text is None:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    seq = parse_sheet_text(text)
    return play_sequence(seq, label=path or "pasted sheet",
                         char_spacing=KEY_SPACING, step_gap=DELAY)


# ----------------------------------------------------------------------------
# Roblox window focus (Windows)
# ----------------------------------------------------------------------------


def focus_roblox():
    """Bring the Roblox window to the foreground. Returns True on success."""
    try:
        user32 = ctypes.windll.user32
        FindWindowW = user32.FindWindowW
        FindWindowW.restype = ctypes.c_void_p
        FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        SetForegroundWindow = user32.SetForegroundWindow
        SetForegroundWindow.argtypes = [ctypes.c_void_p]
        keybd_event = user32.keybd_event

        hwnd = FindWindowW("RobloxWindow", None)

        if not hwnd:
            # Fallback: any top-level window with "roblox" in its title.
            result = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            def enum_callback(h, _):
                length = user32.GetWindowTextLengthW(h)
                if length:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(h, buf, length + 1)
                    if "roblox" in buf.value.lower():
                        result.append(h)
                return True

            user32.EnumWindows(enum_callback, None)
            hwnd = result[0] if result else None

        if not hwnd:
            print("Could not find a Roblox window - switch to it manually now!")
            return False

        # Windows blocks focus stealing; a benign Alt tap unlocks SetForegroundWindow.
        keybd_event(0xA4, 0, 0, 0)  # Alt down
        keybd_event(0xA4, 0, 2, 0)  # Alt up (KEYEVENTF_KEYUP)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE (un-minimize if needed)
        SetForegroundWindow(hwnd)
        time.sleep(0.3)
        print("Focused the Roblox window.")
        return True
    except Exception as exc:
        print(f"Could not focus Roblox ({exc}); switch windows manually now!")
        return False


# ----------------------------------------------------------------------------
# Keyboard & console controls (Windows)
#   - Ctrl+E closes the command window, any time the app is running.
#   - While a song plays the physical keyboard can be turned off: a low-level
#     hook swallows real keystrokes but lets the player's own synthetic keys
#     through, ESC still stops playback, and the keyboard returns as soon as
#     the song ends or is stopped.
# ----------------------------------------------------------------------------


def kill_console_window():
    """Close the command window for good (Ctrl+E). No-op without a console."""
    if os.name != "nt":
        return
    if not _kernel32.GetConsoleWindow():
        return
    if _kernel32.FreeConsole():
        # The console is gone; send prints to the void so a long song can't
        # crash on a dead stdout.
        try:
            _null = open(os.devnull, "w", encoding="utf-8")
            sys.stdout = _null
            sys.stderr = _null
        except Exception:
            pass


def start_console_hotkey():
    """Listen for Ctrl+E while the keyboard is not turned off."""
    try:
        from pynput.keyboard import GlobalHotKeys

        hotkeys = GlobalHotKeys({"<ctrl>+e": kill_console_window})
        hotkeys.daemon = True
        hotkeys.start()
    except Exception:
        pass  # the hotkey is a convenience, never fatal


if os.name == "nt":
    import ctypes.wintypes as _wt

    _WH_KEYBOARD_LL = 13        # low-level keyboard hook
    _LLKHF_INJECTED = 0x10      # event was sent by software, not a real key
    _VK_ESCAPE, _VK_CONTROL, _VK_E = 0x1B, 0x11, 0x45
    _WM_QUIT = 0x0012

    _LRESULT = ctypes.c_ssize_t
    _HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, _wt.WPARAM, _wt.LPARAM)

    class _KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", _wt.DWORD),
            ("scanCode", _wt.DWORD),
            ("flags", _wt.DWORD),
            ("time", _wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _kernel32.GetConsoleWindow.restype = ctypes.c_void_p
    _user32.SetWindowsHookExW.restype = ctypes.c_void_p
    _user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, _HOOKPROC, _wt.HINSTANCE, _wt.DWORD]
    _user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    _user32.PostThreadMessageW.argtypes = [
        _wt.DWORD, _wt.UINT, _wt.WPARAM, _wt.LPARAM]
    _user32.CallNextHookEx.argtypes = [
        ctypes.c_void_p, ctypes.c_int, _wt.WPARAM, _wt.LPARAM]
    _user32.CallNextHookEx.restype = _LRESULT


class KeyboardBlocker:
    """Turn the physical keyboard off while a song plays (Windows)."""

    def __init__(self):
        self._hook = None
        self._proc = None
        self._thread = None
        self._thread_id = 0
        self._quit = threading.Event()

    def start(self):
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        self._thread.start()
        if not ready.wait(3.0):
            print("[!] Could not turn the keyboard off; playing without it.")
            self.stop()
            return False
        return True

    def _run(self, ready):
        self._thread_id = _kernel32.GetCurrentThreadId()

        def hook_proc(n_code, w_param, l_param):
            global STOP_REQUESTED
            if n_code == 0:
                kb = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                if kb.vkCode == _VK_ESCAPE:
                    STOP_REQUESTED = True
                    try:
                        self.stop()
                    except Exception:
                        pass
                    return 1
                if kb.vkCode == _VK_E and _user32.GetAsyncKeyState(_VK_CONTROL) & 0x8000:
                    kill_console_window()
                    return 1
                if not (kb.flags & _LLKHF_INJECTED):
                    return 1
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc = _HOOKPROC(hook_proc)
        self._hook = _user32.SetWindowsHookExW(_WH_KEYBOARD_LL, self._proc, None, 0)
        ready.set()
        if not self._hook:
            print("[!] Could not turn the keyboard off; playing without it.")
            return
        msg = _wt.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0 and not self._quit.is_set():
            pass
        _user32.UnhookWindowsHookEx(self._hook)
        self._hook = None

    def stop(self):
        self._quit.set()
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def app_dir():
    """Folder containing the script (or the frozen .exe itself)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def build_choices():
    """List playable files next to the app: [(path, label, is_default)].

    MIDIs come first, sheet.txt last; the first entry is the default.
    """
    base = app_dir()
    choices = []
    for f in sorted(f for f in os.listdir(base) if f.lower().endswith((".mid", ".midi"))):
        full = os.path.join(base, f)
        size_kb = os.path.getsize(full) // 1024
        choices.append((full, f"{f}  ({size_kb} KB)", False))
    sheet = os.path.join(base, "sheet.txt")
    if os.path.isfile(sheet):
        choices.append((sheet, "sheet.txt  (Virtual Piano sheet)", False))
    if choices:
        choices[0] = (choices[0][0], choices[0][1], True)
    return choices


def choose_with_ui():
    """Open a small picker window with two tabs:
      - 'Files in this folder': every .mid/.midi plus sheet.txt
      - 'Paste a sheet': type/paste Virtual Piano sheet text and play it
    Returns ('file', path, keyboard_off, speed, mistakes),
    ('text', text, keyboard_off, speed, mistakes), or None if canceled."""
    import tkinter as tk
    from tkinter import ttk

    choices = build_choices()
    root = tk.Tk()
    root.title("Roblox Midi AutoPlayer")
    root.resizable(False, False)
    root.attributes("-topmost", True)  # stay visible over Roblox while picking

    chosen = [None]

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=8, pady=8)

    block_var = tk.BooleanVar(value=BLOCK_KEYBOARD)
    options = tk.Frame(root)
    options.pack(fill="x", padx=8)
    tk.Checkbutton(options, text="Turn off keyboard while playing",
                   variable=block_var).pack(side=tk.LEFT)
    tk.Label(options, text="Ctrl+E closes the command window",
             fg="#555").pack(side=tk.RIGHT)

    controls = tk.Frame(root)
    controls.pack(fill="x", padx=8, pady=(2, 6))
    tk.Label(controls, text="Speed:").pack(side=tk.LEFT)
    speed_scale = ttk.Scale(controls, from_=0.25, to=3.0, length=140)
    speed_scale.set(SPEED)
    speed_scale.pack(side=tk.LEFT)
    speed_label = tk.Label(controls, text=f"{SPEED:.2f}x", width=7, anchor="w")
    speed_label.pack(side=tk.LEFT)
    speed_scale.config(command=lambda v: speed_label.config(text=f"{float(v):.2f}x"))
    tk.Label(controls, text="Mistakes:").pack(side=tk.LEFT, padx=(16, 2))
    mode_box = ttk.Combobox(controls, values=("Beginner", "Legit", "Pro"),
                            state="readonly", width=9)
    mode_box.set(MISTAKE_MODE)
    mode_box.pack(side=tk.LEFT)

    # --- Tab 1: files in this folder -------------------------------------
    files_tab = ttk.Frame(notebook)
    notebook.add(files_tab, text="Files in this folder")

    tk.Label(files_tab, text="What should I play?",
             font=("Segoe UI", 11, "bold")).pack(padx=12, pady=(10, 4))

    if choices:
        listbox = tk.Listbox(files_tab, width=48, height=min(len(choices), 10),
                             activestyle="dotbox", exportselection=False)
        for _, label, _default in choices:
            listbox.insert(tk.END, label)
        listbox.selection_set(0)
        listbox.pack(padx=12, pady=6)

        tk.Label(files_tab, text="Roblox will be focused automatically.",
                 fg="#555").pack(padx=12, pady=(0, 6))

        def start(_event=None):
            sel = listbox.curselection()
            if sel:
                chosen[0] = ("file", choices[sel[0]][0], bool(block_var.get()),
                             float(speed_scale.get()), mode_box.get())
            root.destroy()

        listbox.bind("<Double-Button-1>", start)
        listbox.focus_set()

        btns = tk.Frame(files_tab)
        btns.pack(pady=(0, 10))
        tk.Button(btns, text="Start", width=12, command=start).pack(side=tk.LEFT, padx=4)
        tk.Button(btns, text="Quit", width=12, command=root.destroy).pack(side=tk.LEFT, padx=4)
    else:
        tk.Label(files_tab, text="No .mid files or sheet.txt found in this folder.\n"
                            "Drop a MIDI file next to this program, or use the\n"
                            "'Paste a sheet' tab to play something right now.",
                 justify="center").pack(padx=24, pady=16)
        tk.Button(files_tab, text="Quit", width=12,
                  command=root.destroy).pack(pady=(0, 10))

    # --- Tab 2: paste your own sheet -------------------------------------
    paste_tab = ttk.Frame(notebook)
    notebook.add(paste_tab, text="Paste a sheet")

    tk.Label(paste_tab, text="Paste or type a Virtual Piano sheet:",
             font=("Segoe UI", 11, "bold")).pack(padx=12, pady=(10, 2))
    tk.Label(paste_tab, text="lowercase = white keys, UPPERCASE & !@$%^*() = black keys, "
                             "[abc] = chord, | = pause", fg="#555").pack(padx=12, pady=(0, 4))

    paste_hint = tk.Label(paste_tab, text="", fg="#b00020")
    sheet_box = tk.Text(paste_tab, width=56, height=12, wrap="word", undo=True)
    sheet_box.pack(padx=12, pady=2)

    def play_pasted(_event=None):
        content = sheet_box.get("1.0", "end").strip()
        if not content:
            paste_hint.config(text="Paste a sheet first!")
            return
        chosen[0] = ("text", content, bool(block_var.get()),
                     float(speed_scale.get()), mode_box.get())
        root.destroy()

    pbtns = tk.Frame(paste_tab)
    pbtns.pack(pady=8)
    tk.Button(pbtns, text="Play pasted sheet", width=16,
              command=play_pasted).pack(side=tk.LEFT, padx=4)
    tk.Button(pbtns, text="Clear", width=10,
              command=lambda: (sheet_box.delete("1.0", "end"),
                               paste_hint.config(text=""))).pack(side=tk.LEFT, padx=4)
    paste_hint.pack()

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return chosen[0]


def pick_file():
    """Auto-detect what to play: the first .mid/.midi next to the app, else sheet.txt."""
    base = app_dir()  # scan the exe's own folder so double-clicking it just works
    mids = sorted(f for f in os.listdir(base) if f.lower().endswith((".mid", ".midi")))
    if mids and os.path.isfile(os.path.join(base, "sheet.txt")):
        print(f"Found both sheet.txt and {mids[0]}; using {mids[0]}.")
        print("(Run 'python auto.py sheet.txt' to force the sheet instead.)")
    if mids:
        return os.path.join(base, mids[0])
    sheet = os.path.join(base, "sheet.txt")
    return sheet if os.path.isfile(sheet) else None


def main():
    global DRY_RUN, BLOCK_KEYBOARD, SPEED, MISTAKE_EVERY

    args = sys.argv[1:]
    track = None
    if "--track" in args:
        i = args.index("--track")
        if i + 1 < len(args):
            track = int(args[i + 1])
            del args[i:i + 2]
    list_tracks = "--list" in args
    if list_tracks:
        args.remove("--list")
    if "--allow-keyboard" in args:
        args.remove("--allow-keyboard")
        BLOCK_KEYBOARD = False
        print("[Keyboard stays on while playing]")
    if "--dry-run" in args:
        args.remove("--dry-run")
        DRY_RUN = True
        print("[Dry run: no keys will be pressed]")

    path = args[0] if args else None
    if path is None and not list_tracks and not DRY_RUN:
        # Plain launch (or double-click): open the file picker UI.
        start_console_hotkey()
        print("Tip: press Ctrl+E to close this command window.")
        try:
            picked = choose_with_ui()
        except Exception as exc:
            print(f"Could not open the picker UI ({exc}); falling back to auto-detect.")
            picked = pick_file()
        if picked is None:
            print("Nothing selected.")
            return 0
        if isinstance(picked, str):  # auto-detect fallback returned a path
            picked = ("file", picked, True, 1.0, MISTAKE_MODE)
        kind, value, block_kb, speed, mode = picked
        BLOCK_KEYBOARD = bool(block_kb)
        SPEED = max(0.1, float(speed) or 1.0)
        MISTAKE_EVERY = MISTAKE_MAP.get(mode, MISTAKE_EVERY)
        try:
            if not DRY_RUN:
                focus_roblox()
            if kind == "file":
                path = value
            else:
                return 0 if play_sheet(text=value) else 1
        except KeyboardInterrupt:
            print("\n[Stopped by user]")
            return 0
    elif path is None:
        path = pick_file()

    if path is None:
        print("No .mid or sheet.txt found. Drop a MIDI file next to auto.py or run:")
        print("    python auto.py path\\to\\song.mid")
        return 1
    if not os.path.isfile(path):
        print(f"File not found: {path}")
        return 1

    print(f"Selected: {path}")

    if path.lower().endswith((".mid", ".midi")):
        try:
            events = load_midi_events(path, track=track)
        except ImportError:
            print("Missing dependency 'mido'. Install it with:  pip install mido")
            return 1
        metas = [v for _, k, v in events if k == "meta"]
        note_count = sum(1 for _, k, _ in events if k == "on")
        if list_tracks:
            print("Tracks in this MIDI file (drum tracks are skipped automatically):")
            for m in metas:
                print(f"  {m}")
            return 0
        if note_count == 0:
            print("No playable notes found (try --list to inspect tracks).")
            return 1
        for m in metas:
            print(f"  {m}")
        print(f"  {note_count} playable notes")

        try:
            seq = steps_to_sequence(events_to_steps(events))
            if not DRY_RUN:
                focus_roblox()
            play_sequence(seq, label=path)
        except KeyboardInterrupt:
            print("\n[Stopped by user]")
    else:
        try:
            if not DRY_RUN:
                focus_roblox()
            play_sheet(path)
        except KeyboardInterrupt:
            print("\n[Stopped by user]")
    return 0


if __name__ == "__main__":
    _code = main()
    if _code and getattr(sys, "frozen", False) and sys.stdin and sys.stdin.isatty():
        # Keep a double-clicked exe's console open so the error can be read.
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
    sys.exit(_code)
