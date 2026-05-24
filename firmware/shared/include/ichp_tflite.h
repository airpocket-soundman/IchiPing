/*
 * IchiPing 推論エンジンラッパー — TFLite Micro + Neutron NPU 実行を C 側から呼べる API。
 *
 * C++ 側で MicroInterpreter を立てて、C 側からは int8 入力ポインタを渡して
 * 32-class logits (int8) を受け取るだけの薄いラッパー。NXP eIQ neutron_lib が
 * 提供する Neutron op resolver を内部で登録する。
 *
 * Tensor arena は呼び出し側 (main.c) で静的に確保 — MCXN947 の SRAM 量と
 * モデル依存。XL Neutron モデル (108 KB) の実測値だと 48 KB 程度で足りる。
 * 不足時は ichp_tflite_init が非 0 を返すので、ARENA_SIZE を増やす。
 *
 * Input/Output 契約 (現行 v1 Neutron XL モデル):
 *   入力: int8 NHWC (1, 1, 1024, 1)、scale=0.185412、zero_point=29
 *   出力: int8 NHWC (1, 1, 1, 32)、scale=...、zero_point=...
 *         スケールは推論時に動的に取得 (モデル更新で変わるため)。
 *         argmax は INT8 でも符号付きで動くので量子化解除は不要 (argmax 比較のみ)。
 *
 * model_data.h の TFLite バイナリは:
 *   firmware/projects/10_inference/source/model_data.h
 *   = const uint8_t ichp_model_data[108336];
 */

#ifndef ICHP_TFLITE_H_
#define ICHP_TFLITE_H_

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ICHP_TFLITE_INPUT_LEN    1024u
#define ICHP_TFLITE_OUTPUT_LEN   32u

/* 初期化結果。 */
typedef enum {
    ICHP_TFLITE_OK              = 0,
    ICHP_TFLITE_ERR_MODEL_PARSE = -1,
    ICHP_TFLITE_ERR_ALLOC       = -2,
    ICHP_TFLITE_ERR_INVOKE      = -3,
    ICHP_TFLITE_ERR_SHAPE       = -4,
    ICHP_TFLITE_ERR_NOT_INIT    = -5,
} ichp_tflite_status_t;

/* 推論結果の付加情報。RESULT line 整形時に使う。 */
typedef struct {
    uint8_t  argmax_idx;            /* 0..31 */
    int8_t   argmax_logit;          /* INT8 出力の最大値 */
    int8_t   second_logit;          /* 2 位の logit 値 (margin 確認用) */
    uint8_t  second_idx;            /* 2 位の class idx */
    uint32_t invoke_us;             /* 推論本体に要した時間 (us) */
} ichp_tflite_result_t;

/* TFLite Micro + Neutron 初期化。
 *
 *   model_data           : ichp_model_data (model_data.h)
 *   model_size           : ICHP_MODEL_DATA_LEN
 *   arena                : caller-allocated SRAM buffer
 *   arena_size           : sizeof(arena) — 推奨 48 KB 以上
 *
 * 成功時 ICHP_TFLITE_OK を返す。失敗時はエラーコード + 内部状態は破棄。 */
ichp_tflite_status_t ichp_tflite_init(const uint8_t *model_data,
                                      size_t model_size,
                                      uint8_t *arena,
                                      size_t arena_size);

/* 入力スケール/zero_point を取得 (Welch 出力の INT8 量子化に使う)。
 * ichp_tflite_init 後でないと値は未定義。 */
void ichp_tflite_input_qparams(float *scale, int32_t *zero_point);

/* 出力スケール/zero_point を取得。argmax だけ使うなら不要だが、
 * softmax 確率の dequant が要るときに使う。 */
void ichp_tflite_output_qparams(float *scale, int32_t *zero_point);

/* 推論実行。
 *   in_int8    : 1024 INT8 (NHWC (1,1,1024,1))、量子化済み入力
 *   out_int8   : 32 INT8 (NHWC (1,1,1,32))、書き戻し用、呼び出し側が確保
 *   result_out : argmax + timing 情報、NULL なら argmax 計算もスキップ
 *
 * us 計測は SysTick で行う (caller が wallclock 取得関数を渡す形ではなく、
 * 内部で DWT cycle counter を使う実装でも可)。 */
ichp_tflite_status_t ichp_tflite_invoke(const int8_t *in_int8,
                                        int8_t *out_int8,
                                        ichp_tflite_result_t *result_out);

/* 統計: 累積推論回数、平均推論時間 (us)。 */
uint32_t ichp_tflite_total_invokes(void);
uint32_t ichp_tflite_last_invoke_us(void);

#ifdef __cplusplus
}
#endif

#endif /* ICHP_TFLITE_H_ */
