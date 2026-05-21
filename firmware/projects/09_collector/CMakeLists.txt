# Copyright 2026 IchiPing project
#
# SPDX-License-Identifier: BSD-3-Clause

cmake_minimum_required(VERSION 3.22.0)

cmake_path(APPEND conf_file_path ${CMAKE_CURRENT_LIST_DIR} ${board}_${core_id} prj.conf)
set(CONF_FILE ${conf_file_path} ${CONF_FILE})
include(${SdkRootDirPath}/cmake/extension/mcux.cmake)

project(ichiping_09_collector LANGUAGES C CXX ASM)

include("${board}_${core_id}/board_files.cmake")
include(${SdkRootDirPath}/CMakeLists.txt)

# Servo backend selection — matches 02_servo_test conventions.
# Switched to PCA9685 after observing ~30 deg back-and-forth swings on
# the LU9685 even with bulk capacitance on V+. PCA9685 has been the
# more reliable backend on this hardware. Flip back to LU9685 by
# swapping to SERVO_BACKEND_LU9685_I2C — servo_driver.h picks the
# implementation and the default I²C address (LU9685 = 0x1F, PCA9685
# = 0x40) at compile time. Both .c files are linked in either case.
mcux_add_configuration(
    CC "-DSERVO_BACKEND_PCA9685"
)

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
            ../../shared/source/collector_display.c
            ../../shared/source/pattern_lib.c
            ../../shared/source/spk_eq.c
)

mcux_add_include(
    INCLUDES .
             ${board}_${core_id}/cm33_core0
             ${board}_${core_id}/pins
             ../../shared/include
)

mcux_convert_binary(BINARY ${APPLICATION_BINARY_DIR}/${MCUX_SDK_PROJECT_NAME}.bin)
