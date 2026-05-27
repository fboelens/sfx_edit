from __future__ import annotations

import csv
import math
import struct
import wave
from pathlib import Path

from .model import AY_CLOCK, MAX_EFFECTS, MAX_FX_LEN, MIX_RATE, SCC_WAVE_SIZE, Bank, Effect, Frame, clean_effect_filename, name_from_path
from .wavetable import byte_to_signed

PSG_NAME_SUFFIX = " PSG"
SCC_NAME_SUFFIX = " SCC"


def _u16le(data: bytes | bytearray, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


def _put_u16le(value: int) -> bytes:
    return bytes((value & 0xff, (value >> 8) & 0xff))


def decode_effect(data: bytes, name: str = "effect") -> tuple[Effect, int]:
    effect = Effect(name)
    effect.clear()
    pos = 0
    out = 0
    tone = 0
    noise = 0
    while pos < len(data) and out < MAX_FX_LEN:
        flags = data[pos]
        pos += 1
        if flags & 0x20:
            if pos + 2 > len(data):
                break
            tone = _u16le(data, pos) & 0x0fff
            pos += 2
        if flags & 0x40:
            if pos >= len(data):
                break
            noise = data[pos]
            pos += 1
            if flags == 0xD0 and noise >= 0x20:
                break
            noise &= 0x1f
        effect.frames[out] = Frame(
            tone=tone,
            noise=noise,
            volume=flags & 0x0f,
            t=not bool(flags & 0x10),
            n=not bool(flags & 0x80),
        )
        out += 1
    return effect, pos


def encode_effect(effect: Effect) -> bytes:
    payload = bytearray()
    tone = -1
    noise = -1
    for frame in effect.frames[: effect.real_len()]:
        flags = frame.volume & 0x0f
        if not frame.t:
            flags |= 0x10
        if not frame.n:
            flags |= 0x80
        if frame.tone != tone:
            tone = frame.tone & 0x0fff
            flags |= 0x20
        if frame.noise != noise:
            noise = frame.noise & 0x1f
            flags |= 0x40
        payload.append(flags)
        if flags & 0x20:
            payload += _put_u16le(tone)
        if flags & 0x40:
            payload.append(noise)
    payload += b"\xd0\x20"
    return bytes(payload)


def load_afx(path: str | Path) -> Effect:
    path = Path(path)
    effect, _ = decode_effect(path.read_bytes(), name_from_path(path))
    return effect


def save_afx(effect: Effect, path: str | Path) -> None:
    Path(path).write_bytes(encode_effect(effect))


def load_afb(path: str | Path) -> Bank:
    path = Path(path)
    data = path.read_bytes()
    if not data:
        raise ValueError("Empty bank file.")
    count = data[0] or MAX_EFFECTS
    if len(data) < 1 + count * 2:
        raise ValueError("Bank file is too small for its offset table.")
    offsets = [_u16le(data, 1 + i * 2) + 2 + i * 2 for i in range(count)]
    raw_effects: list[Effect] = []
    for index, start in enumerate(offsets):
        end = offsets[index + 1] if index < count - 1 else len(data)
        if start > len(data):
            raise ValueError(f"Effect {index + 1} offset is outside the file.")
        effect, used = decode_effect(data[start:end], default_bank_name(index))
        name_start = start + used
        if name_start < end:
            raw_name = data[name_start:end].split(b"\x00", 1)[0]
            if raw_name:
                effect.name = raw_name.decode("latin-1", errors="replace")[:255]
        raw_effects.append(effect)
    bank = Bank()
    bank.path = path
    if _is_paired_scc_bank(raw_effects):
        bank.effects = []
        for index in range(0, len(raw_effects), 2):
            psg = raw_effects[index]
            scc = raw_effects[index + 1]
            psg.name = _paired_base_name(psg.name, index // 2)
            psg.scc_frames = [Frame(frame.tone, frame.noise, frame.volume, frame.t, frame.n) for frame in scc.frames]
            bank.effects.append(psg)
    else:
        bank.effects = raw_effects
    if not bank.effects:
        bank.effects = [Effect(default_bank_name(0))]
    return bank


def default_bank_name(index: int) -> str:
    return f"noname{index + 1:03d}"


def _is_paired_scc_bank(effects: list[Effect]) -> bool:
    if not effects or len(effects) % 2 != 0:
        return False
    if all(_is_named_pair(effects[index], effects[index + 1]) for index in range(0, len(effects), 2)):
        return True
    return all(effect.name == default_bank_name(index) for index, effect in enumerate(effects))


def _is_named_pair(psg: Effect, scc: Effect) -> bool:
    psg_name = psg.name.casefold()
    scc_name = scc.name.casefold()
    return psg_name.endswith(PSG_NAME_SUFFIX.casefold()) and scc_name.endswith(SCC_NAME_SUFFIX.casefold())


def _paired_base_name(name: str, index: int) -> str:
    folded = name.casefold()
    if folded.endswith(PSG_NAME_SUFFIX.casefold()):
        return name[: -len(PSG_NAME_SUFFIX)] or default_bank_name(index)
    return name or default_bank_name(index)


def _paired_name(name: str, suffix: str) -> str:
    base = name
    folded = base.casefold()
    for existing in (PSG_NAME_SUFFIX, SCC_NAME_SUFFIX):
        if folded.endswith(existing.casefold()):
            base = base[: -len(existing)]
            break
    return f"{base[: 255 - len(suffix)]}{suffix}"


def _scc_effect(effect: Effect) -> Effect:
    scc = Effect(_paired_name(effect.name, SCC_NAME_SUFFIX))
    scc.frames = [Frame(frame.tone, frame.noise, frame.volume, frame.t, frame.n) for frame in effect.scc_frames]
    return scc


def save_afb(bank: Bank, path: str | Path, include_names: bool = True) -> None:
    effects: list[Effect] = []
    for effect in bank.effects[: MAX_EFFECTS // 2]:
        effects.append(effect)
        effects.append(_scc_effect(effect))
    table_len = 1 + len(effects) * 2
    payloads: list[bytes] = []
    current = table_len
    header = bytearray([len(effects) & 0xff])
    for index, effect in enumerate(effects):
        header += _put_u16le(current - index * 2 - 2)
        item = bytearray(encode_effect(effect))
        if include_names and effect.name:
            name = _paired_name(effect.name, PSG_NAME_SUFFIX) if index % 2 == 0 else effect.name
            item += name.encode("latin-1", errors="replace")[:255] + b"\x00"
        payloads.append(bytes(item))
        current += len(item)
    Path(path).write_bytes(bytes(header) + b"".join(payloads))
    bank.path = Path(path)


def export_csv(effect: Effect, path: str | Path) -> None:
    with Path(path).open("w", newline="") as handle:
        writer = csv.writer(handle)
        for frame in effect.frames[: effect.real_len()]:
            writer.writerow([int(frame.t), int(frame.n), f"0x{frame.tone:03x}", f"0x{frame.noise:02x}", f"0x{frame.volume:x}"])


def export_vt2(effect: Effect, path: str | Path, base_note: int = 48) -> None:
    freqs = [2093.0, 2217.4, 2349.2, 2489.0, 2637.0, 2793.8, 2960.0, 3136.0, 3322.4, 3520.0, 3729.2, 3951.0]
    offsets = []
    for octave in range(8):
        for freq in freqs:
            offsets.append(int(AY_CLOCK * 8.0 / freq) >> octave)
    base_note = max(0, min(base_note, len(offsets) - 1))
    lines = ["[Sample]"]
    for frame in effect.frames[: min(effect.real_len(), 0x3F)]:
        delta = max(-1023, min(1023, frame.tone - offsets[base_note]))
        lines.append(
            f"{'T' if frame.t else 't'}{'N' if frame.n else 'n'}e "
            f"{'-' if delta < 0 else '+'}{abs(delta):03X}_ +{frame.noise:02X}_ {frame.volume:X}_"
        )
    lines.append("tne +000_ +00_ 0_ L")
    Path(path).write_text("\n".join(lines) + "\n")


VOL_TAB = [0, 836 // 3, 1212 // 3, 1773 // 3, 2619 // 3, 3875 // 3, 5397 // 3, 8823 // 3, 10392 // 3, 16706 // 3, 23339 // 3, 29292 // 3, 36969 // 3, 46421 // 3, 55195 // 3, 65535 // 3]


class _AyChip:
    def __init__(self) -> None:
        self.reg = [0] * 16
        self.tone_count = 0
        self.tone_state = 0
        self.noise_count = 0
        self.noise_reg = 0x0FFFF
        self.noise_qcc = 0
        self.noise_state = 0
        self.freq_div = 0

    def out(self, reg: int, value: int) -> None:
        if reg == 1:
            value &= 15
        elif reg in (0, 7):
            pass
        elif reg in (6, 8):
            value &= 31
        else:
            return
        self.reg[reg] = value

    def tick(self, ticks: int) -> int:
        total = 0
        period = self.reg[0] | (self.reg[1] << 8)
        for _ in range(ticks):
            self.freq_div ^= 1
            if self.tone_count >= period:
                self.tone_count = 0
                self.tone_state ^= 1
            self.tone_count += 1
            if self.freq_div:
                if self.noise_count == 0:
                    noise_di = (self.noise_qcc ^ ((self.noise_reg >> 13) & 1)) ^ 1
                    self.noise_qcc = (self.noise_reg >> 15) & 1
                    self.noise_state = self.noise_qcc
                    self.noise_reg = ((self.noise_reg << 1) | noise_di) & 0xffff
                self.noise_count = (self.noise_count + 1) & 31
                if self.noise_count >= self.reg[6]:
                    self.noise_count = 0
            tone_active = self.tone_state | (self.reg[7] & 1)
            noise_active = self.noise_state | ((self.reg[7] >> 3) & 1)
            dac = self.reg[8] if tone_active and noise_active else 0
            total += VOL_TAB[dac & 0x0f]
        return total


SCC_CLOCK = 3_579_545


def render_wav_bytes(effect: Effect, start_frame: int = 0, wavetables: list[list[int]] | None = None) -> bytes:
    frames = max(0, max(effect.real_len(), effect.scc_real_len()) + 3 - start_frame)
    samples_per_frame = MIX_RATE // 50
    total_samples = samples_per_frame * frames
    chip = _AyChip()
    ticks = AY_CLOCK // 8 // MIX_RATE
    samples = bytearray()
    frame_tick = 0
    frame_index = start_frame
    scc_frame = Frame()
    scc_waveform = effect.scc_frames[0].noise if effect.scc_frames else 0
    scc_phase = 0.0
    for _ in range(total_samples):
        if frame_tick == 0 and frame_index < MAX_FX_LEN:
            frame = effect.frames[frame_index]
            scc_frame = effect.scc_frames[frame_index]
            scc_waveform = effect.scc_frames[0].noise
            chip.out(0, frame.tone & 0xff)
            chip.out(1, frame.tone >> 8)
            chip.out(6, frame.noise)
            chip.out(7, 0xF6 | (0 if frame.t else 1) | (0 if frame.n else 8))
            chip.out(8, frame.volume)
            frame_index += 1
        ay_value = int(chip.tick(ticks) / max(1, ticks))
        scc_value = _scc_sample(scc_frame, wavetables, scc_phase, scc_waveform)
        scc_phase = _scc_advance(scc_frame, scc_phase)
        value = int(ay_value * 0.7 + scc_value)
        value = max(-32768, min(32767, value))
        samples += struct.pack("<h", value)
        frame_tick = (frame_tick + 1) % samples_per_frame
    riff_size = 36 + len(samples)
    return (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, MIX_RATE, MIX_RATE * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(samples))
        + samples
    )


def export_wav(effect: Effect, path: str | Path, wavetables: list[list[int]] | None = None) -> None:
    Path(path).write_bytes(render_wav_bytes(effect, wavetables=wavetables))


def render_scc_wave_bytes(wave: list[int], period: int = 120, volume: int = 15, seconds: float = 1.0) -> bytes:
    samples = bytearray()
    phase = 0.0
    frame = Frame(period, 0, volume)
    total_samples = max(1, int(MIX_RATE * seconds))
    wavetables = [wave]
    for _ in range(total_samples):
        value = _scc_sample(frame, wavetables, phase, 0)
        phase = _scc_advance(frame, phase)
        value = max(-32768, min(32767, value))
        samples += struct.pack("<h", value)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(samples))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, MIX_RATE, MIX_RATE * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(samples))
        + samples
    )


