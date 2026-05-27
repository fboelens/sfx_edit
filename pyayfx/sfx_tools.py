from __future__ import annotations

import math
import random

from .model import MAX_FX_LEN, Effect, Frame


def selected_range(effect: Effect) -> tuple[int, int]:
    selected = [index for index, frame in enumerate(effect.frames) if frame.selected]
    if selected:
        return min(selected), max(selected) + 1
    length = max(effect.real_len(), effect.scc_real_len())
    return 0, max(1, min(MAX_FX_LEN, length))


def fade_volume(effect: Effect, channel: str, start_volume: int, end_volume: int) -> None:
    start, end = selected_range(effect)
    count = max(1, end - start - 1)
    for index in range(start, end):
        value = round(start_volume + (end_volume - start_volume) * ((index - start) / count))
        if channel in ("psg", "both"):
            effect.frames[index].volume = _clamp(value, 0, 15)
        if channel in ("scc", "both"):
            effect.scc_frames[index].volume = _clamp(value, 0, 15)


def sweep_period(effect: Effect, channel: str, start_period: int, end_period: int, curve: str = "linear") -> None:
    start, end = selected_range(effect)
    count = max(1, end - start - 1)
    for index in range(start, end):
        t = (index - start) / count
        if curve == "expo":
            t = t * t
        value = round(start_period + (end_period - start_period) * t)
        if channel in ("psg", "both"):
            effect.frames[index].tone = _clamp(value, 0, 4095)
            effect.frames[index].t = True
        if channel in ("scc", "both"):
            effect.scc_frames[index].tone = _clamp(value, 0, 4095)


def randomize_psg_noise(effect: Effect, low: int = 1, high: int = 31) -> None:
    start, end = selected_range(effect)
    for index in range(start, end):
        effect.frames[index].noise = random.randint(_clamp(low, 0, 31), _clamp(high, 0, 31))
        effect.frames[index].n = True


def randomize_period(effect: Effect, channel: str, amount: int = 24) -> None:
    start, end = selected_range(effect)
    for index in range(start, end):
        if channel in ("psg", "both"):
            effect.frames[index].tone = _clamp(effect.frames[index].tone + random.randint(-amount, amount), 0, 4095)
        if channel in ("scc", "both"):
            effect.scc_frames[index].tone = _clamp(effect.scc_frames[index].tone + random.randint(-amount, amount), 0, 4095)


def tremolo(effect: Effect, channel: str, depth: int = 5, period: int = 4) -> None:
    start, end = selected_range(effect)
    for index in range(start, end):
        offset = round(math.sin((index - start) / max(1, period) * math.tau) * depth)
        if channel in ("psg", "both"):
            effect.frames[index].volume = _clamp(effect.frames[index].volume + offset, 0, 15)
        if channel in ("scc", "both"):
            effect.scc_frames[index].volume = _clamp(effect.scc_frames[index].volume + offset, 0, 15)


def gate(effect: Effect, channel: str, on_frames: int = 2, off_frames: int = 2) -> None:
    start, end = selected_range(effect)
    cycle = max(1, on_frames + off_frames)
    for index in range(start, end):
        muted = ((index - start) % cycle) >= on_frames
        if muted and channel in ("psg", "both"):
            effect.frames[index].volume = 0
        if muted and channel in ("scc", "both"):
            effect.scc_frames[index].volume = 0


def interpolate_selected(effect: Effect, channel: str) -> None:
    start, end = selected_range(effect)
    if end - start < 2:
        return
    psg_start = effect.frames[start]
    psg_end = effect.frames[end - 1]
    scc_start = effect.scc_frames[start]
    scc_end = effect.scc_frames[end - 1]
    count = end - start - 1
    for index in range(start, end):
        t = (index - start) / count
        if channel in ("psg", "both"):
            effect.frames[index].tone = round(psg_start.tone + (psg_end.tone - psg_start.tone) * t)
            effect.frames[index].noise = round(psg_start.noise + (psg_end.noise - psg_start.noise) * t)
            effect.frames[index].volume = round(psg_start.volume + (psg_end.volume - psg_start.volume) * t)
        if channel in ("scc", "both"):
            effect.scc_frames[index].tone = round(scc_start.tone + (scc_end.tone - scc_start.tone) * t)
            effect.scc_frames[index].volume = round(scc_start.volume + (scc_end.volume - scc_start.volume) * t)


def reverse_selected(effect: Effect) -> None:
    start, end = selected_range(effect)
    effect.frames[start:end] = list(reversed(effect.frames[start:end]))
    effect.scc_frames[start:end] = list(reversed(effect.scc_frames[start:end]))


