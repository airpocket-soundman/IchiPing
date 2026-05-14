# 09_audio_stream — INMP441 連続ストリーミング

[01_dummy_emitter](../01_dummy_emitter/) が **合成 chirp** を流していたのを、**実マイク (INMP441) の音**に差し替えた版。ICHP フレーム形式と 921600 bps の経路はそのままなので、PC 側 [pc/receiver.py](../../../pc/receiver.py) は変更ゼロで使える。

## 使い方

```
# 1) MCU 焼き
cd firmware/projects/09_audio_stream
# MCUXpresso for VS Code → Import → debug → build → run

# 2) PC 受信
cd pc
python receiver.py --port COM7 --baud 921600 --out ../captures/09
```

`captures/09/frame_NNNNNN.wav` に 2 秒分の WAV が連続で保存される（**1 フレーム ~ 560 ms かかる**ので、2 秒録音 + 0.56 秒送信 = 約 0.4 fps）。

## v0.1 の制約

- **UART 921600 bps はストリーミング下限ぎりぎり**。レート上げたければ [05_usb_cdc_emitter](../05_usb_cdc_emitter/) と組み合わせる（こちらは ~20 fps 出る）
- 現状 `sai_mic_record_blocking` を使うので、**録音中は他の処理ができない**。EDMA に切り替えれば録音中に TX を流せて 2 倍弱の cadence になる

## 配線

[06_mic_test](../06_mic_test/README.md) と完全に同じ（**マイクのみ**, SAI1 TX-master + RX-sync）。J1.1 SCK / J1.3 WS / J1.15 SD、SJ10=2-3 / SJ11=1-2。スピーカ (MAX98357A) は接続不要。
