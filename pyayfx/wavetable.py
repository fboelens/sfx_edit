from __future__ import annotations

import re
from pathlib import Path

from .model import SCC_WAVE_COUNT, SCC_WAVE_SIZE

BYTE_RE = re.compile(r"\$[0-9a-fA-F]{1,2}|0x[0-9a-fA-F]{1,2}|[0-9]+")
NAME_RE = re.compile(r";\s*#\s*([0-9a-fA-F]{1,2})(?:/[0-9]+)?\s*-\s*(.*?):?\s*$")


def parse_wavetable_asm(path: str | Path) -> tuple[list[str], list[list[int]]]:
    names = [f"{index:02X}" for index in range(SCC_WAVE_COUNT)]
    waves: list[list[int]] = []
    pending_name: tuple[int, str] | None = None

    for line in Path(path).read_text(encoding="latin-1").splitlines():
        name_match = NAME_RE.match(line.strip())
        if name_match:
            pending_name = (int(name_match.group(1), 16), name_match.group(2).strip())
            continue
        body = _strip_comment(line).strip()
        if not re.match(r"(?i)^(db|defb|\.db)\b", body):
            continue
        values = [_parse_byte(match.group(0)) for match in BYTE_RE.finditer(body)]
        if not values:
            continue
        for offset in range(0, len(values), SCC_WAVE_SIZE):
            chunk = values[offset : offset + SCC_WAVE_SIZE]
            if len(chunk) != SCC_WAVE_SIZE:
                raise ValueError(f"Wavetable row has {len(chunk)} bytes; expected 32.")
            wave_index = len(waves)
            if wave_index >= SCC_WAVE_COUNT:
                raise ValueError("Wavetable file contains more than 32 waves.")
            waves.append(chunk)
            if pending_name and pending_name[0] == wave_index:
                names[wave_index] = pending_name[1] or names[wave_index]
            pending_name = None

    if not waves:
        raise ValueError("No wavetable data found.")
    while len(waves) < SCC_WAVE_COUNT:
        waves.append([0 for _ in range(SCC_WAVE_SIZE)])
    return names, waves


def save_wavetable_asm(path: str | Path, names: list[str], waves: list[list[int]]) -> None:
    lines = ["SFX_WAVEBASE:"]
    for index in range(SCC_WAVE_COUNT):
        name = names[index].strip() if index < len(names) else ""
        wave = waves[index] if index < len(waves) else []
        wave = [(value & 0xFF) for value in wave[:SCC_WAVE_SIZE]]
        wave.extend(0 for _ in range(SCC_WAVE_SIZE - len(wave)))
        lines.append(f";#{index:02X}-{name or f'{index:02X}'}:")
        lines.append("\tdb\t" + ",".join(f"${value:02X}" for value in wave))
    Path(path).write_text("\n".join(lines) + "\n", encoding="latin-1")


def _strip_comment(line: str) -> str:
    return line.split(";", 1)[0]


def _parse_byte(token: str) -> int:
    token = token.strip()
    if token.startswith("$"):
        value = int(token[1:], 16)
    elif token.lower().startswith("0x"):
        value = int(token, 16)
    else:
        value = int(token, 10)
    if not 0 <= value <= 255:
        raise ValueError(f"Wavetable byte {token} is outside 0..255.")
    return value


def byte_to_signed(value: int) -> int:
    value &= 0xFF
    return value - 256 if value >= 128 else value


def signed_to_byte(value: int) -> int:
    return value & 0xFF
