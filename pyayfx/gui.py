from __future__ import annotations

import math
import os
import sys
import tempfile
import tkinter as tk
from array import array
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .formats import (
    export_csv,
    export_vt2,
    export_wav,
    import_psg,
    import_vgm,
    import_wav,
    load_afb,
    load_afx_pair,
    render_scc_wave_bytes,
    render_wav_bytes,
    save_afb,
    save_afx_pair,
)
from .model import MAX_FX_LEN, Bank, Effect, Frame, clean_effect_filename
from .sfx_tools import (
    copy_psg_to_scc,
    echo,
    fade_volume,
    gate,
    generate_template,
    interpolate_selected,
    invert_wave,
    noise_wave,
    normalize_wave,
    randomize_psg_noise,
    randomize_period,
    reverse_selected,
    rotate_wave,
    saw_wave,
    sine_wave,
    smooth_wave,
    square_wave,
    sweep_period,
    tremolo,
    triangle_wave,
)
from .wavetable import byte_to_signed, parse_wavetable_asm, save_wavetable_asm, signed_to_byte

TITLE = "AY Sound FX Editor Python"
LINE_HEIGHT = 18
GRID_TOP = 28
BAR_WIDTH = 260
SCC_PERIOD_COL = 250
SCC_VOLUME_COL = 314
SCC_WAVEFORM_COL = 362
AY_PERIOD_BAR = 430
AY_VOLUME_BAR = 700
AY_NOISE_BAR = 770
SCC_PERIOD_BAR = 850
SCC_VOLUME_BAR = 1120
SCC_WAVEFORM_BAR = 1210
DEFAULT_WAVETABLE = "wavetable.asm"

# Transferable columns. Right-clicking works on the bars themselves; their
# small gutter also provides a target that cannot change the input value.
SELECTABLE_COLUMNS = (
    ("psg", "tone", AY_PERIOD_BAR, AY_PERIOD_BAR + BAR_WIDTH),
    ("psg", "volume", AY_VOLUME_BAR, AY_VOLUME_BAR + 60),
    ("scc", "tone", SCC_PERIOD_BAR, SCC_PERIOD_BAR + BAR_WIDTH),
    ("scc", "volume", SCC_VOLUME_BAR, SCC_VOLUME_BAR + 80),
)
SELECTION_CURSOR_COLUMNS = (0, 1, 3, 4)
TRANSFER_COLUMN_RANGES = {"psg": (0, 1), "scc": (2, 3)}
TEXT_CELL_COLUMNS = (
    (0, 100, 151),
    (1, 152, 195),
    (3, 242, 307),
    (4, 308, 355),
)
TEXT_CHANNEL_RANGES = {"psg": (100, 195), "scc": (242, 355)}
NON_SELECTABLE_TEXT_RANGES = ((196, 239), (356, 425))
SELECTION_GUTTER_WIDTH = 8
UNDO_LIMIT = 50


@dataclass
class UndoState:
    bank: Bank
    effects: list[Effect]
    current_index: int
    effect: Effect
    effect_name: str
    frames: array
    scc_frames: array
    wavetable_names: list[str]
    wavetables: list[list[int]]


class AyFxEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(TITLE)
        self.geometry("1380x700")
        self.minsize(1320, 420)

        self.bank = Bank()
        self.current_index = 0
        self.cursor_row = 0
        self.cursor_col = 0
        self.clipboard: list[Frame] = []
        self.scc_clipboard: list[Frame] = []
        self.column_clipboard: dict[str, object] | None = None
        self.column_selection: tuple[int, int, int, int, str] | None = None
        self._right_anchor: tuple[int, int, str] | None = None
        self._right_dragged = False
        self._right_popup_allowed = False
        self.hover_channel: str | None = None
        self.undo_stack: list[UndoState] = []
        self._left_drag_undo_recorded = False
        self.period_linear = tk.BooleanVar(value=True)
        self.export_all = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready")
        self._play_file: str | None = None
        self.load_default_wavetable()

        self._build_menu()
        self._build_toolbar()
        self._build_editor()
        self._build_status()
        self._bind_keys()
        self.refresh_all()

    @property
    def effect(self) -> Effect:
        return self.bank.effects[self.current_index]

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="New bank", command=self.new_bank, accelerator="Ctrl+N")
        file_menu.add_command(label="Load bank...", command=self.load_bank, accelerator="Ctrl+O")
        file_menu.add_command(label="Save bank...", command=lambda: self.save_bank(True), accelerator="Ctrl+S")
        file_menu.add_command(label="Save bank w/o names...", command=lambda: self.save_bank(False))
        file_menu.add_command(label="Save split banks of 32...", command=self.save_split_banks)
        file_menu.add_separator()
        file_menu.add_command(label="Clear current effect", command=self.clear_effect)
        file_menu.add_command(label="Clear PSG part", command=self.clear_psg_effect)
        file_menu.add_command(label="Clear SCC part", command=self.clear_scc_effect)
        file_menu.add_command(label="Load current effect...", command=self.load_effect)
        file_menu.add_command(label="Save current effect...", command=self.save_effect)
        file_menu.add_command(label="Multi-load to bank...", command=self.multi_load)
        file_menu.add_command(label="Multi-save from bank...", command=self.multi_save)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="Undo", command=self.undo, accelerator="Ctrl+Z")
        edit_menu.add_separator()
        edit_menu.add_command(label="Cut", command=self.cut, accelerator="Ctrl+X")
        edit_menu.add_command(label="Copy", command=self.copy, accelerator="Ctrl+C")
        edit_menu.add_command(label="Paste", command=self.paste, accelerator="Ctrl+V")
        edit_menu.add_command(label="Delete", command=self.delete_selection, accelerator="Del")
        edit_menu.add_separator()
        edit_menu.add_command(label="Select all", command=self.select_all, accelerator="Ctrl+A")
        edit_menu.add_command(label="Unselect all", command=self.unselect_all)
        edit_menu.add_command(label="Inverse selection", command=self.inverse_selection, accelerator="Ctrl+I")
        menubar.add_cascade(label="Edit", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_radiobutton(label="Linear period", variable=self.period_linear, value=True, command=self.refresh_grid)
        view_menu.add_radiobutton(label="Logarithmic period", variable=self.period_linear, value=False, command=self.refresh_grid)
        menubar.add_cascade(label="View", menu=view_menu)

        wave_menu = tk.Menu(menubar, tearoff=False)
        wave_menu.add_command(label="Load wavetable...", command=self.load_wavetable)
        wave_menu.add_command(label="Save wavetable...", command=self.save_wavetable)
        wave_menu.add_command(label="Edit wavetables...", command=self.edit_wavetables)
        menubar.add_cascade(label="Wavetable", menu=wave_menu)

        tools_menu = tk.Menu(menubar, tearoff=False)
        generate_menu = tk.Menu(tools_menu, tearoff=False)
        for label, template in (
            ("Jump", "jump"),
            ("Coin", "coin"),
            ("Laser", "laser"),
            ("Hit", "hit"),
            ("Explosion", "explosion"),
            ("Powerup", "powerup"),
            ("Pickup", "pickup"),
            ("Splash", "splash"),
            ("Rumble", "rumble"),
        ):
            generate_menu.add_command(label=label, command=lambda name=template: self.generate_sfx(name))
        tools_menu.add_cascade(label="Generate SFX", menu=generate_menu)
        tools_menu.add_separator()
        tools_menu.add_command(label="Fade PSG out", command=lambda: self.apply_fade("psg", 15, 0))
        tools_menu.add_command(label="Fade SCC out", command=lambda: self.apply_fade("scc", 15, 0))
        tools_menu.add_command(label="Fade both out", command=lambda: self.apply_fade("both", 15, 0))
        tools_menu.add_command(label="Pitch sweep PSG...", command=lambda: self.apply_sweep("psg"))
        tools_menu.add_command(label="Pitch sweep SCC...", command=lambda: self.apply_sweep("scc"))
        tools_menu.add_command(label="Randomize PSG noise", command=self.randomize_noise)
        tools_menu.add_command(label="Randomize period...", command=self.apply_period_jitter)
        tools_menu.add_command(label="Tremolo both...", command=self.apply_tremolo)
        tools_menu.add_command(label="Gate/stutter both...", command=self.apply_gate)
        tools_menu.add_command(label="Echo both...", command=self.apply_echo)
        tools_menu.add_command(label="Interpolate selection", command=self.interpolate)
        tools_menu.add_command(label="Reverse selection", command=self.reverse)
        tools_menu.add_command(label="Copy PSG period/volume to SCC", command=self.copy_to_scc)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        bank_menu = tk.Menu(menubar, tearoff=False)
        bank_menu.add_command(label="Add new effect", command=self.add_effect)
        bank_menu.add_command(label="Delete effect", command=self.delete_effect)
        bank_menu.add_command(label="Insert new effect", command=self.insert_effect)
        menubar.add_cascade(label="Bank", menu=bank_menu)

        import_menu = tk.Menu(menubar, tearoff=False)
        import_menu.add_command(label="PSG for AY...", command=self.import_psg_dialog)
        import_menu.add_command(label="Wave file...", command=self.import_wav_dialog)
        import_menu.add_command(label="VTX file...", command=lambda: self.unsupported("VTX import needs an LH5 decoder port."))
        import_menu.add_command(label="VGM file...", command=self.import_vgm_dialog)
        menubar.add_cascade(label="Import", menu=import_menu)

        export_menu = tk.Menu(menubar, tearoff=False)
        export_menu.add_command(label="VTII sample...", command=self.export_vt2_dialog)
        export_menu.add_command(label="Wave file...", command=self.export_wav_dialog)
        export_menu.add_command(label="CSV...", command=self.export_csv_dialog)
        export_menu.add_separator()
        export_menu.add_radiobutton(label="Current effect", variable=self.export_all, value=False)
        export_menu.add_radiobutton(label="All effects", variable=self.export_all, value=True)
        menubar.add_cascade(label="Export", menu=export_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About", command=lambda: messagebox.showinfo("About", TITLE))
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self, padding=(6, 5))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Play", command=self.play).pack(side="left")
        ttk.Button(toolbar, text="PSG", command=lambda: self.play(channel="psg")).pack(side="left", padx=(4, 0))
        ttk.Button(toolbar, text="SCC", command=lambda: self.play(channel="scc")).pack(side="left", padx=(4, 0))
        ttk.Button(toolbar, text="Stop", command=self.stop).pack(side="left", padx=(4, 12))
        ttk.Button(toolbar, text="Add", command=self.add_effect).pack(side="left")
        ttk.Button(toolbar, text="Del", command=self.delete_effect).pack(side="left", padx=(4, 12))
        ttk.Button(toolbar, text="|<", width=3, command=lambda: self.go_effect(0)).pack(side="left")
        ttk.Button(toolbar, text="<", width=3, command=lambda: self.go_effect(self.current_index - 1)).pack(side="left")
        self.count_label = ttk.Label(toolbar, width=9, anchor="center")
        self.count_label.pack(side="left", padx=4)
        ttk.Button(toolbar, text=">", width=3, command=lambda: self.go_effect(self.current_index + 1)).pack(side="left")
        ttk.Button(toolbar, text=">|", width=3, command=lambda: self.go_effect(len(self.bank.effects) - 1)).pack(side="left", padx=(0, 12))
        ttk.Label(toolbar, text="Name").pack(side="left")
        self.name_var = tk.StringVar()
        name_entry = ttk.Entry(toolbar, textvariable=self.name_var, width=32)
        name_entry.pack(side="left", padx=4, fill="x", expand=True)
        name_entry.bind("<Return>", lambda _event: self.commit_name())
        name_entry.bind("<FocusOut>", lambda _event: self.commit_name())

    def _build_editor(self) -> None:
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(outer, background="white", highlightthickness=0, takefocus=True)
        yscroll = ttk.Scrollbar(outer, orient="vertical", command=self.on_scrollbar)
        self.canvas.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self.yscroll = yscroll
        self.canvas.bind("<Configure>", lambda _event: self.refresh_grid())
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_left_release)
        self.canvas.bind("<B3-Motion>", self.on_right_drag)
        self.canvas.bind("<ButtonRelease-3>", self.on_right_release)
        self.canvas.bind("<Motion>", self.on_canvas_motion)
        self.canvas.bind("<Leave>", self.on_canvas_leave)
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)

    def _build_status(self) -> None:
        ttk.Label(self, textvariable=self.status, anchor="w", padding=(6, 3)).pack(fill="x")

    def _bind_keys(self) -> None:
        self.bind("<Control-n>", lambda _event: self.new_bank())
        self.bind("<Control-o>", lambda _event: self.load_bank())
        self.bind("<Control-s>", lambda _event: self.save_bank(True))
        self.bind("<Control-a>", lambda _event: self.select_all())
        self.bind("<Control-i>", lambda _event: self.inverse_selection())
        self.canvas.bind("<Control-x>", self.on_cut_shortcut)
        self.canvas.bind("<Control-c>", self.on_copy_shortcut)
        self.canvas.bind("<Control-v>", self.on_paste_shortcut)
        self.canvas.bind("<Control-z>", self.on_undo_shortcut)
        self.bind("<Delete>", lambda _event: self.delete_selection())
        self.bind("<Insert>", lambda event: self.insert_frame(clone=bool(event.state & 0x4)))
        self.bind("<Up>", lambda _event: self.move_cursor(0, -1))
        self.bind("<Down>", lambda _event: self.move_cursor(0, 1))
        self.bind("<Left>", lambda _event: self.move_cursor(-1, 0))
        self.bind("<Right>", lambda _event: self.move_cursor(1, 0))
        self.bind("<Home>", lambda _event: self.set_cursor(0))
        self.bind("<End>", lambda _event: self.set_cursor(max(0, self.effect.real_len() - 1)))
        self.bind("<Return>", lambda _event: self.play())
        self.bind("<Control-Return>", lambda _event: self.play(from_cursor=True))
        self.bind("<space>", lambda _event: self.stop())
        self.bind("t", lambda _event: self.toggle_flag("t"))
        self.bind("n", lambda _event: self.toggle_flag("n"))
        for char in "0123456789abcdefABCDEF":
            self.bind(char, self.enter_hex)

    def on_cut_shortcut(self, _event: tk.Event) -> str:
        self.cut()
        return "break"

    def on_copy_shortcut(self, _event: tk.Event) -> str:
        self.copy()
        return "break"

    def on_paste_shortcut(self, _event: tk.Event) -> str:
        self.paste()
        return "break"

    def on_undo_shortcut(self, _event: tk.Event) -> str:
        self.undo()
        return "break"

    @staticmethod
    def _pack_frames(frames: list[Frame]) -> array:
        return array(
            "I",
            (
                frame.tone
                | (frame.noise << 12)
                | (frame.volume << 17)
                | (int(frame.t) << 21)
                | (int(frame.n) << 22)
                | (int(frame.selected) << 23)
                for frame in frames
            ),
        )

    @staticmethod
    def _unpack_frames(values: array) -> list[Frame]:
        return [
            Frame(
                tone=value & 0x0FFF,
                noise=(value >> 12) & 0x1F,
                volume=(value >> 17) & 0x0F,
                t=bool(value & (1 << 21)),
                n=bool(value & (1 << 22)),
                selected=bool(value & (1 << 23)),
            )
            for value in values
        ]

    def _record_undo(self) -> None:
        effect = self.effect
        self.undo_stack.append(
            UndoState(
                bank=self.bank,
                effects=list(self.bank.effects),
                current_index=self.current_index,
                effect=effect,
                effect_name=effect.name,
                frames=self._pack_frames(effect.frames),
                scc_frames=self._pack_frames(effect.scc_frames),
                wavetable_names=list(self.bank.wavetable_names),
                wavetables=[list(wave) for wave in self.bank.wavetables],
            )
        )
        if len(self.undo_stack) > UNDO_LIMIT:
            del self.undo_stack[0]

    def undo(self) -> None:
        if not self.undo_stack:
            self.status.set("Nothing to undo")
            return
        state = self.undo_stack.pop()
        self.bank = state.bank
        self.bank.effects = list(state.effects)
        state.effect.name = state.effect_name
        state.effect.frames = self._unpack_frames(state.frames)
        state.effect.scc_frames = self._unpack_frames(state.scc_frames)
        self.bank.wavetable_names = list(state.wavetable_names)
        self.bank.wavetables = [list(wave) for wave in state.wavetables]
        self.current_index = max(0, min(state.current_index, len(self.bank.effects) - 1))
        self.column_selection = None
        self.status.set(f"Undo ({len(self.undo_stack)} action(s) remaining)")
        self.refresh_all()

    def refresh_all(self) -> None:
        self.name_var.set(self.effect.name)
        self.count_label.configure(text=f"{self.current_index + 1:03d}/{len(self.bank.effects):03d}")
        title = TITLE
        if self.bank.path:
            title += f" [{self.bank.path.name}]"
        self.title(title)
        self.refresh_grid()

    def refresh_grid(self) -> None:
        width = max(self.canvas.winfo_width(), 1320)
        visible_rows = self._visible_rows()
        self.canvas.delete("all")
        if self.hover_channel:
            x1, x2 = TEXT_CHANNEL_RANGES[self.hover_channel]
            self.canvas.create_rectangle(x1, 0, x2, GRID_TOP, fill="#dce9ff", outline="")
            for channel, _field, bar_x1, bar_x2 in SELECTABLE_COLUMNS:
                if channel == self.hover_channel:
                    self.canvas.create_rectangle(bar_x1, 0, bar_x2, GRID_TOP, fill="#dce9ff", outline="")
        headers = [
            ("Pos", 8),
            ("T", 52),
            ("N", 76),
            ("Per", 108),
            ("V", 160),
            ("Ns", 202),
            ("SPer", SCC_PERIOD_COL),
            ("SVol", SCC_VOLUME_COL),
            ("SWf", SCC_WAVEFORM_COL),
            ("AY Period", AY_PERIOD_BAR),
            ("AY Vol", AY_VOLUME_BAR),
            ("AY Noise", AY_NOISE_BAR),
            ("SCC Period", SCC_PERIOD_BAR),
            ("SCC Vol", SCC_VOLUME_BAR),
            ("Wave", SCC_WAVEFORM_BAR),
        ]
        for text, x in headers:
            self.canvas.create_text(x, 8, text=text, anchor="nw", fill="#222")
        for row in range(visible_rows):
            index = self._top_row() + row
            if index >= MAX_FX_LEN:
                break
            self.draw_row(index, row, width)
        self.yscroll.set(self._top_row() / MAX_FX_LEN, min(1, (self._top_row() + visible_rows) / MAX_FX_LEN))

    def draw_row(self, index: int, row: int, width: int) -> None:
        frame = self.effect.frames[index]
        scc = self.scc_frame(index)
        y = GRID_TOP + row * LINE_HEIGHT
        actual = index < self.effect.real_len()
        bg = "#dff3df" if frame.selected else "#ffffff"
        if index == self.cursor_row:
            bg = "#e8f0ff"
        self.canvas.create_rectangle(0, y, width, y + LINE_HEIGHT, fill=bg, outline="")
        if self.hover_channel:
            x1, x2 = TEXT_CHANNEL_RANGES[self.hover_channel]
            self.canvas.create_rectangle(x1, y, x2, y + LINE_HEIGHT, fill="#edf4ff", outline="")
        color = "#111" if actual else "#888"
        self.canvas.create_text(8, y + 2, text=f"{index:03X}", anchor="nw", fill=color)
        self.canvas.create_text(54, y + 2, text="T" if frame.t else "-", anchor="nw", fill=color)
        self.canvas.create_text(78, y + 2, text="N" if frame.n else "-", anchor="nw", fill=color)
        initial_waveform = self.scc_initial_waveform()
        effective_waveform = initial_waveform
        wave_name = self.bank.wavetable_names[effective_waveform] if 0 <= effective_waveform < len(self.bank.wavetable_names) else ""
        values = [
            f"{frame.tone:03X}",
            f"{frame.volume:X}",
            f"{frame.noise:02X}",
            f"{scc.tone:03X}",
            f"{scc.volume:X}",
            f"{effective_waveform:02X}",
        ]
        xs = [108, 160, 202, SCC_PERIOD_COL, SCC_VOLUME_COL, SCC_WAVEFORM_COL]
        for col, (value, x) in enumerate(zip(values, xs)):
            if index == self.cursor_row and col == self.cursor_col:
                self.canvas.create_rectangle(x - 3, y + 1, x + 38, y + LINE_HEIGHT - 1, fill="#111", outline="")
                fill = "#fff"
            else:
                fill = color
            self.canvas.create_text(x, y + 2, text=value, anchor="nw", fill=fill)
        self.canvas.create_text(SCC_WAVEFORM_COL + 30, y + 2, text=wave_name[:16], anchor="nw", fill="#555")
        self._draw_selection_gutters(y)
        self._bar(AY_PERIOD_BAR, y + 3, BAR_WIDTH, LINE_HEIGHT - 6, self._period_width(frame.tone), "#717b91")
        self._bar(AY_VOLUME_BAR, y + 3, 60, LINE_HEIGHT - 6, frame.volume / 15 if frame.volume else 0, "#b46b5f")
        self._bar(AY_NOISE_BAR, y + 3, 60, LINE_HEIGHT - 6, frame.noise / 31 if frame.noise else 0, "#4f8f72")
        self._bar(SCC_PERIOD_BAR, y + 3, BAR_WIDTH, LINE_HEIGHT - 6, self._period_width(scc.tone), "#806f9a")
        self._bar(SCC_VOLUME_BAR, y + 3, 80, LINE_HEIGHT - 6, scc.volume / 15 if scc.volume else 0, "#b46b5f")
        self._bar(SCC_WAVEFORM_BAR, y + 3, 120, LINE_HEIGHT - 6, effective_waveform / 31 if effective_waveform else 0, "#4f8f72")
        self._draw_column_hover(y)
        self._draw_column_selection(index, y)

    def _bar(self, x: int, y: int, w: int, h: int, frac: float, color: str) -> None:
        self.canvas.create_rectangle(x, y, x + w, y + h, fill="#f4f4f4", outline="#d0d0d0")
        if frac > 0:
            self.canvas.create_rectangle(x, y, x + max(2, int(w * min(1, frac))), y + h, fill=color, outline=color)

    def _draw_column_selection(self, index: int, y: int) -> None:
        if not self.column_selection:
            return
        first_row, last_row, first_col, last_col, _channel = self.column_selection
        if not first_row <= index <= last_row:
            return
        for column in range(first_col, last_col + 1):
            _channel, _field, x1, x2 = SELECTABLE_COLUMNS[column]
            self.canvas.create_rectangle(
                x1,
                y + 1,
                x2,
                y + LINE_HEIGHT - 1,
                fill="#8fb7ff",
                stipple="gray25",
                outline="#2f6fd0",
                width=2,
            )

    def _draw_column_hover(self, y: int) -> None:
        if not self.hover_channel:
            return
        for channel, _field, x1, x2 in SELECTABLE_COLUMNS:
            if channel == self.hover_channel:
                self.canvas.create_rectangle(
                    x1,
                    y + 1,
                    x2,
                    y + LINE_HEIGHT - 1,
                    fill="#a9c8ff",
                    stipple="gray12",
                    outline="#8aafea",
                )

    def _draw_selection_gutters(self, y: int) -> None:
        for channel, _field, x1, _x2 in SELECTABLE_COLUMNS:
            fill = "#bcd3ff" if channel == self.hover_channel else "#eeeeee"
            self.canvas.create_rectangle(
                x1 - SELECTION_GUTTER_WIDTH,
                y + 1,
                x1 - 2,
                y + LINE_HEIGHT - 1,
                fill=fill,
                outline="#c5c5c5",
            )

    def _period_width(self, period: int) -> float:
        if period <= 0:
            return 0.0
        if self.period_linear.get():
            return min(1.0, period / 4095)
        return min(1.0, math.log(period / 8.0) / math.log(4095.0 / 8.0)) if period > 8 else 0.02

    def scc_effect_frames(self) -> list[Frame]:
        return self.effect.scc_frames

    def scc_frame(self, index: int) -> Frame:
        return self.scc_effect_frames()[index]

    def scc_initial_waveform(self) -> int:
        return self.effect.scc_frames[0].noise if self.effect.scc_frames else 0

    def _clear_scc_frames(self) -> None:
        self.effect.scc_frames = [Frame() for _ in range(MAX_FX_LEN)]

    def clear_psg_effect(self) -> None:
        self._record_undo()
        self.effect.frames = [Frame() for _ in range(MAX_FX_LEN)]
        self.status.set("PSG part cleared")
        self.refresh_grid()

    def clear_scc_effect(self) -> None:
        self._record_undo()
        self._clear_scc_frames()
        self.status.set("SCC part cleared")
        self.refresh_grid()

    def _visible_rows(self) -> int:
        available_height = self.canvas.winfo_height() - GRID_TOP
        return max(1, min(MAX_FX_LEN, available_height // LINE_HEIGHT))

    def _top_row(self) -> int:
        return max(0, min(MAX_FX_LEN - self._visible_rows(), getattr(self, "top_row", 0)))

    def ensure_cursor_visible(self) -> None:
        top = self._top_row()
        visible_rows = self._visible_rows()
        if self.cursor_row < top:
            self.top_row = self.cursor_row
        elif self.cursor_row >= top + visible_rows:
            self.top_row = self.cursor_row - visible_rows + 1

    def set_cursor(self, row: int) -> None:
        self.cursor_row = max(0, min(MAX_FX_LEN - 1, row))
        self.ensure_cursor_visible()
        self.refresh_grid()

    def move_cursor(self, dx: int, dy: int) -> None:
        self.cursor_col = max(0, min(5, self.cursor_col + dx))
        self.cursor_row = max(0, min(MAX_FX_LEN - 1, self.cursor_row + dy))
        self.ensure_cursor_visible()
        self.refresh_grid()

    def enter_hex(self, event: tk.Event) -> None:
        try:
            digit = int(event.char, 16)
        except ValueError:
            return
        if self.cursor_col == 5 and self.cursor_row != 0:
            self.status.set("SCC waveform is fixed by frame 000")
            return
        self._record_undo()
        frame = self.effect.frames[self.cursor_row]
        if self.cursor_col == 0:
            frame.tone = ((frame.tone << 4) | digit) & 0x0fff
        elif self.cursor_col == 1:
            frame.volume = digit & 0x0f
        elif self.cursor_col == 2:
            frame.noise = ((frame.noise << 4) | digit) & 0x1f
        else:
            scc = self.scc_frame(self.cursor_row)
            if self.cursor_col == 3:
                scc.tone = ((scc.tone << 4) | digit) & 0x0fff
            elif self.cursor_col == 4:
                scc.volume = digit & 0x0f
            elif self.cursor_col == 5:
                scc.noise = ((scc.noise << 4) | digit) & 0x1f
        self.refresh_grid()

    def toggle_flag(self, flag: str) -> None:
        self._record_undo()
        frame = self.effect.frames[self.cursor_row]
        setattr(frame, flag, not getattr(frame, flag))
        self.refresh_grid()

    def on_click(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        self.column_selection = None
        self._left_drag_undo_recorded = self._pointer_edits_data(event.x)
        if self._left_drag_undo_recorded:
            self._record_undo()
        self._handle_pointer(event.x, event.y, right=False)

    def on_right_click(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        cell = self._selection_cell_at(event.x, event.y)
        self._right_dragged = False
        self._right_popup_allowed = cell is not None
        self._right_anchor = cell
        if cell is not None:
            row, column, channel = cell
            if not self._column_selection_contains(row, column, channel):
                first_transfer_col, last_transfer_col = TRANSFER_COLUMN_RANGES[channel]
                self.column_selection = (
                    row,
                    row,
                    min(column, first_transfer_col),
                    max(column, last_transfer_col),
                    channel,
                )
            self.cursor_row = row
            self.cursor_col = SELECTION_CURSOR_COLUMNS[column]
            self.refresh_grid()
        else:
            target = self._text_cell_at(event.x, event.y)
            if target is not None:
                self._right_popup_allowed = True
                self.column_selection = None
                self._set_cell_target(*target)
                self.refresh_grid()

    def on_right_drag(self, event: tk.Event) -> None:
        if self._right_anchor is None:
            return
        cell = self._selection_cell_at(event.x, event.y)
        if cell is None or cell[2] != self._right_anchor[2]:
            return
        anchor_row, anchor_col, channel = self._right_anchor
        row, column, _channel = cell
        first_transfer_col, last_transfer_col = TRANSFER_COLUMN_RANGES[channel]
        self._right_dragged = True
        self.column_selection = (
            min(anchor_row, row),
            max(anchor_row, row),
            min(anchor_col, column, first_transfer_col),
            max(anchor_col, column, last_transfer_col),
            channel,
        )
        self.cursor_row = row
        self.cursor_col = SELECTION_CURSOR_COLUMNS[column]
        self.status.set(f"Selected {channel.upper()} rows {min(anchor_row, row):03X}-{max(anchor_row, row):03X}")
        self.refresh_grid()

    def on_right_release(self, event: tk.Event) -> None:
        self._right_anchor = None
        if not self._right_dragged and self._right_popup_allowed:
            self._show_edit_popup(event)

    def _selection_cell_at(self, x: int, y: int) -> tuple[int, int, str] | None:
        visible_row = (y - GRID_TOP) // LINE_HEIGHT
        if visible_row < 0 or visible_row >= self._visible_rows():
            return None
        row = self._top_row() + visible_row
        if row >= MAX_FX_LEN:
            return None
        for column, (channel, _field, x1, x2) in enumerate(SELECTABLE_COLUMNS):
            if x1 - SELECTION_GUTTER_WIDTH <= x <= x2:
                return row, column, channel
        return None

    def _selectable_column_at(self, x: int) -> tuple[int, str] | None:
        for column, (channel, _field, x1, x2) in enumerate(SELECTABLE_COLUMNS):
            if x1 - SELECTION_GUTTER_WIDTH <= x <= x2:
                return column, channel
        return None

    def _selection_gutter_at(self, x: int) -> tuple[int, str] | None:
        for column, (channel, _field, x1, _x2) in enumerate(SELECTABLE_COLUMNS):
            if x1 - SELECTION_GUTTER_WIDTH <= x <= x1 - 2:
                return column, channel
        return None

    def _visible_row_at(self, y: int) -> int | None:
        visible_row = (y - GRID_TOP) // LINE_HEIGHT
        if visible_row < 0 or visible_row >= self._visible_rows():
            return None
        row = self._top_row() + visible_row
        return row if row < MAX_FX_LEN else None

    def _text_cell_at(self, x: int, y: int) -> tuple[int, int] | None:
        row = self._visible_row_at(y)
        if row is None:
            return None
        for column, x1, x2 in TEXT_CELL_COLUMNS:
            if x1 <= x <= x2:
                return row, column
        return None

    def _text_channel_at(self, x: int) -> str | None:
        for channel, (x1, x2) in TEXT_CHANNEL_RANGES.items():
            if x1 <= x <= x2:
                return channel
        return None

    def on_canvas_motion(self, event: tk.Event) -> None:
        channel = self._text_channel_at(event.x) if event.y >= GRID_TOP else None
        if channel is None and event.y >= GRID_TOP:
            selectable = self._selectable_column_at(event.x)
            channel = selectable[1] if selectable else None
        if channel != self.hover_channel:
            self.hover_channel = channel
            self.refresh_grid()

    def on_canvas_leave(self, _event: tk.Event) -> None:
        if self.hover_channel is not None:
            self.hover_channel = None
            self.refresh_grid()

    def _column_selection_contains(self, row: int, column: int, channel: str) -> bool:
        if not self.column_selection:
            return False
        first_row, last_row, first_col, last_col, selected_channel = self.column_selection
        return selected_channel == channel and first_row <= row <= last_row and first_col <= column <= last_col

    def _show_edit_popup(self, event: tk.Event) -> None:
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Copy", command=self.copy)
        menu.add_command(label="Cut", command=self.cut)
        menu.add_command(label="Paste", command=self.paste)
        menu.add_separator()
        menu.add_command(label="Clear selection", command=self.unselect_all)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            self.canvas.focus_set()

    def _set_cell_target(self, row: int, column: int) -> None:
        self.cursor_row = row
        self.cursor_col = column
        channel = "PSG" if column <= 2 else "SCC"
        self.status.set(f"Paste target: {channel} row {row:03X}")

    def on_drag(self, event: tk.Event) -> None:
        if not self._left_drag_undo_recorded and self._pointer_edits_data(event.x):
            self._record_undo()
            self._left_drag_undo_recorded = True
        self._handle_pointer(event.x, event.y, right=False, drag=True)

    def on_left_release(self, _event: tk.Event) -> None:
        self._left_drag_undo_recorded = False

    @staticmethod
    def _pointer_edits_data(x: int) -> bool:
        return (
            48 <= x <= 92
            or AY_PERIOD_BAR <= x <= AY_PERIOD_BAR + BAR_WIDTH
            or AY_VOLUME_BAR <= x <= AY_VOLUME_BAR + 60
            or AY_NOISE_BAR <= x <= AY_NOISE_BAR + 60
            or SCC_PERIOD_BAR <= x <= SCC_PERIOD_BAR + BAR_WIDTH
            or SCC_VOLUME_BAR <= x <= SCC_VOLUME_BAR + 80
            or SCC_WAVEFORM_BAR <= x <= SCC_WAVEFORM_BAR + 120
        )

    def _handle_pointer(self, x: int, y: int, right: bool, drag: bool = False) -> None:
        row = (y - GRID_TOP) // LINE_HEIGHT
        if row < 0 or row >= self._visible_rows():
            return
        index = self._top_row() + row
        if index >= MAX_FX_LEN:
            return
        frame = self.effect.frames[index]
        scc = self.scc_frame(index)
        width = max(self.canvas.winfo_width(), 1320)
        gutter = self._selection_gutter_at(x)
        if gutter is None and any(x1 <= x <= x2 for x1, x2 in NON_SELECTABLE_TEXT_RANGES):
            return
        self.cursor_row = index
        if x < 45:
            frame.selected = not right
        elif 48 <= x <= 68:
            frame.t = not right
        elif 72 <= x <= 92:
            frame.n = not right
        elif (target := self._text_cell_at(x, y)) is not None:
            self._set_cell_target(*target)
        elif gutter is not None:
            column, _channel = gutter
            self._set_cell_target(index, SELECTION_CURSOR_COLUMNS[column])
        elif AY_PERIOD_BAR <= x <= AY_PERIOD_BAR + BAR_WIDTH:
            frac = max(0, min(1, (x - AY_PERIOD_BAR) / BAR_WIDTH))
            if self.period_linear.get():
                frame.tone = int(frac * 4095)
            else:
                frame.tone = int(8.0 * math.exp(frac * math.log(4095.0 / 8.0)))
        elif AY_VOLUME_BAR <= x <= AY_VOLUME_BAR + 60:
            frame.volume = int(max(0, min(15, round((x - AY_VOLUME_BAR) * 15 / 60))))
        elif AY_NOISE_BAR <= x <= AY_NOISE_BAR + 60:
            frame.noise = int(max(0, min(31, round((x - AY_NOISE_BAR) * 31 / 60))))
        elif SCC_PERIOD_BAR <= x <= SCC_PERIOD_BAR + BAR_WIDTH:
            frac = max(0, min(1, (x - SCC_PERIOD_BAR) / BAR_WIDTH))
            if self.period_linear.get():
                scc.tone = int(frac * 4095)
            else:
                scc.tone = int(8.0 * math.exp(frac * math.log(4095.0 / 8.0)))
        elif SCC_VOLUME_BAR <= x <= SCC_VOLUME_BAR + 80:
            scc.volume = int(max(0, min(15, round((x - SCC_VOLUME_BAR) * 15 / 80))))
        elif SCC_WAVEFORM_BAR <= x <= min(width - 8, SCC_WAVEFORM_BAR + 120):
            if index == 0:
                scc.noise = int(max(0, min(31, round((x - SCC_WAVEFORM_BAR) * 31 / 120))))
            else:
                self.status.set("SCC waveform is fixed by frame 000")
        self.ensure_cursor_visible()
        self.refresh_grid()

    def on_mousewheel(self, event: tk.Event) -> None:
        delta = -5 if event.delta > 0 else 5
        self.top_row = max(0, min(MAX_FX_LEN - self._visible_rows(), self._top_row() + delta))
        self.refresh_grid()

    def on_scrollbar(self, *args: str) -> None:
        if args[0] == "moveto":
            self.top_row = int(float(args[1]) * MAX_FX_LEN)
        elif args[0] == "scroll":
            page_size = self._visible_rows() if args[2] == "pages" else 5
            self.top_row = self._top_row() + int(args[1]) * page_size
        self.top_row = max(0, min(MAX_FX_LEN - self._visible_rows(), self.top_row))
        self.refresh_grid()

    def commit_name(self) -> None:
        new_name = self.name_var.get()[:255] or clean_effect_filename(self.effect.name)
        if new_name == self.effect.name:
            return
        self._record_undo()
        self.effect.name = new_name
        self.refresh_all()

    def new_bank(self) -> None:
        self.stop()
        wavetable_names = list(self.bank.wavetable_names)
        wavetables = [list(wave) for wave in self.bank.wavetables]
        self._record_undo()
        self.bank = Bank()
        self.bank.wavetable_names = wavetable_names
        self.bank.wavetables = wavetables
        self.current_index = 0
        self.cursor_row = 0
        self.status.set("New bank")
        self.refresh_all()

    def load_bank(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("AYFX bank", "*.afb"), ("All files", "*.*")])
        if not path:
            return
        try:
            wavetable_names = list(self.bank.wavetable_names)
            wavetables = [list(wave) for wave in self.bank.wavetables]
            loaded_bank = load_afb(path)
            self._record_undo()
            self.bank = loaded_bank
            self.bank.wavetable_names = wavetable_names
            self.bank.wavetables = wavetables
            self.current_index = 0
            self.status.set(f"Loaded {Path(path).name}")
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror("Load bank", str(exc))

    def load_default_wavetable(self) -> None:
        paths = [Path.cwd() / DEFAULT_WAVETABLE, Path(__file__).resolve().parent.parent / DEFAULT_WAVETABLE]
        for path in paths:
            if not path.exists():
                continue
            try:
                self.bank.wavetable_names, self.bank.wavetables = parse_wavetable_asm(path)
                self.status.set(f"Loaded wavetable {path.name}")
                return
            except Exception as exc:
                self.status.set(f"Could not load {path.name}: {exc}")
                return

    def save_bank(self, names: bool) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".afb", filetypes=[("AYFX bank", "*.afb"), ("All files", "*.*")])
        if not path:
            return
        try:
            save_afb(self.bank, path, include_names=names)
            self.status.set(f"Saved {Path(path).name}")
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror("Save bank", str(exc))

    def save_split_banks(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".afb", filetypes=[("AYFX bank", "*.afb"), ("All files", "*.*")])
        if not path:
            return
        try:
            base_path = Path(path)
            saved = []
            for bank_index, start in enumerate(range(0, len(self.bank.effects), 32), start=1):
                chunk = Bank()
                chunk.effects = self.bank.effects[start : start + 32]
                chunk_path = base_path.with_name(f"{base_path.stem}_{bank_index:02d}{base_path.suffix or '.afb'}")
                save_afb(chunk, chunk_path, include_names=True)
                saved.append(chunk_path)
            self.status.set(f"Saved {len(saved)} split bank{'s' if len(saved) != 1 else ''}")
        except Exception as exc:
            messagebox.showerror("Save split banks", str(exc))

    def load_wavetable(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("ASM wavetable", "*.asm"), ("All files", "*.*")])
        if not path:
            return
        try:
            wavetable_names, wavetables = parse_wavetable_asm(path)
            self._record_undo()
            self.bank.wavetable_names = wavetable_names
            self.bank.wavetables = wavetables
            self.status.set(f"Loaded wavetable {Path(path).name}")
            self.refresh_grid()
        except Exception as exc:
            messagebox.showerror("Load wavetable", str(exc))

    def save_wavetable(self) -> None:
        path = filedialog.asksaveasfilename(initialfile="wavetable.asm", defaultextension=".asm", filetypes=[("ASM wavetable", "*.asm"), ("All files", "*.*")])
        if not path:
            return
        try:
            save_wavetable_asm(path, self.bank.wavetable_names, self.bank.wavetables)
            self.status.set(f"Saved wavetable {Path(path).name}")
        except Exception as exc:
            messagebox.showerror("Save wavetable", str(exc))

    def edit_wavetables(self) -> None:
        WavetableEditor(self).wait_window()

    def generate_sfx(self, template: str) -> None:
        waveform = self.scc_initial_waveform()
        self._record_undo()
        generate_template(self.effect, template, waveform)
        self.status.set(f"Generated {template} template")
        self.refresh_grid()

    def apply_fade(self, channel: str, start: int, end: int) -> None:
        self._record_undo()
        fade_volume(self.effect, channel, start, end)
        self.status.set("Applied volume fade")
        self.refresh_grid()

    def apply_sweep(self, channel: str) -> None:
        start = simpledialog.askinteger("Pitch sweep", "Start period", initialvalue=120, minvalue=0, maxvalue=4095, parent=self)
        if start is None:
            return
        end = simpledialog.askinteger("Pitch sweep", "End period", initialvalue=1200, minvalue=0, maxvalue=4095, parent=self)
        if end is None:
            return
        self._record_undo()
        sweep_period(self.effect, channel, start, end)
        self.status.set("Applied period sweep")
        self.refresh_grid()

    def randomize_noise(self) -> None:
        self._record_undo()
        randomize_psg_noise(self.effect)
        self.status.set("Randomized PSG noise")
        self.refresh_grid()

    def apply_period_jitter(self) -> None:
        amount = simpledialog.askinteger("Randomize period", "Maximum +/- amount", initialvalue=24, minvalue=0, maxvalue=4095, parent=self)
        if amount is None:
            return
        self._record_undo()
        randomize_period(self.effect, "both", amount)
        self.status.set("Randomized period")
        self.refresh_grid()

    def apply_tremolo(self) -> None:
        depth = simpledialog.askinteger("Tremolo", "Volume depth", initialvalue=5, minvalue=1, maxvalue=15, parent=self)
        if depth is None:
            return
        period = simpledialog.askinteger("Tremolo", "Frames per cycle", initialvalue=4, minvalue=1, maxvalue=64, parent=self)
        if period is None:
            return
        self._record_undo()
        tremolo(self.effect, "both", depth, period)
        self.status.set("Applied tremolo")
        self.refresh_grid()

    def apply_gate(self) -> None:
        on_frames = simpledialog.askinteger("Gate", "On frames", initialvalue=2, minvalue=1, maxvalue=64, parent=self)
        if on_frames is None:
            return
        off_frames = simpledialog.askinteger("Gate", "Off frames", initialvalue=2, minvalue=1, maxvalue=64, parent=self)
        if off_frames is None:
            return
        self._record_undo()
        gate(self.effect, "both", on_frames, off_frames)
        self.status.set("Applied gate")
        self.refresh_grid()

    def apply_echo(self) -> None:
        delay = simpledialog.askinteger("Echo", "Delay frames", initialvalue=6, minvalue=1, maxvalue=128, parent=self)
        if delay is None:
            return
        decay = simpledialog.askinteger("Echo", "Decay volume 0-15", initialvalue=8, minvalue=0, maxvalue=15, parent=self)
        if decay is None:
            return
        self._record_undo()
        echo(self.effect, "both", delay, decay)
        self.status.set("Applied echo")
        self.refresh_grid()

    def interpolate(self) -> None:
        self._record_undo()
        interpolate_selected(self.effect, "both")
        self.status.set("Interpolated selected range")
        self.refresh_grid()

    def reverse(self) -> None:
        self._record_undo()
        reverse_selected(self.effect)
        self.status.set("Reversed selected range")
        self.refresh_grid()

    def copy_to_scc(self) -> None:
        self._record_undo()
        copy_psg_to_scc(self.effect)
        self.status.set("Copied PSG period/volume to SCC")
        self.refresh_grid()

    def load_effect(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("AYFX effect", "*.afx"), ("All files", "*.*")])
        if not path:
            return
        try:
            loaded_effect = load_afx_pair(path)
            self._record_undo()
            self.bank.effects[self.current_index] = loaded_effect
            self.status.set(f"Loaded {Path(path).name}")
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror("Load effect", str(exc))

    def save_effect(self) -> None:
        if self.effect.real_len() == 0 and self.effect.scc_real_len() == 0:
            messagebox.showinfo("Save effect", "Effect is empty, nothing to save.")
            return
        path = filedialog.asksaveasfilename(initialfile=f"{clean_effect_filename(self.effect.name)}.afx", defaultextension=".afx", filetypes=[("AYFX effect", "*.afx"), ("All files", "*.*")])
        if path:
            saved_path = save_afx_pair(self.effect, path)
            self.status.set(f"Saved {saved_path.name}")

    def multi_load(self) -> None:
        paths = filedialog.askopenfilenames(filetypes=[("AYFX effects", "*.afx"), ("All files", "*.*")])
        if not paths:
            return
        self._record_undo()
        start = len(self.bank.effects) - 1
        if self.bank.effects[start].real_len() > 0:
            start += 1
        for path in paths:
            if start >= 256:
                break
            effect = load_afx_pair(path)
            if start < len(self.bank.effects):
                self.bank.effects[start] = effect
            else:
                self.bank.effects.append(effect)
            start += 1
        self.current_index = max(0, start - 1)
        self.refresh_all()

    def multi_save(self) -> None:
        directory = filedialog.askdirectory()
        if not directory:
            return
        for index, effect in enumerate(self.bank.effects):
            if effect.real_len() <= 0 and effect.scc_real_len() <= 0:
                continue
            path = Path(directory) / f"{clean_effect_filename(effect.name)}.afx"
            save_afx_pair(effect, path)
        self.status.set("Saved non-empty effects")

    def clear_effect(self) -> None:
        self._record_undo()
        self.effect.clear()
        self.status.set("Effect cleared")
        self.refresh_grid()

    def add_effect(self) -> None:
        try:
            self._record_undo()
            self.current_index = self.bank.add()
            self.refresh_all()
        except ValueError as exc:
            messagebox.showerror("Bank", str(exc))

    def insert_effect(self) -> None:
        try:
            self._record_undo()
            self.bank.insert(self.current_index)
            self.refresh_all()
        except ValueError as exc:
            messagebox.showerror("Bank", str(exc))

    def delete_effect(self) -> None:
        if not messagebox.askyesno("Delete effect", "Delete current effect?"):
            return
        self._record_undo()
        self.current_index = self.bank.delete(self.current_index)
        self.refresh_all()

    def go_effect(self, index: int) -> None:
        self.current_index = max(0, min(len(self.bank.effects) - 1, index))
        self.cursor_row = 0
        self.refresh_all()

    def select_all(self) -> None:
        self.column_selection = None
        for frame in self.effect.frames[: self.effect.real_len()]:
            frame.selected = True
        self.refresh_grid()

    def unselect_all(self) -> None:
        self.effect.deselect_all()
        self.column_selection = None
        self.refresh_grid()

    def inverse_selection(self) -> None:
        self.column_selection = None
        for frame in self.effect.frames[: self.effect.real_len()]:
            frame.selected = not frame.selected
        self.refresh_grid()

    def copy(self) -> None:
        if self.column_selection:
            self._copy_columns()
            return
        self.column_clipboard = None
        selected = [index for index, frame in enumerate(self.effect.frames) if frame.selected]
        self.clipboard = [self.effect.frames[index].clone() for index in selected]
        self.scc_clipboard = [self.effect.scc_frames[index].clone() for index in selected]
        if not self.clipboard:
            self.clipboard = [self.effect.frames[self.cursor_row].clone()]
            self.scc_clipboard = [self.effect.scc_frames[self.cursor_row].clone()]
        for frame in self.clipboard:
            frame.selected = False
        self.unselect_all()

    def cut(self) -> None:
        self._record_undo()
        if self.column_selection:
            self._copy_columns()
            if self.column_clipboard:
                self._clear_selected_columns()
            return
        self.copy()
        self.delete_selection(force_cursor=True, record_undo=False)

    def paste(self) -> None:
        if self.column_clipboard:
            self._record_undo()
            self._paste_columns()
            return
        if not self.clipboard:
            return
        self._record_undo()
        frames = self.effect.frames
        count = min(len(self.clipboard), MAX_FX_LEN - self.cursor_row)
        for _ in range(count):
            frames.insert(self.cursor_row, Frame())
            del frames[-1]
        scc_frames = self.scc_effect_frames()
        for _ in range(count):
            scc_frames.insert(self.cursor_row, Frame())
            del scc_frames[-1]
        for offset in range(count):
            frames[self.cursor_row + offset] = self.clipboard[offset].clone()
            frames[self.cursor_row + offset].selected = False
            scc_frames[self.cursor_row + offset] = self.scc_clipboard[offset].clone()
            scc_frames[self.cursor_row + offset].selected = False
        self.refresh_grid()

    def _copy_columns(self) -> None:
        if not self.column_selection:
            return
        first_row, last_row, first_col, last_col, channel = self.column_selection
        fields = [
            field
            for selected_channel, field, _x1, _x2 in SELECTABLE_COLUMNS[first_col : last_col + 1]
            if selected_channel == channel and field is not None
        ]
        fields = list(dict.fromkeys(fields))
        self.clipboard = []
        self.scc_clipboard = []
        if not fields:
            self.column_clipboard = None
            self.status.set("Selection contains only ignored data (AY noise or SCC wave)")
            return
        source = self.effect.frames if channel == "psg" else self.scc_effect_frames()
        values = [{field: getattr(source[row], field) for field in fields} for row in range(first_row, last_row + 1)]
        self.column_clipboard = {"channel": channel, "fields": fields, "values": values}
        labels = ", ".join("period" if field == "tone" else field for field in fields)
        self.status.set(f"Copied {len(values)} {channel.upper()} row(s): {labels}")

    def _paste_columns(self) -> None:
        if not self.column_clipboard:
            return
        if self.column_selection:
            first_row, _last_row, first_col, last_col, channel = self.column_selection
            destination_fields = {
                field
                for selected_channel, field, _x1, _x2 in SELECTABLE_COLUMNS[first_col : last_col + 1]
                if selected_channel == channel and field is not None
            }
        else:
            first_row = self.cursor_row
            channel = "psg" if self.cursor_col <= 2 else "scc"
            destination_fields = {"tone", "volume"}
        clipboard_fields = set(self.column_clipboard["fields"])
        fields = clipboard_fields & destination_fields
        if not fields:
            self.status.set("Paste target contains no matching period or volume column")
            return
        values = self.column_clipboard["values"]
        count = min(len(values), MAX_FX_LEN - first_row)
        destination = self.effect.frames if channel == "psg" else self.scc_effect_frames()
        for offset in range(count):
            for field in fields:
                setattr(destination[first_row + offset], field, values[offset][field])
        source_channel = str(self.column_clipboard["channel"]).upper()
        labels = ", ".join("period" if field == "tone" else field for field in sorted(fields))
        self.status.set(f"Pasted {count} row(s) from {source_channel} to {channel.upper()}: {labels}")
        self.refresh_grid()

    def _clear_selected_columns(self) -> None:
        if not self.column_selection:
            return
        first_row, last_row, first_col, last_col, channel = self.column_selection
        fields = {
            field
            for selected_channel, field, _x1, _x2 in SELECTABLE_COLUMNS[first_col : last_col + 1]
            if selected_channel == channel and field is not None
        }
        if not fields:
            self.status.set("Selection contains only ignored data (AY noise or SCC wave)")
            return
        destination = self.effect.frames if channel == "psg" else self.scc_effect_frames()
        for row in range(first_row, last_row + 1):
            for field in fields:
                setattr(destination[row], field, 0)
        self.status.set(f"Cleared selected {channel.upper()} period/volume data")
        self.refresh_grid()

    def delete_selection(self, force_cursor: bool = False, record_undo: bool = True) -> None:
        if record_undo:
            self._record_undo()
        if self.column_selection and not force_cursor:
            self._clear_selected_columns()
            return
        if force_cursor or not any(frame.selected for frame in self.effect.frames):
            self.effect.frames[self.cursor_row].selected = True
        selected = [frame.selected for frame in self.effect.frames]
        self.effect.frames = [frame for frame in self.effect.frames if not frame.selected]
        self.effect.frames.extend(Frame() for _ in range(MAX_FX_LEN - len(self.effect.frames)))
        scc_frames = self.scc_effect_frames()
        self.effect.scc_frames = [frame for frame, remove in zip(scc_frames, selected) if not remove]
        self.effect.scc_frames.extend(Frame() for _ in range(MAX_FX_LEN - len(self.effect.scc_frames)))
        self.refresh_grid()

    def insert_frame(self, clone: bool = False) -> None:
        self._record_undo()
        src = self.effect.frames[self.cursor_row].clone() if clone else Frame()
        src.selected = False
        self.effect.frames.insert(self.cursor_row, src)
        del self.effect.frames[-1]
        scc_frames = self.scc_effect_frames()
        scc_src = scc_frames[self.cursor_row].clone() if clone else Frame()
        scc_src.selected = False
        scc_frames.insert(self.cursor_row, scc_src)
        del scc_frames[-1]
        self.refresh_grid()

    def import_psg_dialog(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("PSG AY register dump", "*.psg"), ("All files", "*.*")])
        if not path:
            return
        channel = simpledialog.askinteger("PSG channel", "Channel: -1 auto, 0 A, 1 B, 2 C", initialvalue=-1, minvalue=-1, maxvalue=2, parent=self)
        if channel is None:
            return
        try:
            imported_effect = import_psg(path, channel)
            self._record_undo()
            self.bank.effects[self.current_index] = imported_effect
            self._clear_scc_frames()
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror("Import PSG", str(exc))

    def import_wav_dialog(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Wave file", "*.wav"), ("All files", "*.*")])
        if not path:
            return
        try:
            imported_effect = import_wav(path)
            self._record_undo()
            self.bank.effects[self.current_index] = imported_effect
            self._clear_scc_frames()
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror("Import WAV", str(exc))

    def import_vgm_dialog(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("VGM sound log", "*.vgm"), ("All files", "*.*")])
        if not path:
            return
        channel = simpledialog.askinteger("VGM channel", "Channel: -1 auto, 0-2 tone channels", initialvalue=-1, minvalue=-1, maxvalue=2, parent=self)
        if channel is None:
            return
        include_noise = messagebox.askyesno("VGM import", "Include SN76489 noise channel?")
        try:
            imported_effect = import_vgm(path, channel, include_noise)
            self._record_undo()
            self.bank.effects[self.current_index] = imported_effect
            self._clear_scc_frames()
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror("Import VGM", str(exc))

    def export_csv_dialog(self) -> None:
        self._export_dialog("csv", export_csv, "CSV")

    def export_wav_dialog(self) -> None:
        self._export_dialog("wav", lambda effect, path: export_wav(effect, path, self.bank.wavetables), "Wave")

    def export_vt2_dialog(self) -> None:
        base = simpledialog.askinteger("VTII sample", "Base note (0-95)", initialvalue=48, minvalue=0, maxvalue=95, parent=self)
        if base is None:
            return
        self._export_dialog("txt", lambda effect, path: export_vt2(effect, path, base), "VTII sample")

    def _export_dialog(self, ext: str, writer, title: str) -> None:
        if self.export_all.get():
            directory = filedialog.askdirectory(title=f"Export all as {title}")
            if not directory:
                return
            for index, effect in enumerate(self.bank.effects):
                if effect.real_len() <= 0:
                    continue
                writer(effect, Path(directory) / f"{index:03d}_{clean_effect_filename(effect.name)}.{ext}")
            self.status.set(f"Exported all {title} files")
        else:
            path = filedialog.asksaveasfilename(initialfile=f"{clean_effect_filename(self.effect.name)}.{ext}", defaultextension=f".{ext}", filetypes=[(title, f"*.{ext}"), ("All files", "*.*")])
            if not path:
                return
            writer(self.effect, path)
            self.status.set(f"Exported {Path(path).name}")

    def play(self, from_cursor: bool = False, channel: str = "both") -> None:
        self.stop()
        data = render_wav_bytes(self.effect, self.cursor_row if from_cursor else 0, self.bank.wavetables, channel)
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        handle.write(data)
        handle.close()
        self._play_file = handle.name
        if sys.platform.startswith("win"):
            import winsound

            winsound.PlaySound(self._play_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            self.status.set(f"Rendered preview to {self._play_file}")
            return
        label = {"psg": "PSG", "scc": "SCC"}.get(channel, "mix")
        self.status.set(f"Playing {label}")

    def stop(self) -> None:
        if sys.platform.startswith("win"):
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        if self._play_file and os.path.exists(self._play_file):
            try:
                os.unlink(self._play_file)
            except OSError:
                pass
        self._play_file = None

    def unsupported(self, text: str) -> None:
        messagebox.showinfo("Not yet ported", text)


class WavetableEditor(tk.Toplevel):
    def __init__(self, editor: AyFxEditor) -> None:
        super().__init__(editor)
        self.editor = editor
        self.title("SCC Wavetable Editor")
        self.geometry("780x420")
        self.transient(editor)
        self.grab_set()
        self.current = 0
        self.name_var = tk.StringVar()
        self.value_vars = [tk.IntVar() for _ in range(32)]
        self._play_file: str | None = None
        self._canvas_drag_undo_recorded = False
        self._build()
        self.load_wave(0)
        self.bind("<Control-z>", self.on_undo_shortcut)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)
        left = ttk.Frame(outer)
        left.pack(side="left", fill="y")
        self.listbox = tk.Listbox(left, width=24, exportselection=False)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        for index, name in enumerate(self.editor.bank.wavetable_names):
            self.listbox.insert("end", f"{index:02X} {name}")

        right = ttk.Frame(outer)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        name_row = ttk.Frame(right)
        name_row.pack(fill="x")
        ttk.Label(name_row, text="Name").pack(side="left")
        name_entry = ttk.Entry(name_row, textvariable=self.name_var)
        name_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        name_entry.bind("<FocusOut>", lambda _event: self.store_wave())
        name_entry.bind("<Return>", lambda _event: self.store_wave())

        self.canvas = tk.Canvas(right, height=150, background="white", highlightthickness=1, highlightbackground="#cfcfcf")
        self.canvas.pack(fill="x", pady=8)
        self.canvas.bind("<Button-1>", self.on_canvas)
        self.canvas.bind("<B1-Motion>", self.on_canvas)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)

        grid = ttk.Frame(right)
        grid.pack(fill="x")
        for index, var in enumerate(self.value_vars):
            spin = ttk.Spinbox(grid, from_=-128, to=127, width=5, textvariable=var, command=self.on_spin)
            spin.grid(row=index // 8, column=(index % 8) * 2 + 1, padx=(0, 6), pady=2)
            ttk.Label(grid, text=f"{index:02X}").grid(row=index // 8, column=(index % 8) * 2, padx=(0, 2), pady=2)
            spin.bind("<KeyRelease>", lambda _event: self.on_spin())
            spin.bind("<FocusOut>", lambda _event: self.on_spin())

        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Load...", command=self.load_file).pack(side="left")
        ttk.Button(buttons, text="Save...", command=self.save_file).pack(side="left", padx=6)
        ttk.Button(buttons, text="Play", command=self.play_wave).pack(side="left", padx=(12, 0))
        ttk.Button(buttons, text="Stop", command=self.stop_wave).pack(side="left", padx=6)
        ttk.Button(buttons, text="Close", command=self.close).pack(side="right")

        wave_tools = ttk.Frame(right)
        wave_tools.pack(fill="x", pady=(8, 0))
        for label, func in (
            ("Sine", sine_wave),
            ("Square", square_wave),
            ("Saw", saw_wave),
            ("Triangle", triangle_wave),
            ("Noise", noise_wave),
        ):
            ttk.Button(wave_tools, text=label, command=lambda maker=func: self.replace_wave(maker())).pack(side="left", padx=(0, 4))
        ttk.Button(wave_tools, text="Normalize", command=lambda: self.transform_wave(normalize_wave)).pack(side="left", padx=(10, 4))
        ttk.Button(wave_tools, text="Invert", command=lambda: self.transform_wave(invert_wave)).pack(side="left", padx=(0, 4))
        ttk.Button(wave_tools, text="Smooth", command=lambda: self.transform_wave(smooth_wave)).pack(side="left", padx=(0, 4))
        ttk.Button(wave_tools, text="Rotate", command=lambda: self.transform_wave(rotate_wave)).pack(side="left", padx=(0, 4))

    def load_wave(self, index: int) -> None:
        self.current = max(0, min(31, index))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.current)
        self.listbox.see(self.current)
        self.name_var.set(self.editor.bank.wavetable_names[self.current])
        wave = self.editor.bank.wavetables[self.current]
        for pos, var in enumerate(self.value_vars):
            var.set(byte_to_signed(wave[pos]))
        self.draw_wave()

    def store_wave(self, record_undo: bool = True) -> None:
        new_name = self.name_var.get().strip() or f"{self.current:02X}"
        new_wave = []
        for pos, var in enumerate(self.value_vars):
            try:
                value = max(-128, min(127, int(var.get())))
            except tk.TclError:
                value = 0
            new_wave.append(signed_to_byte(value))
        old_name = self.editor.bank.wavetable_names[self.current]
        old_wave = self.editor.bank.wavetables[self.current]
        if new_name != old_name or new_wave != old_wave:
            if record_undo:
                self.editor._record_undo()
            self.editor.bank.wavetable_names[self.current] = new_name
            self.editor.bank.wavetables[self.current] = new_wave
        self.listbox.delete(self.current)
        self.listbox.insert(self.current, f"{self.current:02X} {self.editor.bank.wavetable_names[self.current]}")
        self.listbox.selection_set(self.current)
        self.draw_wave()
        self.editor.refresh_grid()

    def draw_wave(self) -> None:
        self.canvas.delete("all")
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        mid = height // 2
        self.canvas.create_line(0, mid, width, mid, fill="#d0d0d0")
        step = width / 31
        points = []
        for index, var in enumerate(self.value_vars):
            value = max(-128, min(127, int(var.get())))
            x = index * step
            y = mid - (value / 128) * (height / 2 - 8)
            points.append((x, y))
            self.canvas.create_rectangle(x - 2, y - 2, x + 2, y + 2, fill="#806f9a", outline="")
        for first, second in zip(points, points[1:]):
            self.canvas.create_line(first[0], first[1], second[0], second[1], fill="#806f9a", width=2)

    def on_select(self, _event: tk.Event) -> None:
        selection = self.listbox.curselection()
        if not selection or selection[0] == self.current:
            return
        self.store_wave()
        self.load_wave(selection[0])

    def on_spin(self) -> None:
        self.store_wave()

    def on_canvas(self, event: tk.Event) -> None:
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        index = max(0, min(31, round(event.x / max(1, width) * 31)))
        value = int(round((height / 2 - event.y) / max(1, height / 2 - 8) * 128))
        self.value_vars[index].set(max(-128, min(127, value)))
        self.store_wave(record_undo=not self._canvas_drag_undo_recorded)
        self._canvas_drag_undo_recorded = True

    def on_canvas_release(self, _event: tk.Event) -> None:
        self._canvas_drag_undo_recorded = False

    def on_undo_shortcut(self, _event: tk.Event) -> str:
        self.editor.undo()
        self.listbox.delete(0, "end")
        for index, name in enumerate(self.editor.bank.wavetable_names):
            self.listbox.insert("end", f"{index:02X} {name}")
        self.load_wave(self.current)
        return "break"

    def load_file(self) -> None:
        path = filedialog.askopenfilename(parent=self, filetypes=[("ASM wavetable", "*.asm"), ("All files", "*.*")])
        if not path:
            return
        try:
            wavetable_names, wavetables = parse_wavetable_asm(path)
            self.editor._record_undo()
            self.editor.bank.wavetable_names = wavetable_names
            self.editor.bank.wavetables = wavetables
            self.listbox.delete(0, "end")
            for index, name in enumerate(self.editor.bank.wavetable_names):
                self.listbox.insert("end", f"{index:02X} {name}")
            self.load_wave(0)
        except Exception as exc:
            messagebox.showerror("Load wavetable", str(exc), parent=self)

    def save_file(self) -> None:
        self.store_wave()
        path = filedialog.asksaveasfilename(parent=self, initialfile="wavetable.asm", defaultextension=".asm", filetypes=[("ASM wavetable", "*.asm"), ("All files", "*.*")])
        if path:
            save_wavetable_asm(path, self.editor.bank.wavetable_names, self.editor.bank.wavetables)

    def replace_wave(self, wave: list[int]) -> None:
        self.editor._record_undo()
        self.editor.bank.wavetables[self.current] = wave[:32]
        self.load_wave(self.current)
        self.store_wave(record_undo=False)

    def transform_wave(self, transform) -> None:
        self.store_wave()
        self.editor._record_undo()
        self.editor.bank.wavetables[self.current] = transform(self.editor.bank.wavetables[self.current])[:32]
        self.load_wave(self.current)
        self.store_wave(record_undo=False)

    def play_wave(self) -> None:
        self.store_wave()
        self.stop_wave()
        data = render_scc_wave_bytes(self.editor.bank.wavetables[self.current])
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        handle.write(data)
        handle.close()
        self._play_file = handle.name
        if sys.platform.startswith("win"):
            import winsound

            winsound.PlaySound(self._play_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            self.editor.status.set(f"Rendered wavetable preview to {self._play_file}")

    def stop_wave(self) -> None:
        if sys.platform.startswith("win"):
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        if self._play_file and os.path.exists(self._play_file):
            try:
                os.unlink(self._play_file)
            except OSError:
                pass
        self._play_file = None

    def close(self) -> None:
        self.stop_wave()
        self.store_wave()
        self.destroy()


def main() -> None:
    app = AyFxEditor()
    app.mainloop()


if __name__ == "__main__":
    main()
