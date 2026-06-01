/*
 * IchiPing 推論用特徴量抽出の実装。詳細は ichp_features.h を参照。
 *
 * CMSIS-DSP 依存: arm_rfft_fast_f32 (rFFT) + arm_cmplx_mag_squared_f32
 * (power spectrum). MCXN947 の Cortex-M33 で動く。PowerQuad-FFT に
 * 置き換えると更に速いが、まずは標準 CMSIS-DSP で組んで動かす。
 */

#include "ichp_features.h"

#include "arm_math.h"

#include <math.h>
#include <string.h>

/* factory_baseline.h は static const int8_t 配列 + scale/offset の #define を
 * 提供する。10_inference プロジェクトの source/ にあるので、CMakeLists で
 * -I.../10_inference/source が通っている前提 (現状そうなっている)。 */
#include "factory_baseline.h"

/* ---- factory baseline キャッシュ ---- */

static float    s_factory_cache[ICHP_FEAT_N_BINS];
static bool     s_factory_cached = false;

const float *ichp_baseline_factory(void)
{
    if (!s_factory_cached) {
        const float scale  = ICHP_FACTORY_BASELINE_SCALE;
        const float offset = ICHP_FACTORY_BASELINE_OFFSET;
        for (size_t i = 0; i < ICHP_FEAT_N_BINS; i++) {
            s_factory_cache[i] = (float)ichp_factory_baseline[i] * scale + offset;
        }
        s_factory_cached = true;
    }
    return s_factory_cache;
}

/* ---- live baseline ---- */

static float    s_live_baseline[ICHP_FEAT_N_BINS];
static float    s_live_accum[ICHP_FEAT_N_BINS];
static uint16_t s_live_n_frames = 0;
static bool     s_live_calibrated = false;

const float *ichp_baseline_live(void)         { return s_live_baseline; }
bool      ichp_baseline_live_calibrated(void) { return s_live_calibrated; }
uint16_t  ichp_baseline_live_frames(void)     { return s_live_n_frames; }

void ichp_baseline_live_clear(void)
{
    s_live_calibrated = false;
    s_live_n_frames   = 0;
    memset(s_live_accum,    0, sizeof(s_live_accum));
    memset(s_live_baseline, 0, sizeof(s_live_baseline));
}

void ichp_baseline_live_begin(void)
{
    memset(s_live_accum, 0, sizeof(s_live_accum));
}

void ichp_baseline_live_accumulate(const float *logmag)
{
    for (size_t i = 0; i < ICHP_FEAT_N_BINS; i++) {
        s_live_accum[i] += logmag[i];
    }
}

void ichp_baseline_live_commit(uint16_t n_frames)
{
    if (n_frames == 0u) return;
    const float inv = 1.0f / (float)n_frames;
    for (size_t i = 0; i < ICHP_FEAT_N_BINS; i++) {
        s_live_baseline[i] = s_live_accum[i] * inv;
    }
    s_live_n_frames   = n_frames;
    s_live_calibrated = true;
}

/* ---- Welch log-mag PSD ---- */

int ichp_features_init(ichp_features_ctx_t *ctx,
                       void   *rfft_instance,
                       float  *hann_window,
                       float  *seg_buf,
                       float  *fft_out,
                       float  *accum_power)
{
    if (!ctx || !rfft_instance || !hann_window || !seg_buf || !fft_out || !accum_power) {
        return -1;
    }
    arm_rfft_fast_instance_f32 *inst = (arm_rfft_fast_instance_f32 *)rfft_instance;
    arm_status st = arm_rfft_fast_init_f32(inst, ICHP_FEAT_NFFT);
    if (st != ARM_MATH_SUCCESS) return -2;

    /* Hann 窓係数を 1 度だけ生成。w[n] = 0.5 - 0.5*cos(2πn/(N-1))
     * scipy.signal.windows.hann(N, sym=True) と一致。 */
    const float two_pi_over_nm1 = 6.28318530718f / (float)(ICHP_FEAT_NFFT - 1u);
    for (uint32_t n = 0; n < ICHP_FEAT_NFFT; n++) {
        hann_window[n] = 0.5f - 0.5f * cosf((float)n * two_pi_over_nm1);
    }

    ctx->rfft_instance = rfft_instance;
    ctx->hann_window   = hann_window;
    ctx->seg_buf       = seg_buf;
    ctx->fft_out       = fft_out;
    ctx->accum_power   = accum_power;
    ctx->initialised   = true;
    return 0;
}

/* Welch "spectrum" scaling (scipy.signal.welch scaling="spectrum"):
 *   norm = 1 / (sum(window)^2)
 * 各 segment の |FFT|^2 をこの係数で割って onesided にする。 */