def echo(effect: Effect, channel: str, delay: int = 6, decay: int = 8) -> None:
    start, end = selected_range(effect)
    for index in range(start, end - delay):
        target = index + delay
        if channel in ("psg", "both"):
            src = effect.frames[index]
            dst = effect.frames[target]
            dst.tone = src.tone
            dst.noise = max(dst.noise, src.noise)
            dst.volume = max(dst.volume, src.volume * decay // 15)
            dst.t = src.t
            dst.n = src.n
        if channel in ("scc", "both"):
            src = effect.scc_frames[index]
            dst = effect.scc_frames[target]
            dst.tone = src.tone
            dst.volume = max(dst.volume, src.volume * decay // 15)


def copy_psg_to_scc(effect: Effect, copy_period: bool = True, copy_volume: bool = True) -> None:
    start, end = selected_range(effect)
    for index in range(start, end):
        if copy_period:
            effect.scc_frames[index].tone = effect.frames[index].tone
        if copy_volume:
            effect.scc_frames[index].volume = effect.frames[index].volume


def generate_template(effect: Effect, template: str, waveform: int | None = None) -> None:
    effect.clear()
    if template == "jump":
        _fill_dual(effect, 20, 760, 230, 15, 0, waveform)
    elif template == "coin":
        _fill_arpeggio(effect, [420, 320, 240, 180], waveform)
    elif template == "laser":
        _fill_dual(effect, 28, 120, 1450, 15, 0, waveform)
    elif template == "hit":
        _fill_hit(effect, waveform)
    elif template == "explosion":
        _fill_explosion(effect, waveform)
    elif template == "powerup":
        _fill_arpeggio(effect, [520, 390, 292, 220, 165], waveform)
    elif template == "pickup":
        _fill_arpeggio(effect, [360, 270, 180], waveform)
    elif template == "splash":
        _fill_splash(effect, waveform)
    elif template == "rumble":
        _fill_rumble(effect, waveform)
    else:
        raise ValueError(f"Unknown template {template!r}.")


def _fill_dual(effect: Effect, length: int, start_period: int, end_period: int, start_volume: int, end_volume: int, waveform: int | None) -> None:
    if waveform is not None:
        effect.scc_frames[0].noise = _clamp(waveform, 0, 31)
    for index in range(length):
        t = index / max(1, length - 1)
        period = round(start_period + (end_period - start_period) * t)
        volume = round(start_volume + (end_volume - start_volume) * t)
        effect.frames[index] = Frame(period, 0, _clamp(volume, 0, 15), True, False)
        effect.scc_frames[index].tone = period
        effect.scc_frames[index].volume = _clamp(volume, 0, 15)


def _fill_arpeggio(effect: Effect, periods: list[int], waveform: int | None) -> None:
    if waveform is not None:
        effect.scc_frames[0].noise = _clamp(waveform, 0, 31)
    pos = 0
    for period in periods:
        for _ in range(4):
            volume = max(0, 15 - pos // 2)
            effect.frames[pos] = Frame(period, 0, volume, True, False)
            effect.scc_frames[pos].tone = period
            effect.scc_frames[pos].volume = volume
            pos += 1


def _fill_hit(effect: Effect, waveform: int | None) -> None:
    if waveform is not None:
        effect.scc_frames[0].noise = _clamp(waveform, 0, 31)
    for index in range(18):
        volume = max(0, 15 - index)
        effect.frames[index] = Frame(120 + index * 30, 4 + index, volume, True, True)
        effect.scc_frames[index].tone = 120 + index * 50
        effect.scc_frames[index].volume = volume


def _fill_explosion(effect: Effect, waveform: int | None) -> None:
    if waveform is not None:
        effect.scc_frames[0].noise = _clamp(waveform, 0, 31)
    for index in range(42):
        t = index / 41
        volume = round(15 * (1 - t) * (1 - t))
        effect.frames[index] = Frame(80 + index * 38, random.randint(6, 31), volume, False, True)
        effect.scc_frames[index].tone = 180 + index * 70
        effect.scc_frames[index].volume = max(0, volume - 2)


def _fill_splash(effect: Effect, waveform: int | None) -> None:
    if waveform is not None:
        effect.scc_frames[0].noise = _clamp(waveform, 0, 31)
    for index in range(30):
        volume = max(0, 13 - index // 2)
        effect.frames[index] = Frame(0, random.randint(8, 31), volume, False, True)
        effect.scc_frames[index].tone = random.randint(260, 900)
        effect.scc_frames[index].volume = max(0, volume - 3)


def _fill_rumble(effect: Effect, waveform: int | None) -> None:
    if waveform is not None:
        effect.scc_frames[0].noise = _clamp(waveform, 0, 31)
    for index in range(60):
        volume = 8 + round(math.sin(index / 9) * 3)
        effect.frames[index] = Frame(900 + random.randint(-80, 80), random.randint(12, 31), max(0, volume - 2), False, True)
        effect.scc_frames[index].tone = 850 + random.randint(-120, 120)
        effect.scc_frames[index].volume = _clamp(volume, 0, 15)


def sine_wave() -> list[int]:
    return [round(math.sin(index / 32 * math.tau) * 127) & 0xFF for index in range(32)]


def square_wave() -> list[int]:
    return [0x7F if index < 16 else 0x80 for index in range(32)]


def saw_wave() -> list[int]:
    return [round(-128 + index * 255 / 31) & 0xFF for index in range(32)]


def triangle_wave() -> list[int]:
    values = []
    for index in range(32):
        if index < 16:
            value = round(-128 + index * 255 / 15)
        else:
            value = round(127 - (index - 16) * 255 / 15)
        values.append(value & 0xFF)
    return values


def noise_wave() -> list[int]:
    return [random.randint(0, 255) for _ in range(32)]


def normalize_wave(wave: list[int]) -> list[int]:
    signed = [_signed(value) for value in wave[:32]]
    peak = max(1, max(abs(value) for value in signed))
    return [round(value * 127 / peak) & 0xFF for value in signed]


def invert_wave(wave: list[int]) -> list[int]:
    return [(-_signed(value)) & 0xFF for value in wave[:32]]


def smooth_wave(wave: list[int]) -> list[int]:
    signed = [_signed(value) for value in wave[:32]]
    out = []
    for index in range(32):
        value = round((signed[index - 1] + signed[index] * 2 + signed[(index + 1) % 32]) / 4)
        out.append(value & 0xFF)
    return out


def rotate_wave(wave: list[int], amount: int = 1) -> list[int]:
    amount %= 32
    return wave[amount:32] + wave[:amount]


def _signed(value: int) -> int:
    value &= 0xFF
    return value - 256 if value >= 128 else value


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))
