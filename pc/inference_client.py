"""IchiPing 10_inference — PC client (REPL + CLI 両対応)。

10_inference firmware の対向クライアント。09_collector_client.py と同じ
プロトコル基盤 (ichp_cmd ASCII 線、921600 bps) を使い、INFER / BL_*
コマンドを操作 + 結果整形を担当する。

主な機能:
  - REPL: コマンドを直接打つ / `:` で始まる local helper
  - CLI: --once / --script で非対話実行 / --infer N で一括推論 → CSV
  - 起動時に pc/patterns.yaml を MCU に push (09 と同じ)
  - BL CALIBRATE / BL FACTORY / BL LIVE で baseline 切替
  - RESULT 行を parse して 5-bit door 状態 + 14 等価クラスへ展開して表示

Wire 仕様: firmware/shared/include/ichp_cmd.h §INFER / §BL を参照。

Usage:
  REPL:
    python inference_client.py --port COM7

  ワンショット推論 5 回 + CSV ログ:
    python inference_client.py --port COM7 --infer 5 --csv runs/infer_log.csv

  baseline 校正 → live で 10 回推論:
    python inference_client.py --port COM7 \\
        --once "BL CALIBRATE 10" --once "BL LIVE" --once "INFER STREAM 10"

  サーボ動作テスト:
    python inference_client.py --port COM7 --once "OPEN a" --once "INFER"
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue, Empty
from typing import Optional

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed. Run: uv sync (or pip install pyserial)",
          file=sys.stderr)
    sys.exit(2)

from patterns import PatternLibrary, summary as pattern_summary


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERVO_NAMES = ("a", "b", "c", "AB", "BC")
DEFAULT_PATTERNS_PATH = Path(__file__).resolve().parent / "patterns.yaml"

# 14 等価クラス順序 (pc/training/dataset.py CLASS_ORDER_14 と一致)。
CLASS_ORDER_14 = ("A1", "A2",
                  "B1", "B2", "B3", "B4",
                  "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")


def state_to_14cls(state_idx: int) -> str:
    """state_idx (0..31) を 14 等価クラス名に変換。

    pc/training/dataset.py の `class_of` と完全一致。
    物理モデル: マイクは room A。door AB が closed なら room B/C は
    観測不能 (a のみ意味を持つ)。BC が closed なら c は観測不能。

        AB == 0: A1 if a==0 else A2 (b, c, BC は観測不能)
        AB == 1, BC == 0:
            (a, b) -> B1/B2/B3/B4   (c, BC は観測不能)
        AB == 1, BC == 1:
            C{1 + a + 2b + 4c}      (全 window 観測可)
    """
    bits = [(state_idx >> k) & 1 for k in range(5)]
    a, b, c, ab, bc = bits
    if ab == 0:
        return "A1" if a == 0 else "A2"
    if bc == 0:
        return {(0, 0): "B1", (1, 0): "B2",
                (0, 1): "B3", (1, 1): "B4"}[(a, b)]
    return "C" + str(1 + a + 2 * b + 4 * c)


# ---------------------------------------------------------------------------
# RESULT line parser
# ---------------------------------------------------------------------------

# 新フォーマット (10_inference 本実装、32cls + 14cls 両方明示):
#   RESULT seq=42 cls32_idx=9 cls32_state=s10010 cls14=B2
#          second32_idx=11 second32_state=s11010
#          baseline=factory argmax_q=87 second_q=12 margin=75
#          infer_us=2100 cap_ms=2010 doors a=1 b=0 c=0 AB=1 BC=0
RESULT_RE = re.compile(
    r"RESULT\s+"
    r"seq=(?P<seq>\d+)\s+"
    r"cls32_idx=(?P<sidx>\d+)\s+"
    r"cls32_state=(?P<state>s\d{5})\s+"
    r"cls14=(?P<cls14>\S+)\s+"
    r"second32_idx=(?P<sidx2>\d+)\s+"
    r"second32_state=(?P<state2>s\d{5})\s+"
    r"baseline=(?P<bl>\S+)\s+"
    r"argmax_q=(?P<aq>-?\d+)\s+"
    r"second_q=(?P<sq>-?\d+)\s+"
    r"margin=(?P<mg>-?\d+)\s+"
    r"infer_us=(?P<ius>\d+)\s+"
    r"cap_ms=(?P<cms>\d+)"
)


@dataclass
class InferResult:
    seq: int
    # 32cls (raw argmax)
    state_idx:   int          # 0..31, primary
    state:       str          # "s10010"
    state_idx2:  int          # 2nd best
    state2:      str
    # 14cls (firmware 算出、PC 側でも検算)
    cls_14_mcu:  str          # firmware 報告値
    cls_14:      str          # PC 算出値 (state_idx から)
    cls_14_ok:   bool         # 両者一致なら True
    baseline:    str          # "factory" / "live"
    argmax_q:    int
    second_q:    int
    margin:      int
    infer_us:    int
    cap_ms:      int
    received_at: _dt.datetime


def parse_result(line: str) -> Optional[InferResult]:
    m = RESULT_RE.match(line)
    if not m:
        return None
    sidx  = int(m.group("sidx"))
    sidx2 = int(m.group("sidx2"))
    cls14_mcu = m.group("cls14")
    cls14_pc  = state_to_14cls(sidx)
    return InferResult(
        seq=int(m.group("seq")),
        state_idx=sidx,
        state=m.group("state"),
        state_idx2=sidx2,
        state2=m.group("state2"),
        cls_14_mcu=cls14_mcu,
        cls_14=cls14_pc,
        cls_14_ok=(cls14_mcu == cls14_pc),
        baseline=m.group("bl"),
        argmax_q=int(m.group("aq")),
        second_q=int(m.group("sq")),
        margin=int(m.group("mg")),
        infer_us=int(m.group("ius")),
        cap_ms=int(m.group("cms")),
        received_at=_dt.datetime.now(),
    )


def fmt_result(r: InferResult, truth: Optional[str] = None) -> str:
    """1 line pretty-print。32cls (state + idx) と 14cls の両方を併記する。

    truth 照合は state (s10010 形式), state_idx 整数, または cls14 名のどれでも可。
    firmware と PC で 14cls が一致しないと "[MCU=X PC=Y]" を出して可視化。
    """
    tag = ""
    if truth is not None:
        truth_s = str(truth)
        ok = (truth_s == r.state
              or truth_s == str(r.state_idx)
              or truth_s == r.cls_14)
        tag = "  OK" if ok else f"  NG (truth={truth_s})"
    cls14_str = (r.cls_14 if r.cls_14_ok
                 else f"{r.cls_14}[MCU={r.cls_14_mcu}!]")
    return (f"[{r.received_at.strftime('%H:%M:%S')}] "
            f"seq={r.seq:4d}  "
            f"cls32={r.state}({r.state_idx:>2d})  "
            f"cls14={cls14_str:<3s}  "
            f"2nd={r.state2}({r.state_idx2:>2d})  "
            f"bl={r.baseline:<7s}  q={r.argmax_q:+4d} mgn={r.margin:+4d}  "
            f"{r.infer_us}us cap={r.cap_ms}ms{tag}")


# ---------------------------------------------------------------------------
# Line reader (single-channel, no binary frames — 10_inference doesn't send ICHP)
# ---------------------------------------------------------------------------

class LineReader(threading.Thread):
    """Serial → 行単位 queue + 任意の callback (REPL 用)。"""

    def __init__(self, ser: serial.Serial) -> None:
        super().__init__(daemon=True)
        self.ser = ser
        self.lines: Queue[str] = Queue()
        self.line_callback = None      # set to callable(str) for REPL streaming
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(256)
            except serial.SerialException:
                break
            if not chunk:
                continue
            for b in chunk:
                if b in (0x0A, 0x0D):
                    if buf:
                        try:
                            line = buf.decode("utf-8", errors="replace").rstrip()
                        finally:
                            buf.clear()
                        if line:
                            if self.line_callback is not None:
                                self.line_callback(line)
                            else:
                                self.lines.put(line)
                else:
                    buf.append(b)


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------

def send(ser: serial.Serial, line: str) -> None:
    ser.write((line + "\r\n").encode("utf-8"))


def wait_for_prefix(reader: LineReader, prefix: str, timeout: float = 5.0,
                    echo: bool = True) -> Optional[str]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            line = reader.lines.get(timeout=0.2)
        except Empty:
            continue
        if echo:
            print(f"  < {line}")
        if line.startswith(prefix):
            return line
    return None


def wait_for_ack(reader: LineReader, timeout: float = 2.0,
                 echo: bool = True) -> Optional[str]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            line = reader.lines.get(timeout=0.2)
        except Empty:
            continue
        if echo:
            print(f"  < {line}")
        if line.startswith("OK") or line.startswith("ERR"):
            return line
    return None


# ---------------------------------------------------------------------------
# CSV logger
# ---------------------------------------------------------------------------

class CsvLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        self._f = path.open("a", newline="", encoding="utf-8")
        self._w = csv.writer(self._f)
        if new:
            self._w.writerow([
                "wall_ts", "seq",
                # 32cls (raw argmax + 2nd best)
                "cls32_idx", "cls32_state",
                "second32_idx", "second32_state",
                # 14cls (firmware & PC 算出、両方記録して divergence を検出可能に)
                "cls14_mcu", "cls14_pc", "cls14_ok",
                "baseline", "truth",
                "argmax_q", "second_q", "margin",
                "infer_us", "cap_ms",
            ])

    def append(self, r: InferResult, truth: Optional[str]) -> None:
        self._w.writerow([
            r.received_at.isoformat(timespec="milliseconds"),
            r.seq,
            r.state_idx, r.state,
            r.state_idx2, r.state2,
            r.cls_14_mcu, r.cls_14, int(r.cls_14_ok),
            r.baseline, truth if truth else "",
            r.argmax_q, r.second_q, r.margin,
            r.infer_us, r.cap_ms,
        ])
        self._f.flush()

    def close(self) -> None:
        try: self._f.close()
        except Exception: pass


# ---------------------------------------------------------------------------
# Non-interactive runners
# ---------------------------------------------------------------------------

def run_oneshot(ser: serial.Serial, reader: LineReader,
                commands: list[str], logger: Optional[CsvLogger],
                truth: Optional[str], timeout: float = 30.0) -> int:
    """Send a list of wire commands. INFER / INFER STREAM / BL CALIBRATE は
    完了行 (OK INFER done / OK BL calibrated) まで待つ。"""
    exit_code = 0
    for raw in commands:
        cmd = raw.strip()
        if not cmd or cmd.startswith("#"):
            continue
        print(f"> {cmd}")
        send(ser, cmd)
        ack = wait_for_ack(reader, timeout=timeout)
        if ack is None:
            print(f"  FAIL: no ack in {timeout:.1f}s for `{cmd}`", file=sys.stderr)
            exit_code = 2
            continue
        if ack.startswith("ERR"):
            exit_code = 2
            continue
        # 多 line 応答を待つ verb
        if ack.startswith("OK INFER started"):
            end = _drain_until(reader, lambda L: L.startswith("OK INFER done")
                                                or L.startswith("OK INFER aborted"),
                               result_logger=logger, truth=truth, timeout=timeout)
            if end is None:
                print("  FAIL: INFER STREAM timed out", file=sys.stderr)
                exit_code = 2
        elif ack.startswith("OK BL calibrating"):
            end = _drain_until(reader, lambda L: L.startswith("OK BL calibrated")
                                                or L.startswith("ERR BL "),
                               result_logger=None, truth=None, timeout=timeout)
            if end is None:
                print("  FAIL: BL CALIBRATE timed out", file=sys.stderr)
                exit_code = 2
    return exit_code


def _drain_until(reader: LineReader, end_pred,
                 result_logger: Optional[CsvLogger],
                 truth: Optional[str], timeout: float) -> Optional[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            line = reader.lines.get(timeout=0.2)
        except Empty:
            continue
        r = parse_result(line)
        if r is not None:
            print("  " + fmt_result(r, truth))
            if result_logger is not None:
                result_logger.append(r, truth)
        else:
            print(f"  < {line}")
        if end_pred(line):
            return line
    return None


def run_infer_n(ser: serial.Serial, reader: LineReader, n: int,
                logger: Optional[CsvLogger], truth: Optional[str]) -> int:
    """ショートカット: INFER STREAM <n> + 結果整形 + 任意 CSV ログ。
    一括計測したいときの典型動線。"""
    send(ser, f"INFER STREAM {n}")
    ack = wait_for_ack(reader, timeout=5.0)
    if ack is None or not ack.startswith("OK INFER started"):
        print(f"  FAIL: INFER STREAM rejected: {ack}", file=sys.stderr)
        return 2
    end = _drain_until(reader, lambda L: L.startswith("OK INFER done")
                                        or L.startswith("OK INFER aborted"),
                       result_logger=logger, truth=truth,
                       timeout=max(30.0, 3.0 * n))
    return 0 if end and end.startswith("OK INFER done") else 2


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

REPL_HELP = """
MCU に転送されるコマンド (大文字小文字区別なし):

  PING / GET CONFIG / GET HOME / GET OPEN
  SET VOLUME <0..100>
  SERVO <a|b|c|AB|BC> <deg>         (move, settle, auto-OFF)
  SERVO <name> OFF                   /  SERVO ALL OFF
  OPEN <name>   /  CLOSE <name>
  OPEN ALL      /  CLOSE ALL
  SET HOME <name> <deg>             /  SET OPEN <name> <deg>
  PAT INFO      /  PAT SELECT <idx>
  EMIT <idx>                         (test-play pattern, no inference)

  --- 推論 ---
  INFER                              (1 回推論 → RESULT 行)
  INFER STREAM <N>                   (N 回連続、STOP で中断)
  STOP                               (INFER STREAM 中断)

  --- Baseline ---
  BL STATUS                          (mode + 校正状態)
  BL FACTORY                         (factory 固定値を使う)
  BL LIVE                            (live 校正値を使う、要 CALIBRATE)
  BL CALIBRATE [N]                   (静粛時 N frame → live baseline)
  BL CLEAR                           (live 破棄 → factory に戻す)

