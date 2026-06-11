# 09_collector ハング時の復旧手順

`collector_client.py` で採取しようとして以下のいずれかが起きた場合、ここの手順で復旧する。

## 症状

| サイン | 説明 |
|---|---|
| `timeout waiting for CLOSE ALL OK` | `plan: initial CLOSE ALL to sync door state...` の次でクライアントが 10 秒固まって落ちる |
| `collector_client` が `connected COM3 @ 921600 bps` も出さずに 0 byte 出力でハング | 別プロセスが COM3 を握ったまま死んでいる (zombie) |
| `serial.serialutil.SerialException: could not open port 'COM3': PermissionError(13...)` | 同上 |
| `Write timeout` (pyserial) | MCU の USB CDC スタックがハング |
| `SerialException: could not open port 'COM3': FileNotFoundError(2...)` | COM3 が Windows のデバイス一覧から消えた (USB enum 落ち) |

どれも本質的には「MCU か COM3 のソフト状態が前セッションを引きずって不整合になっている」状態。

## 復旧手順

### 1. zombie プロセスを掃除

`collector_client` が失敗した直後、Python プロセスが COM3 を握ったまま残ることがある。最初に必ず:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='uv.exe'" |
  Where-Object { $_.CommandLine -like '*collector_client*' -or $_.CommandLine -like '*_probe*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
```

### 2. USB ケーブルを物理的に抜き差し

**これが本質**。LinkServer の soft reset では PCA9685 や MCU の USB CDC スタックの状態が戻らないことがあるため、VCC を一度落とす必要がある。

### 3. (必要なら) firmware を再 flash

PING で生存確認するだけでもよいが、念のため再 flash しておくと初期状態が確実:

```powershell
Set-Location "d:\GitHub\IchiPing\firmware\projects\09_collector\debug"
& "C:\nxp\LinkServer_26.3.123\LinkServer.exe" flash MCXN947 load "ichiping_09_collector_cm33_core0.bin:0x0"
```

### 4. PING で疎通確認

`pc/_probe.py` (PING を 1 回打つ最小スクリプト) で `OK PONG <build date>` が返れば OK:

```powershell
& D:\GitHub\IchiPing\pc\.venv\Scripts\python.exe -u D:\GitHub\IchiPing\pc\_probe.py
```

### 5. collector_client 起動

`-u` (unbuffered) を付けておくと task output がリアルタイムに見えてデバッグしやすい:

```bash
cd /d/GitHub/IchiPing/pc
uv run python -u collector_client.py --port COM3 --plan plans/full_32_train_vXX.yaml --run-id full_32_train_vXX
```

s00000/ に frame_*.wav が増えはじめれば成功。

## やってはいけないこと

- **ファームをいじって直そうとする**。同じバイナリが前日動いていたのに今日動かないなら、原因は MCU のハード状態であってコードではない。CLOSE ALL の各 step に DBG を仕込んでも、USB CDC が死んでいたら DBG ログ自体届かない (今日 1 回これで時間を溶かした)。
- **soft reset (LinkServer flash 含む) だけで済ませる**。PCA9685 のバス状態は MCU リセットでは戻らない。

## なぜ昨晩は動いたのか

昨晩は `TFT_SPI_BAUD` を 1 MHz → 20 MHz に上げた直後で、新規 flash 直後の clean な状態だった。今日は MCU/PCA9685 が一晩中通電のまま放置されていて、bus がどこかで詰まっていた可能性が高い (再現性は今のところ未検証)。常用するなら採取セッション開始前にいったん USB を抜き差ししておくと無難。
