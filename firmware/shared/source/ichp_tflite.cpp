/*
 * IchiPing 推論エンジン実装 — TFLite Micro + Neutron NPU。
 *
 * C++ で書く必然性: TFLite Micro の MicroInterpreter / MicroAllocator /
 * MicroMutableOpResolver はすべて C++ クラス。C 側から見える API は
 * ichp_tflite.h の薄い extern "C" 関数群だけ。
 *
 * Op resolver の op 一覧は model_data.h (Neutron 変換済み TFLite) の中身に
 * 合わせて最小限だけ登録する。現行 v1 モデル (PINTO onnx2tf → neutron-converter,
 * 8/9 = 89% NPU) は次の op を含む:
 *   - NeutronGraph (NXP custom op、Neutron NPU 上で Conv2D + ReLU + AvgPool +
 *     1x1 Conv をまとめて実行)
 *   - Quantize / Dequantize (I/O 境界、CPU 実行)
 * NeutronGraph は custom op として resolver に手動登録する必要がある。
 *
 * SDK 依存:
 *   - tensorflow/lite/micro/...        (NXP MCUXpresso SDK middleware)
 *   - eIQ Toolkit Neutron driver       (libNeutronDriver.a + NeutronFirmware)
 *   prj.conf の CONFIG_MCUX_COMPONENT_middleware.eiq.tensorflow_lite_micro.* で有効化。
 */

#include "ichp_tflite.h"

extern "C" {
#include "fsl_common.h"
#include "fsl_debug_console.h"
}

#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/micro/micro_log.h"
#include "tensorflow/lite/schema/schema_generated.h"

/* NXP Neutron driver (libNeutronDriver.a) と TFLite Micro 側の NeutronGraph
 * op を取り込む。SDK の include path は CMSIS / TFLite Micro 共に -I で
 * 渡されている前提。 */
extern "C" {
#include "NeutronDriver.h"        /* neutronInit() / NeutronError / ENONE */
}
#include "tensorflow/lite/micro/kernels/neutron/neutron.h"  /* tflite::Register_NEUTRON_GRAPH() */

namespace {

/* MicroInterpreter / Allocator は static にして heap 不要にする。
 * Op resolver はコンパイル時 op 数を template 引数に持つ。
 * 現行 v1 モデル (PINTO onnx2tf → neutron-converter) の op set:
 *   NeutronGraph + Pad + Quantize + Dequantize + 余裕 (StridedSlice/Reshape) */
constexpr int kNumOps = 8;

tflite::MicroMutableOpResolver<kNumOps> *g_resolver = nullptr;
const tflite::Model *g_model = nullptr;
tflite::MicroInterpreter *g_interpreter = nullptr;
TfLiteTensor *g_input  = nullptr;
TfLiteTensor *g_output = nullptr;

/* MicroMutableOpResolver / MicroInterpreter を placement new で in-place 構築
 * するための領域 (TFLite Micro の流儀: heap を使わない)。 */
alignas(8) uint8_t g_resolver_storage[sizeof(tflite::MicroMutableOpResolver<kNumOps>)];
alignas(8) uint8_t g_interpreter_storage[sizeof(tflite::MicroInterpreter)];

/* 推論統計 */
uint32_t g_total_invokes  = 0;
uint32_t g_last_invoke_us = 0;

/* DWT cycle counter で us を測る。Cortex-M33 は DWT を持つ。
 * SystemCoreClock は SDK が更新する。 */
inline uint32_t dwt_enable_once() {
    if ((CoreDebug->DEMCR & CoreDebug_DEMCR_TRCENA_Msk) == 0) {
        CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
        DWT->CYCCNT       = 0;
        DWT->CTRL        |= DWT_CTRL_CYCCNTENA_Msk;
    }
    return DWT->CYCCNT;
}
inline uint32_t cycles_to_us(uint32_t cyc) {
    /* SystemCoreClock は Hz。1e6 を avoiding 32-bit overflow するため /1000 を 2 段で。 */
    return (uint32_t)((uint64_t)cyc * 1000000ull / (uint64_t)SystemCoreClock);
}

} /* anonymous namespace */

