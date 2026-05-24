# Copyright 2026 IchiPing project
#
# SPDX-License-Identifier: BSD-3-Clause

cmake_minimum_required(VERSION 3.22.0)

cmake_path(APPEND conf_file_path ${CMAKE_CURRENT_LIST_DIR} ${board}_${core_id} prj.conf)
set(CONF_FILE ${conf_file_path} ${CONF_FILE})
include(${SdkRootDirPath}/cmake/extension/mcux.cmake)

project(ichiping_10_inference LANGUAGES C CXX ASM)

include("${board}_${core_id}/board_files.cmake")
include(${SdkRootDirPath}/CMakeLists.txt)

# 09 と同じ servo backend (PCA9685)。サーボはテスト用に動かせるよう同梱。
#
# 加えて TFLite Micro が third_party (kissfft 等) を引きずってくるため、
# SDK 既定の -Werror で「unused variable / function」などの軽微警告が fatal 化する。
# プロジェクト側を綺麗に保ったまま 3rd party のノイズだけ落とすため、
# 該当する -Wno-error=* を後置して -Werror を局所的に上書きする。
mcux_add_configuration(
    CC "-DSERVO_BACKEND_PCA9685 \
        -Wno-error=unused-variable -Wno-error=unused-function \
        -Wno-error=unused-parameter -Wno-error=maybe-uninitialized \
        -Wno-error=comment -Wno-error=sign-compare \
        -Wno-error=type-limits"
)

# 推論コア。09 と被るインフラ + 推論専用の features / tflite ラッパー。
# ichp_tflite.cpp は C++、main.c は C。TFLite Micro の C++ コードと
# Neutron neutron_lib もリンクされる (prj.conf 側で middleware 有効化)。
mcux_add_source(
    SOURCES main.c
            ../../shared/source/sai_mic.c
            ../../shared/source/sai_speaker.c
            ../../shared/source/ichiping_frame.c
            ../../shared/source/pca9685.c
            ../../shared/source/lu9685.c
            ../../shared/source/ili9341.c
            ../../shared/source/ichp_cmd.c
            ../../shared/source/servo_config.c
            ../../shared/source/pattern_lib.c
            ../../shared/source/spk_eq.c
            ../../shared/source/ichp_features.c
            ../../shared/source/ichp_tflite.cpp
)

mcux_add_include(
    INCLUDES .
             source
             ${board}_${core_id}/cm33_core0
             ${board}_${core_id}/pins
             ../../shared/include
)

# CMSIS-DSP / TFLite Micro / Neutron NPU runtime の有効化は prj.conf 側で
# CONFIG_MCUX_COMPONENT_* として宣言してある (Kconfig 経由)。
# SDK 側で component 名が異なる場合 prj.conf の方を調整する。

mcux_convert_binary(BINARY ${APPLICATION_BINARY_DIR}/${MCUX_SDK_PROJECT_NAME}.bin)
