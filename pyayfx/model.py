from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MAX_FX_LEN = 0x1000
MAX_EFFECTS = 0x100
SCC_WAVE_COUNT = 32
SCC_WAVE_SIZE = 32
AY_CLOCK = 1_773_400
MIX_RATE = 44_100


@dataclass
class Frame:
    tone: int = 0
    noise: int = 0
    volume: int = 0
    t: bool = False
    n: bool = False
    selected: bool = False

    def clone(self) -> "Frame":
        return Frame(self.tone, self.noise, self.volume, self.t, self.n, self.selected)


@dataclass
class Effect:
    name: str = "noname001"
    frames: list[Frame] = field(default_factory=lambda: [Frame() for _ in range(MAX_FX_LEN)])
    scc_frames: list[Frame] = field(default_factory=lambda: [Frame() for _ in range(MAX_FX_LEN)])

    def clear(self) -> None:
        self.frames = [Frame() for _ in range(MAX_FX_LEN)]
        self.scc_frames = [Frame() for _ in range(MAX_FX_LEN)]

    def real_len(self) -> int:
        for index in range(MAX_FX_LEN - 1, -1, -1):
            if self.frames[index].volume > 0:
                return index + 1
        return 0

    def scc_real_len(self) -> int:
        for index in range(MAX_FX_LEN - 1, -1, -1):
            if self.scc_frames[index].volume > 0:
                return index + 1
        return 0

    def deselect_all(self) -> None:
        for frame in self.frames:
            frame.selected = False


def default_effect_name(index: int) -> str:
    return f"noname{index + 1:03d}"


class Bank:
    def __init__(self) -> None:
        self.effects = [Effect(default_effect_name(0))]
        self.wavetable_names = [f"{index:02X}" for index in range(SCC_WAVE_COUNT)]
        self.wavetables = [[0 for _ in range(SCC_WAVE_SIZE)] for _ in range(SCC_WAVE_COUNT)]
        self.path: Path | None = None

    def add(self) -> int:
        if len(self.effects) >= MAX_EFFECTS // 2:
            raise ValueError("The bank already contains 128 PSG/SCC effect pairs.")
        self.effects.append(Effect(default_effect_name(len(self.effects))))
        return len(self.effects) - 1

    def insert(self, index: int) -> None:
        if len(self.effects) >= MAX_EFFECTS // 2:
            raise ValueError("The bank already contains 128 PSG/SCC effect pairs.")
        self.effects.insert(index, Effect(f"inserted{index:03d}"))

    def delete(self, index: int) -> int:
        if len(self.effects) == 1:
            self.effects[0] = Effect(default_effect_name(0))
            return 0
        del self.effects[index]
        return min(index, len(self.effects) - 1)


def clean_effect_filename(name: str) -> str:
    keep = []
    for char in name.strip() or "effect":
        keep.append(char if char.isalnum() or char in "._- " else "_")
    return "".join(keep).strip() or "effect"


def name_from_path(path: str | Path) -> str:
    return Path(path).stem[:255] or "effect"