static void compute_welch_spectrum(ichp_features_ctx_t *ctx,
                                   const int16_t *samples,
                                   float *out_power_onesided)
{
    arm_rfft_fast_instance_f32 *inst =
        (arm_rfft_fast_instance_f32 *)ctx->rfft_instance;

    /* 窓和 (window sum) → scaling norm。 */
    float win_sum = 0.0f;
    for (uint32_t n = 0; n < ICHP_FEAT_NFFT; n++) win_sum += ctx->hann_window[n];
    const float scale = 1.0f / (win_sum * win_sum);

    const uint32_t n_bins_onesided = ICHP_FEAT_NFFT / 2u + 1u;   /* 1025 */
    memset(ctx->accum_power, 0, n_bins_onesided * sizeof(float));

    /* int16 → float32 正規化係数 ([-1, 1] へ)。学習側 dataset と一致。 */
    const float inv32768 = 1.0f / 32768.0f;

    uint32_t n_seg = 0;
    for (uint32_t seg = 0; seg + ICHP_FEAT_NFFT <= ICHP_FEAT_WINDOW_SAMP;
         seg += ICHP_FEAT_NHOP) {
        /* 1. 窓掛け + float 化 */
        for (uint32_t i = 0; i < ICHP_FEAT_NFFT; i++) {
            ctx->seg_buf[i] = (float)samples[seg + i] * inv32768 * ctx->hann_window[i];
        }
        /* 2. rFFT (forward=0): out は packed complex
         *    [Re(0), Re(N/2), Re(1), Im(1), Re(2), Im(2), ..., Re(N/2-1), Im(N/2-1)] */
        arm_rfft_fast_f32(inst, ctx->seg_buf, ctx->fft_out, 0);
        /* 3. Power = Re^2 + Im^2 (one-sided)
         *    Bin 0 = Re(0)^2, Bin N/2 = Re(N/2)^2 — どちらも純実数。
         *    Bin 1..N/2-1 は (Re, Im) ペア。 */
        ctx->accum_power[0]                 += ctx->fft_out[0] * ctx->fft_out[0];
        ctx->accum_power[ICHP_FEAT_NFFT/2u] += ctx->fft_out[1] * ctx->fft_out[1];
        for (uint32_t k = 1; k < ICHP_FEAT_NFFT/2u; k++) {
            const float re = ctx->fft_out[2u*k];
            const float im = ctx->fft_out[2u*k + 1u];
            ctx->accum_power[k] += (re*re + im*im);
        }
        n_seg++;
    }

    /* 4. segment 平均 + Welch scaling */
    const float inv_nseg = (n_seg > 0u) ? (1.0f / (float)n_seg) : 1.0f;
    for (uint32_t k = 0; k < n_bins_onesided; k++) {
        out_power_onesided[k] = ctx->accum_power[k] * inv_nseg * scale;
    }
}

void ichp_features_logmag_psd(ichp_features_ctx_t *ctx,
                              const int16_t *samples,
                              float *out_logmag)
{
    if (!ctx || !ctx->initialised) return;

    /* out_power_onesided として accum_power をそのまま使い回す (compute_welch_spectrum
     * 内で in-place 書き換え) のは不可なので、別領域として fft_out 後半などを
     * 一時利用するのもアリだが、ここでは accum_power が compute 後にちょうど
     * 平均値で埋まっていることを利用して直接 log を取る。 */
    arm_rfft_fast_instance_f32 *inst =
        (arm_rfft_fast_instance_f32 *)ctx->rfft_instance;
    (void)inst;

    compute_welch_spectrum(ctx, samples, ctx->accum_power);
    /* accum_power は 1025 bin (one-sided)、bin 0 を捨てて 1024 bin out。 */
    const float eps = 1e-12f;
    for (uint32_t k = 0; k < ICHP_FEAT_N_BINS; k++) {
        float p = ctx->accum_power[k + 1u];
        if (p < eps) p = eps;
        float db = 10.0f * log10f(p);
        if (db < ICHP_FEAT_DB_FLOOR) db = ICHP_FEAT_DB_FLOOR;
        out_logmag[k] = db;
    }
}

void ichp_features_subtract_baseline(float *logmag_inout,
                                     const float *baseline)
{
    for (uint32_t k = 0; k < ICHP_FEAT_N_BINS; k++) {
        logmag_inout[k] -= baseline[k];
    }
}

void ichp_features_normalize_frame(float *logmag_diff)
{
    /* per-frame zero-mean unit-variance 正規化。学習側
     * samples_to_noise_diff_norm_features と数値一致させる。 */
    float mean = 0.0f;
    for (uint32_t k = 0; k < ICHP_FEAT_N_BINS; k++) {
        mean += logmag_diff[k];
    }
    mean /= (float)ICHP_FEAT_N_BINS;

    float var = 0.0f;
    for (uint32_t k = 0; k < ICHP_FEAT_N_BINS; k++) {
        logmag_diff[k] -= mean;
        var += logmag_diff[k] * logmag_diff[k];
    }
    float std = sqrtf(var / (float)ICHP_FEAT_N_BINS) + 1e-6f;
    float inv_std = 1.0f / std;
    for (uint32_t k = 0; k < ICHP_FEAT_N_BINS; k++) {
        logmag_diff[k] *= inv_std;
    }
}

void ichp_features_quantize_int8(const float *logmag_diff,
                                 float scale, int32_t zero_point,
                                 int8_t *out_int8)
{
    const float inv_scale = 1.0f / scale;
    for (uint32_t k = 0; k < ICHP_FEAT_N_BINS; k++) {
        int32_t q = (int32_t)lrintf(logmag_diff[k] * inv_scale) + zero_point;
        if (q < -128) q = -128;
        if (q >  127) q =  127;
        out_int8[k] = (int8_t)q;
    }
}
