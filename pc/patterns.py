"""IchiPing audio excitation pattern library.

Mirrors pc/patterns.yaml onto the 09_collector firmware's in-RAM library
(firmware/shared/include/pattern_lib.h) via the ASCII PAT_* protocol.

Two pattern kinds:
    pulse — list of {freq_hz, on_ms, off_ms} tones with optional repeat.
            Total recording window (ms) = sum(on+off across tones) * repeat.
    sweep — linear chirp start_hz -> end_hz over sweep_ms, then silence_ms
            of trailing silence.
            Total recording window (ms) = sweep_ms + silence_ms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Union

import yaml


# ---------------------------------------------------------------------------
# Pattern data classes
# ---------------------------------------------------------------------------


@dataclass
class PulseTone:
    freq_hz: int
    on_ms: int
    off_ms: int


@dataclass
class PulsePattern:
    name: str
    tones: List[PulseTone]
    repeat: int = 1

    def total_ms(self) -> int:
        per_iter = sum(t.on_ms + t.off_ms for t in self.tones)
        return per_iter * max(self.repeat, 1)


@dataclass
class SweepPattern:
    name: str
    start_hz: int
    end_hz: int
    sweep_ms: int
    silence_ms: int = 0

    def total_ms(self) -> int:
        return self.sweep_ms + self.silence_ms


@dataclass
class NoisePattern:
    """White-noise pattern. Mirrors firmware/shared/include/pattern_lib.h
    PATTERN_KIND_NOISE."""
    name: str
    duration_ms: int
    volume_pct: int = 30
    shape: int = 0          # 0 = PRBS (±1, crest 0 dB), 1 = uniform int16

    def total_ms(self) -> int:
        return self.duration_ms


Pattern = Union[PulsePattern, SweepPattern, NoisePattern]


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------


# Firmware constants (must match pattern_lib.h).
MAX_PATTERNS = 16
MAX_TONES_PER_PULSE = 64
MAX_WINDOW_MS = 2000


@dataclass
class PatternLibrary:
    """In-memory representation of pc/patterns.yaml, plus a push helper."""

    source_path: Optional[Path] = None
    patterns: List[Pattern] = field(default_factory=list)

    # ---- loading ----

    @classmethod
    def load_yaml(cls, path: Path | str) -> "PatternLibrary":
        p = Path(path)
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        lib = cls(source_path=p)
        entries = data.get("patterns", []) if isinstance(data, dict) else []
        for entry in entries:
            lib.patterns.append(_parse_entry(entry))
        lib._validate()
        return lib

    def reload(self) -> None:
        if self.source_path is None:
            raise RuntimeError("no source_path set; cannot reload")
        fresh = PatternLibrary.load_yaml(self.source_path)
        self.patterns = fresh.patterns

    # ---- lookup ----

    def names(self) -> List[str]:
        return [p.name for p in self.patterns]

    def find(self, key: Union[int, str]) -> tuple[int, Pattern]:
        """Resolve a pattern by integer index or name. Raises KeyError."""
        if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
            idx = int(key)
            if 0 <= idx < len(self.patterns):
                return idx, self.patterns[idx]
            raise KeyError(
                f"pattern index {idx} out of range "
                f"(0..{len(self.patterns) - 1 if self.patterns else 0})"
            )
        for i, p in enumerate(self.patterns):
            if p.name == key:
                return i, p
        raise KeyError(f"pattern named {key!r} not found")

    # ---- push to MCU ----

    def push(
        self,
        send_line: Callable[[str], None],
        wait_ack: Optional[Callable[[], Optional[str]]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Send PAT CLEAR + each pattern via PAT_* lines.

        send_line(line) writes one ASCII command (no CR/LF inside) to the wire.
        wait_ack() (optional) blocks until the MCU acks (returns the OK/ERR
        line) and is called after every command. Without ack pacing, at
        921600 baud the MCU's TX echo can outlast the incoming-byte rate
        and overflow the LPUART RX FIFO, dropping line terminators silently.
        log(line) (optional) echoes each sent line to stdout.
        """
        def _emit(line: str) -> None:
            send_line(line)
            if log is not None:
                log(line)
            if wait_ack is not None:
                wait_ack()

        _emit("PAT CLEAR")
        for p in self.patterns:
            if isinstance(p, PulsePattern):
                _emit(f"PAT PULSE BEGIN {p.name}")
                for t in p.tones:
                    _emit(f"PAT TONE {t.freq_hz} {t.on_ms} {t.off_ms}")
                _emit(f"PAT PULSE END {max(p.repeat, 1)}")
            elif isinstance(p, SweepPattern):
                _emit(
                    f"PAT SWEEP {p.name} {p.start_hz} {p.end_hz} "
                    f"{p.sweep_ms} {p.silence_ms}"
                )
            elif isinstance(p, NoisePattern):
                _emit(
                    f"PAT NOISE {p.name} {p.duration_ms} "
                    f"{p.volume_pct} {p.shape}"
                )
            else:
                raise TypeError(f"unsupported pattern type {type(p).__name__}")

    # ---- validation ----

    def _validate(self) -> None:
        if len(self.patterns) > MAX_PATTERNS:
            raise ValueError(
                f"YAML has {len(self.patterns)} patterns but firmware caps at {MAX_PATTERNS}"
            )
        seen = set()
        for p in self.patterns:
            if not p.name:
                raise ValueError("pattern with empty name")
            if p.name in seen:
                raise ValueError(f"duplicate pattern name {p.name!r}")
            seen.add(p.name)
            if isinstance(p, PulsePattern):
                if len(p.tones) > MAX_TONES_PER_PULSE:
                    raise ValueError(
                        f"pattern {p.name!r} has {len(p.tones)} tones; "
                        f"firmware caps at {MAX_TONES_PER_PULSE}"
                    )
            if p.total_ms() > MAX_WINDOW_MS:
                raise ValueError(
                    f"pattern {p.name!r} total {p.total_ms()}ms exceeds "
                    f"max window {MAX_WINDOW_MS}ms (firmware buffer)"
                )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_entry(entry: dict) -> Pattern:
    name = entry["name"]
    kind = entry["type"]
    if kind == "pulse":
        tones = [
            PulseTone(int(t["freq_hz"]), int(t["on_ms"]), int(t["off_ms"]))
            for t in entry.get("tones", [])
        ]
        return PulsePattern(
            name=name,
            tones=tones,
            repeat=int(entry.get("repeat", 1)),
        )
    if kind == "sweep":
        return SweepPattern(
            name=name,
            start_hz=int(entry["start_hz"]),
            end_hz=int(entry["end_hz"]),
            sweep_ms=int(entry["sweep_ms"]),
            silence_ms=int(entry.get("silence_ms", 0)),
        )
    if kind == "noise":
        # shape accepts either an integer or a string alias.
        sh = entry.get("shape", 0)
        if isinstance(sh, str):
            sh = {"prbs": 0, "uniform": 1}.get(sh.lower(), 0)
        return NoisePattern(
            name=name,
            duration_ms=int(entry["duration_ms"]),
            volume_pct=int(entry.get("volume_pct", 30)),
            shape=int(sh),
        )
    raise ValueError(f"pattern {name!r}: unknown type {kind!r}")


def summary(p: Pattern) -> str:
    """One-line human-readable summary of a pattern."""
    if isinstance(p, PulsePattern):
        return (
            f"pulse  tones={len(p.tones)} repeat={p.repeat} "
            f"dur={p.total_ms()}ms  {p.name}"
        )
    if isinstance(p, SweepPattern):
        return (
            f"sweep  {p.start_hz}..{p.end_hz}Hz sweep={p.sweep_ms}ms "
            f"silence={p.silence_ms}ms dur={p.total_ms()}ms  {p.name}"
        )
    if isinstance(p, NoisePattern):
        shape_name = {0: "PRBS", 1: "uniform"}.get(p.shape, f"shape{p.shape}")
        return (
            f"noise  {shape_name} dur={p.duration_ms}ms vol={p.volume_pct}%  {p.name}"
        )
    return f"???  {getattr(p, 'name', '?')}"