extern "C" {

ichp_tflite_status_t ichp_tflite_init(const uint8_t *model_data,
                                      size_t /* model_size */,
                                      uint8_t *arena,
                                      size_t arena_size)
{
    if (model_data == nullptr || arena == nullptr || arena_size < 4096) {
        return ICHP_TFLITE_ERR_ALLOC;
    }

    /* DWT 起動 (us 計測用) */
    (void)dwt_enable_once();

    /* Neutron driver の初期化 (op resolver より先)。
     * NeutronError は int32_t、成功は ENONE (= 0)。 */
    if (neutronInit() != ENONE) {
        return ICHP_TFLITE_ERR_INVOKE;
    }

    g_model = tflite::GetModel(model_data);
    if (g_model->version() != TFLITE_SCHEMA_VERSION) {
        return ICHP_TFLITE_ERR_MODEL_PARSE;
    }

    /* Op resolver: NeutronGraph custom op + 各種 builtin。
     * 現行モデルに必要なのは:
     *   NeutronGraph : NPU 上の Conv+ReLU+AvgPool+1x1Conv 一括 (8 op 分)
     *   Pad          : Neutron 境界で挿入される CPU op
     *   Quantize / Dequantize : 入出力境界の INT8↔FP32
     *   StridedSlice / Reshape : onnx2tf が稀に残す形状変換、保険で登録
     * Custom op 名 "NeutronGraph" は tflite::GetString_NEUTRON_GRAPH() と一致。 */
    g_resolver = new (g_resolver_storage)
        tflite::MicroMutableOpResolver<kNumOps>();
    if (g_resolver->AddCustom("NeutronGraph", tflite::Register_NEUTRON_GRAPH())
            != kTfLiteOk) {
        return ICHP_TFLITE_ERR_MODEL_PARSE;
    }
    if (g_resolver->AddPad()          != kTfLiteOk) return ICHP_TFLITE_ERR_MODEL_PARSE;
    if (g_resolver->AddQuantize()     != kTfLiteOk) return ICHP_TFLITE_ERR_MODEL_PARSE;
    if (g_resolver->AddDequantize()   != kTfLiteOk) return ICHP_TFLITE_ERR_MODEL_PARSE;
    if (g_resolver->AddStridedSlice() != kTfLiteOk) return ICHP_TFLITE_ERR_MODEL_PARSE;
    if (g_resolver->AddReshape()      != kTfLiteOk) return ICHP_TFLITE_ERR_MODEL_PARSE;

    g_interpreter = new (g_interpreter_storage)
        tflite::MicroInterpreter(g_model, *g_resolver,
                                 arena, arena_size);

    if (g_interpreter->AllocateTensors() != kTfLiteOk) {
        return ICHP_TFLITE_ERR_ALLOC;
    }

    g_input  = g_interpreter->input(0);
    g_output = g_interpreter->output(0);
    if (!g_input || !g_output) return ICHP_TFLITE_ERR_SHAPE;

    /* 入力形状チェック: (1, 1, 1024, 1) NHWC int8 */
    if (g_input->dims->size != 4
        || g_input->dims->data[0] != 1
        || g_input->dims->data[1] != 1
        || g_input->dims->data[2] != (int)ICHP_TFLITE_INPUT_LEN
        || g_input->dims->data[3] != 1
        || g_input->type != kTfLiteInt8) {
        return ICHP_TFLITE_ERR_SHAPE;
    }
    /* 出力形状チェック: (1, 1, 1, 32) NHWC int8 */
    if (g_output->dims->size != 4
        || g_output->dims->data[0] != 1
        || g_output->dims->data[3] != (int)ICHP_TFLITE_OUTPUT_LEN
        || g_output->type != kTfLiteInt8) {
        return ICHP_TFLITE_ERR_SHAPE;
    }
    return ICHP_TFLITE_OK;
}

void ichp_tflite_input_qparams(float *scale, int32_t *zero_point)
{
    if (!g_input) { if (scale) *scale = 1.0f; if (zero_point) *zero_point = 0; return; }
    if (scale)      *scale      = g_input->params.scale;
    if (zero_point) *zero_point = g_input->params.zero_point;
}

void ichp_tflite_output_qparams(float *scale, int32_t *zero_point)
{
    if (!g_output) { if (scale) *scale = 1.0f; if (zero_point) *zero_point = 0; return; }
    if (scale)      *scale      = g_output->params.scale;
    if (zero_point) *zero_point = g_output->params.zero_point;
}

ichp_tflite_status_t ichp_tflite_invoke(const int8_t *in_int8,
                                        int8_t *out_int8,
                                        ichp_tflite_result_t *result_out)
{
    if (!g_interpreter || !g_input || !g_output) return ICHP_TFLITE_ERR_NOT_INIT;
    if (!in_int8 || !out_int8) return ICHP_TFLITE_ERR_INVOKE;

    /* 入力テンソルへコピー (1024 byte) */
    int8_t *dst = g_input->data.int8;
    for (uint32_t i = 0; i < ICHP_TFLITE_INPUT_LEN; i++) dst[i] = in_int8[i];

    /* 推論 + us 計測 */
    const uint32_t cyc0 = DWT->CYCCNT;
    TfLiteStatus st = g_interpreter->Invoke();
    const uint32_t cyc1 = DWT->CYCCNT;
    if (st != kTfLiteOk) return ICHP_TFLITE_ERR_INVOKE;

    const uint32_t us = cycles_to_us(cyc1 - cyc0);
    g_last_invoke_us = us;
    g_total_invokes++;

    /* 出力テンソルから取り出し */
    const int8_t *src = g_output->data.int8;
    int8_t best_v = (int8_t)-128, second_v = (int8_t)-128;
    uint8_t best_i = 0, second_i = 0;
    for (uint32_t i = 0; i < ICHP_TFLITE_OUTPUT_LEN; i++) {
        const int8_t v = src[i];
        out_int8[i] = v;
        if (v > best_v) {
            second_v = best_v; second_i = best_i;
            best_v   = v;      best_i   = (uint8_t)i;
        } else if (v > second_v) {
            second_v = v;      second_i = (uint8_t)i;
        }
    }

    if (result_out) {
        result_out->argmax_idx    = best_i;
        result_out->argmax_logit  = best_v;
        result_out->second_idx    = second_i;
        result_out->second_logit  = second_v;
        result_out->invoke_us     = us;
    }
    return ICHP_TFLITE_OK;
}

uint32_t ichp_tflite_total_invokes(void)   { return g_total_invokes; }
uint32_t ichp_tflite_last_invoke_us(void)  { return g_last_invoke_us; }

} /* extern "C" */
