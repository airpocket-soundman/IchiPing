# Copyright 2026 IchiPing project
#
# SPDX-License-Identifier: BSD-3-Clause

cmake_minimum_required(VERSION 3.22.0)

cmake_path(APPEND conf_file_path ${CMAKE_CURRENT_LIST_DIR} ${board}_${core_id} prj.conf)
set(CONF_FILE ${conf_file_path} ${CONF_FILE})
include(${SdkRootDirPath}/cmake/extension/mcux.cmake)

project(ichiping_00_demo LANGUAGES C CXX ASM)

include("${board}_${core_id}/board_files.cmake")
include(${SdkRootDirPath}/CMakeLists.txt)

# Use the LU9685 backend for the servo driver to match 02_servo_test (the
# only servo board that has actually been bench-tested in this repo).
# Switch to SERVO_BACKEND_PCA9685 if the production board reverts to NXP.
mcux_add_configuration(
    CC "-DSERVO_BACKEND_LU9685_I2C"
)

mcux_add_source(
    SOURCES main.c
            ../../shared/source/ili9341.c
            ../../shared/source/pca9685.c
            ../../shared/source/lu9685.c
            ../../shared/source/ichiping_frame.c
            ../../shared/source/dummy_audio.c
)

mcux_add_include(
    INCLUDES .
             ${board}_${core_id}/cm33_core0
             ${board}_${core_id}/pins
             ../../shared/include
)

mcux_convert_binary(BINARY ${APPLICATION_BINARY_DIR}/${MCUX_SDK_PROJECT_NAME}.bin)
