# 07_speaker_test — MAX98357A スピーカ疎通

MAX98357A（クラス D, I²S→アナログ）に **200 Hz / 1 kHz / 5 kHz 正弦波**、**chirp 200→8 kHz**、**無音** を順に流して耳で確認する。PC ソフトは不要、スピーカが鳴れば合格。

## 期待する動作

```
[cycle 1]
  200 Hz tone        ← 低音 1秒
  1 kHz tone         ← 中音 1秒（一番聞きやすい）
  5 kHz tone         ← 高音 1秒（耳キンキンする）
  chirp 200->8k      ← 推論で使うパッタリ系の音 2秒
  silence 1 s        ← 完全に止まればクラス D シャットダウン OK
```

判定:
- 無音 → BCLK が出ていない、もしくは VIN が 3V3 のままで 5V が来ていない
- ザザザ系のノイズ → DIN フローティング、または BCLK ↔ LRCLK の順序が逆
- 5 kHz だけ大きく歪む → Fs と BCLK の比率が 32× になっていない（MAX98357A は 32×Fs か 64×Fs しか受け付けない）

## 配線

| MAX98357A | FRDM-MCXN947 | 備考 |
|---|---|---|
| VIN | **5V 外部レール** | 3V3 だと音量取れない |
| GND | GND | |
| LRC | SAI0_FS | LRCLK |
| BCLK | SAI0_BCLK | |
| DIN | SAI0_TXD | |
| GAIN | フローティング | 既定 9 dB |
| SD | VIN | 常時 ON（GPIO でミュート切替したい場合は別ピンへ） |

## ビルド・実行

```
cd firmware/projects/07_speaker_test
# MCUXpresso for VS Code → Import Project → debug preset → build → run
```

## 既知の TODO

- `pins/pin_mux.c` の SAI0 ピン Alt 値は **MCUXpresso Pins tool で確認必須**
- 連続再生（DMA リング）に切り替えるときは `sai_speaker_start_streaming` を実装