def _scc_sample(frame: Frame, wavetables: list[list[int]] | None, phase: float, waveform: int) -> int:
    if not wavetables or frame.volume <= 0:
        return 0
    wave_index = max(0, min(len(wavetables) - 1, waveform))
    wave = wavetables[wave_index]
    if not wave:
        return 0
    sample_index = int(phase) % min(len(wave), SCC_WAVE_SIZE)
    return int(byte_to_signed(wave[sample_index]) * frame.volume * 7.5)


def _scc_advance(frame: Frame, phase: float) -> float:
    if frame.tone <= 0 or frame.volume <= 0:
        return phase
    frequency = SCC_CLOCK / (32 * (frame.tone + 1))
    phase += frequency * SCC_WAVE_SIZE / MIX_RATE
    while phase >= SCC_WAVE_SIZE:
        phase -= SCC_WAVE_SIZE
    return phase


def import_psg(path: str | Path, channel: int = -1) -> Effect:
    data = Path(path).read_bytes()
    if data[:3] != b"PSG":
        raise ValueError("This is not a PSG file.")
    effect = Effect(name_from_path(path))
    effect.clear()
    ptr = 16
    pd = 0
    tone = noise = volume = lnoise = 0
    ltone = [0, 0, 0]
    lt = [False, False, False]
    ln = [False, False, False]
    t = n = False
    first = True
    chan = channel
    newchan = chan
    icnt = 0
    while ptr < len(data):
        code = data[ptr]
        if code <= 10 and ptr + 1 < len(data):
            val = data[ptr + 1]
            if code in (0, 2, 4):
                c = code // 2
                ltone[c] = (ltone[c] & 0x0F00) | val
                if chan < 0 and ltone[c] > 0:
                    noise, tone, t, n = lnoise, ltone[c], lt[c], ln[c]
                if chan == c:
                    tone = ltone[c]
            elif code in (1, 3, 5):
                c = code // 2
                ltone[c] = (ltone[c] & 0x00FF) | (val << 8)
                if chan < 0 and ltone[c] > 0:
                    noise, tone, t, n = lnoise, ltone[c], lt[c], ln[c]
                if chan == c:
                    tone = ltone[c]
            elif code == 6:
                lnoise = val & 0x1f
                if chan >= 0:
                    noise = lnoise
            elif code == 7:
                lt = [not bool(val & 1), not bool(val & 2), not bool(val & 4)]
                ln = [not bool(val & 8), not bool(val & 16), not bool(val & 32)]
                if chan >= 0:
                    t, n = lt[chan], ln[chan]
            elif code in (8, 9, 10):
                c = code - 8
                nvolume = val & 0x0f
                if chan < 0 and nvolume > 0:
                    newchan = c
                    volume = nvolume
                if chan == c:
                    volume = nvolume
            ptr += 2
        elif code == 255:
            icnt = 1
            ptr += 1
        elif code == 254 and ptr + 1 < len(data):
            icnt = data[ptr + 1] * 4
            ptr += 2
        elif code == 253:
            break
        else:
            ptr += 2
        if newchan >= 0:
            chan = newchan
            noise, tone, t, n = lnoise, ltone[chan], lt[chan], ln[chan]
        for _ in range(icnt):
            if pd >= MAX_FX_LEN:
                return effect
            if first and tone == 0 and noise == 0 and volume == 0:
                continue
            first = False
            effect.frames[pd] = Frame(tone & 0x0fff, noise & 0x1f, volume & 0x0f, t, n)
            pd += 1
        icnt = 0
    return effect