Local helpers (`:` で始まる、MCU には送らない):

  :truth <state|cls14|none>          結果の照合ラベル (s10010 / A1 / B2 等)
  :csv <path>                        以降の結果を CSV にも出す
  :csv off                           CSV ログ停止
  :patterns                          patterns.yaml の一覧
  :select <name|idx>                 PAT SELECT 補助 (name 解決)
  :reload                            patterns.yaml 再読込 + MCU に再 push
  :infer [N]                         INFER STREAM N (default 5) のショート
  :help / :quit / :exit
"""


@dataclass
class ReplState:
    truth: Optional[str] = None
    csv_logger: Optional[CsvLogger] = None


def _handle_local(cmd: str, st: ReplState, ser: serial.Serial,
                  lib: PatternLibrary, reader: LineReader) -> str:
    if not cmd.startswith(":"):
        return "forward"
    head, *rest = cmd[1:].split(maxsplit=1)
    head = head.lower()
    arg = rest[0].strip() if rest else ""

    if head in ("quit", "exit"): return "quit"
    if head == "help":
        print(REPL_HELP); return "handled"
    if head == "truth":
        st.truth = None if arg in ("", "none") else arg
        print(f"  truth label: {st.truth or '(none)'}")
        return "handled"
    if head == "csv":
        if arg in ("", "off"):
            if st.csv_logger:
                st.csv_logger.close()
                print(f"  csv logging stopped ({st.csv_logger.path})")
                st.csv_logger = None
            else:
                print("  no csv active")
        else:
            if st.csv_logger:
                st.csv_logger.close()
            st.csv_logger = CsvLogger(Path(arg))
            print(f"  csv logging -> {arg}")
        return "handled"
    if head == "patterns":
        if not lib.patterns:
            print("  (no patterns)")
        else:
            for i, p in enumerate(lib.patterns):
                print(f"  [{i}] {pattern_summary(p)}")
        return "handled"
    if head == "select":
        if not arg:
            print("  usage: :select <name|idx>"); return "handled"
        try:
            idx, p = lib.find(arg)
        except KeyError as exc:
            print(f"  {exc}"); return "handled"
        saved = reader.line_callback; reader.line_callback = None
        try:
            send(ser, f"PAT SELECT {idx}")
            wait_for_ack(reader, timeout=2.0)
        finally:
            reader.line_callback = saved
        return "handled"
    if head == "reload":
        try:
            lib.reload()
        except Exception as exc:
            print(f"  reload failed: {exc}"); return "handled"
        print(f"  patterns reloaded ({len(lib.patterns)}); pushing to MCU...")
        saved = reader.line_callback; reader.line_callback = None
        try:
            lib.push(send_line=lambda L: send(ser, L),
                     wait_ack=lambda: wait_for_ack(reader, timeout=2.0),
                     log=lambda L: print(f"  > {L}"))
            if lib.patterns:
                send(ser, "PAT SELECT 0"); wait_for_ack(reader, timeout=2.0)
        finally:
            reader.line_callback = saved
        return "handled"
    if head == "infer":
        n = int(arg) if arg else 5
        saved = reader.line_callback; reader.line_callback = None
        try:
            run_infer_n(ser, reader, n, st.csv_logger, st.truth)
        finally:
            reader.line_callback = saved
        return "handled"

    print(f"  unknown local: {cmd}")
    return "handled"


def run_repl(ser: serial.Serial, reader: LineReader,
             lib: PatternLibrary) -> None:
    print(REPL_HELP)
    st = ReplState()
    out_lock = threading.Lock()
    auto_push_lock = threading.Lock()

    def trigger_auto_push() -> None:
        if not auto_push_lock.acquire(blocking=False): return
        def worker():
            try:
                with out_lock:
                    sys.stdout.write("\n  ! MCU reset — re-pushing patterns...\n"); sys.stdout.flush()
                time.sleep(0.3)
                saved = reader.line_callback; reader.line_callback = None
                try:
                    lib.push(send_line=lambda L: send(ser, L),
                             wait_ack=lambda: wait_for_ack(reader, timeout=2.0),
                             log=None)
                    if lib.patterns:
                        send(ser, "PAT SELECT 0"); wait_for_ack(reader, timeout=2.0)
                finally:
                    reader.line_callback = saved
                with out_lock:
                    sys.stdout.write(f"  ! re-pushed {len(lib.patterns)} patterns\n> "); sys.stdout.flush()
            finally:
                auto_push_lock.release()
        threading.Thread(target=worker, daemon=True).start()

    def on_line(line: str) -> None:
        if line.startswith("INFO IchiPing 10_inference ready"):
            trigger_auto_push()
        with out_lock:
            r = parse_result(line)
            if r is not None:
                pretty = fmt_result(r, st.truth)
                sys.stdout.write(f"\n  {pretty}\n> ")
                if st.csv_logger:
                    st.csv_logger.append(r, st.truth)
            else:
                sys.stdout.write(f"\n  < {line}\n> ")
            sys.stdout.flush()

    reader.line_callback = on_line

    try:
        while True:
            try:
                cmd = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print(); return
            if not cmd: continue
            decision = _handle_local(cmd, st, ser, lib, reader)
            if decision == "quit": return
            if decision == "handled": continue
            send(ser, cmd)
    finally:
        reader.line_callback = None
        if st.csv_logger: st.csv_logger.close()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="IchiPing 10_inference client")
    p.add_argument("--port", required=True, help="serial port (e.g. COM7)")
    p.add_argument("--baud", type=int, default=921600)
    p.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS_PATH,
                   help="YAML pattern library (default: pc/patterns.yaml)")
    p.add_argument("--no-push-patterns", action="store_true",
                   help="起動時に patterns.yaml を MCU に push しない")
    p.add_argument("--once", action="append", default=None,
                   help="単発コマンドを順に送って終了 (繰り返し可)")
    p.add_argument("--script", type=Path, default=None,
                   help="ファイルから 1 行 1 コマンドで読み込んで実行 → 終了")
    p.add_argument("--infer", type=int, default=0,
                   help="ショートカット: INFER STREAM N → CSV ログ → 終了")
    p.add_argument("--csv", type=Path, default=None,
                   help="--infer / --once の結果を CSV に append")
    p.add_argument("--truth", default=None,
                   help="--infer の結果と比較する正解 label (state or cls14)")
    args = p.parse_args()

    try:
        lib = PatternLibrary.load_yaml(args.patterns)
    except (FileNotFoundError, ValueError) as exc:
        print(f"FAIL loading {args.patterns}: {exc}", file=sys.stderr)
        return 2

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.1)
        try:
            ser.set_buffer_size(rx_size=256 * 1024, tx_size=64 * 1024)
        except (AttributeError, NotImplementedError):
            pass
    except serial.SerialException as exc:
        print(f"FAIL opening {args.port}: {exc}", file=sys.stderr)
        return 2

    reader = LineReader(ser); reader.start()
    print(f"connected {args.port} @ {args.baud} bps")
    print(f"loaded {len(lib.patterns)} patterns from {args.patterns}")

    print("waiting for MCU boot...")
    ready = wait_for_prefix(reader, "INFO IchiPing 10_inference", timeout=30.0,
                            echo=True)
    if ready is None:
        print("  (no boot banner; probing with PING)")
        send(ser, "PING")
        pong = wait_for_prefix(reader, "OK PONG", timeout=2.0, echo=True)
        if pong is None:
            print("FAIL: MCU did not respond. Check power, cable, port.", file=sys.stderr)
            ser.close(); return 2

    # patterns push (09 と同じ手順)
    non_interactive = args.once or args.script or args.infer > 0
    skip_push = args.no_push_patterns
    if not skip_push:
        lib.push(send_line=lambda L: send(ser, L),
                 wait_ack=lambda: wait_for_ack(reader, timeout=2.0, echo=False),
                 log=None)
        if lib.patterns:
            send(ser, "PAT SELECT 0"); wait_for_ack(reader, timeout=2.0, echo=False)
        print(f"  pushed {len(lib.patterns)} patterns; PAT SELECT 0")

    csv_logger = CsvLogger(args.csv) if args.csv else None
    try:
        if args.infer > 0:
            return run_infer_n(ser, reader, args.infer, csv_logger, args.truth)
        if args.once or args.script:
            commands: list[str] = []
            if args.once: commands.extend(args.once)
            if args.script: commands.extend(args.script.read_text(encoding="utf-8").splitlines())
            return run_oneshot(ser, reader, commands, csv_logger, args.truth)
        run_repl(ser, reader, lib)
    finally:
        reader.stop()
        if csv_logger: csv_logger.close()
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
