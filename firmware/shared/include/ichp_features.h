/*
 * IchiPing 推論用特徴量抽出 — Welch log-magnitude PSD + baseline diff + INT8 量子化。
 *
 * 学習時 (pc/training/features.py samples_to_logmag_psd) と完全一致する設計:
 *
 *   - 16 kHz / 16-bit PCM (2 s = 32000 samples) を入力
 *   - 2048-pt Hann 窓 × 50% overlap (step 1024) で 30 segment
 *   - 各 segment を rFFT (CMSIS-DSP arm_rfft_fast_f32) → magnitude squared
 *   - segment 平均 → 10·log10 (= power dB) → DB_FLOOR (-80) でクランプ
 *   - DC bin を捨てて 1024 bin に
 *   - baseline (1024 bin、factory_baseline.h or RAM live) を per-bin で減算
 *   - INT8 量子化: x_int8 = round(x_fp32 / scale) + zero_point、clip [-128, 127]
 *
 * 出力は 1024 INT8 で、TFLite Micro の入力テンソル (1, 1, 1024, 1) NHWC に
 * そのまま流し込める。
 */

#ifndef ICHP_FEATURES_H_
#define ICHP_FEATURES_H_

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ICHP_FEAT_RATE_HZ      16000u
#define ICHP_FEAT_WINDOW_MS    2000u
#define ICHP_FEAT_WINDOW_SAMP  ((ICHP_FEAT_RATE_HZ * ICHP_FEAT_WINDOW_MS) / 1000u)   /* 32000 */
#define ICHP_FEAT_NFFT         2048u
#define ICHP_FEAT_NHOP         1024u            /* 50% overlap */
#define ICHP_FEAT_N_BINS       1024u            /* drop DC, keep bins 1..1024 */
#define ICHP_FEAT_DB_FLOOR     (-80.0f)

/* Welch セグメント数: (WINDOW_SAMP - NFFT) / NHOP + 1 = (32000 - 2048) / 1024 + 1 = 30 */
#define ICHP_FEAT_N_SEGMENTS   ((ICHP_FEAT_WINDOW_SAMP - ICHP_FEAT_NFFT) / ICHP_FEAT_NHOP + 1u)

/* CMSIS-DSP arm_rfft_fast_f32 が要求するワークバッファサイズ (2 × NFFT)。
 * Hann 窓係数も含めて呼び出し側で用意する。 */

/* 特徴量抽出コンテキスト。CMSIS-DSP rfft instance と固定窓を 1 度だけ初期化 → 推論ごとに使う。 */
typedef struct {
    void   *rfft_instance;          /* arm_rfft_fast_instance_f32 * (opaque to non-dsp callers) */
    float  *hann_window;            /* size NFFT, pre-computed Hann coefficients */
    float  *seg_buf;                /* size NFFT, working buffer for a single segment (windowed) */
    float  *fft_out;                /* size NFFT (complex packed), rFFT output */
    float  *accum_power;            /* size NFFT/2 + 1, segment-averaged power */
    bool    initialised;
} ichp_features_ctx_t;

/* Initialise context. Caller owns the buffers (typically static arrays in
 * main.c). Returns 0 on success, non-zero on CMSIS-DSP init failure. */
int ichp_features_init(ichp_features_ctx_t *ctx,
                       void   *rfft_instance,
                       float  *hann_window,
                       float  *seg_buf,
                       float  *fft_out,
                       float  *accum_power);

/* int16 PCM (length ICHP_FEAT_WINDOW_SAMP) → 1024-bin log-magnitude dB (float32)。
 * out_logmag は呼び出し側で N_BINS float 確保。学習時の
 * samples_to_logmag_psd と数値が一致するよう設計。 */
void ichp_features_logmag_psd(ichp_features_ctx_t *ctx,
                              const int16_t *samples,
                              float *out_logmag);

/* logmag - baseline → noise_diff (1024 bin float32 in-place で書き換え)。
 * baseline は 1024 bin float32 (factory_baseline.h を dequant したもの
 * または ichp_baseline_live の値)。 */
void ichp_features_subtract_baseline(float *logmag_inout,
                                     const float *baseline);

/* noise_diff_norm 経路用: 1024-bin float32 を per-frame zero-mean unit-variance
 * 正規化 (in-place)。学習側 samples_to_noise_diff_norm_features と数値一致。
 * SPK 音量や mic gain の絶対レベル変動に対する不変性を持たせる用。
 * noise_diff_norm モデルを焼く際は subtract_baseline 後、quantize_int8 前に
 * 必ず呼ぶこと。通常 noise_diff モデルでは呼ばない。 */
void ichp_features_normalize_frame(float *logmag_diff);

/* float32 1024-bin noise_diff → INT8 1024-bin (TFLite 入力用)。
 *   q[i] = clip(round(x[i] / scale) + zero_point, -128, 127)
 * scale/zero_point は model_data.h のヘッダコメントから取った
 * 学習時固定値 (現行モデル: scale=0.185412, zp=29)。 */
void ichp_features_quantize_int8(const float *logmag_diff,
                                 float scale, int32_t zero_point,
                                 int8_t *out_int8);

/* ---- Baseline 管理 ---- */

/* factory baseline (factory_baseline.h, INT8 量子化) を 1024 float に dequant。
 * 初回呼び出しで内部キャッシュに展開し、以降は同じ float32 配列を返す。 */
const float *ichp_baseline_factory(void);

/* live baseline (RAM 上、BL CALIBRATE で算出)。
 * RAM 永続: 電源切れたら消える。設計どおり。 */
const float *ichp_baseline_live(void);

/* live baseline が calibrated 済みか。 */
bool ichp_baseline_live_calibrated(void);

/* live baseline に新規 sample を追加し平均を更新する逐次インターフェース。
 * Calibrate コマンドが N frame 録音 → 各 frame について logmag_psd を計算 →
 * このフックを呼んで累積、最後に ichp_baseline_live_commit() で確定する。
 *
 * 呼び出し順:
 *   ichp_baseline_live_begin();
 *   for (i = 0; i < N; i++) {
 *       capture audio → ichp_features_logmag_psd(...) → float32 logmag[1024]
 *       ichp_baseline_live_accumulate(logmag);
 *   }
 *   ichp_baseline_live_commit(N);
 */
void ichp_baseline_live_begin(void);
void ichp_baseline_live_accumulate(const float *logmag);
void ichp_baseline_live_commit(uint16_t n_frames);

/* 統計: live baseline が何 frame で構築されたか。0 = uncalibrated。 */
uint16_t ichp_baseline_live_frames(void);

/* live baseline 破棄 (BL CLEAR)。calibrated フラグを下ろす。 */
void ichp_baseline_live_clear(void);

#ifdef __cplusplus
}
#endif

#endif /* ICHP_FEATURES_H_ */