AY_VOL_TAB = [0, 1, 1, 2, 3, 3, 4, 4, 4, 5, 5, 5, 6, 6, 6, 6, 6, 7, 7, 7, 7, 7, 7, 7, 8, 8, 8, 8, 8, 8, 8, 8, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15]


def import_wav(path: str | Path) -> Effect:
    with wave.open(str(path), "rb") as reader:
        channels = reader.getnchannels()
        sample_rate = reader.getframerate()
        width = reader.getsampwidth()
        frames = reader.readframes(reader.getnframes())
    if width not in (1, 2):
        raise ValueError("Only 8-bit and 16-bit PCM WAV files are supported.")
    values: list[int] = []
    step = channels * width
    for pos in range(0, len(frames), step):
        total = 0
        for ch in range(channels):
            sample = frames[pos + ch * width : pos + (ch + 1) * width]
            if width == 1:
                total += (sample[0] - 128) << 8
            else:
                total += struct.unpack("<h", sample)[0]
        values.append(int(total / channels))
    target_len = int(len(values) / sample_rate * MIX_RATE) if sample_rate else 0
    if target_len <= 0:
        raise ValueError("WAV file contains no samples.")
    resampled = [values[min(len(values) - 1, int(i * len(values) / target_len))] for i in range(target_len)]
    frame_samples = MIX_RATE // 50
    while len(resampled) % frame_samples:
        resampled.append(0)
    effect = Effect(name_from_path(path))
    effect.clear()
    peak = max(1, max(abs(v) for v in resampled))
    pd = 0
    for offset in range(0, len(resampled), frame_samples):
        chunk = resampled[offset : offset + frame_samples]
        if pd >= MAX_FX_LEN:
            break
        avg = sum(abs(v) for v in chunk) // max(1, len(chunk))
        smp = min(255, int(avg * 32768 / peak) >> 7)
        volume = AY_VOL_TAB[smp]
        freq = _zero_cross_freq(chunk, MIX_RATE)
        period = int(AY_CLOCK / 8.0 / freq) if freq > 0 else 0
        period = max(0, min(4095, period))
        effect.frames[pd] = Frame(period, (period >> 7) & 0x1f, volume, period > 0, period == 0)
        pd += 1
    return effect


def _zero_cross_freq(samples: list[int], rate: int) -> float:
    crossings = 0
    prev = samples[0] if samples else 0
    for sample in samples[1:]:
        if (prev < 0 <= sample) or (prev > 0 >= sample):
            crossings += 1
        prev = sample
    if crossings < 2:
        return 0.0
    return crossings * rate / (2.0 * len(samples))


class _Sn76489:
    def __init__(self) -> None:
        self.chan_vol = [15, 15, 15, 15]
        self.chan_div = [0, 0, 0, 0]
        self.latched_chan = 0
        self.latched_type = 0

    def out(self, value: int) -> None:
        if value & 0x80:
            chan = (value >> 5) & 3
            div = (self.chan_div[chan] & 0xFF0) | (value & 15)
            self.latched_chan = chan
            self.latched_type = value & 16
        else:
            chan = self.latched_chan
            div = (self.chan_div[chan] & 15) | ((value & 63) << 4)
        if self.latched_type:
            self.chan_vol[chan] = value & 15
        else:
            self.chan_div[chan] = div


def import_vgm(path: str | Path, channel: int = -1, include_noise: bool = True) -> Effect:
    data = Path(path).read_bytes()
    if data[:4] != b"Vgm ":
        raise ValueError("No VGM signature found.")
    if len(data) < 0x40:
        raise ValueError("VGM file is too small.")
    base_freq = struct.unpack_from("<I", data, 0x0C)[0]
    if base_freq == 0:
        raise ValueError("No SN76489 PSG clock found in VGM.")
    psg = _Sn76489()
    effect = Effect(name_from_path(path))
    effect.clear()
    ptr = 0x40
    wait = 0
    pd = 0
    picked_channel = channel
    while ptr < len(data) and pd < MAX_FX_LEN:
        command = data[ptr]
        incr = 1
        if command == 0x50 and ptr + 1 < len(data):
            psg.out(data[ptr + 1])
            incr = 2
        elif command == 0x4F:
            incr = 2
        elif command == 0x61 and ptr + 2 < len(data):
            wait += data[ptr + 1] | (data[ptr + 2] << 8)
            incr = 3
        elif command in (0x51, 0x52, 0x53, 0x54):
            incr = 3
        elif command == 0x62:
            wait += 735
        elif command == 0x63:
            wait += 882
        elif command == 0x66:
            break
        elif 0x70 <= command <= 0x7F:
            wait += (command & 0x0F) + 1
        elif command in (0x67,) and ptr + 6 < len(data):
            size = struct.unpack_from("<I", data, ptr + 3)[0]
            incr = 7 + size
        ptr += incr
        while wait >= 735 and pd < MAX_FX_LEN:
            wait -= 735
            if picked_channel < 0:
                for candidate in range(3):
                    if psg.chan_vol[candidate] < 15:
                        picked_channel = candidate
                        break
            if picked_channel < 0:
                continue
            tone_vol = 15 - (psg.chan_vol[picked_channel] & 15)
            noise_vol = 15 - (psg.chan_vol[3] & 15) if include_noise else 0
            noise_div = 0
            if noise_vol:
                mode = psg.chan_div[3] & 3
                noise_div = [0x1F, 0x19, 0x10, min(63, psg.chan_div[2] >> 1)][mode]
            if psg.chan_div[picked_channel] > 0:
                freq = base_freq / (psg.chan_div[picked_channel] * 16)
            else:
                freq = 100
            tone_div = int(AY_CLOCK / 8 / freq)
            tone_div = max(0, min(4095, tone_div))
            effect.frames[pd] = Frame(tone_div, noise_div & 0x1f, max(tone_vol, noise_vol), tone_vol > 0, noise_vol > 0)
            pd += 1
    return effect
