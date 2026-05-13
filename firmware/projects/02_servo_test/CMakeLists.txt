# Copyright 2026 IchiPing project
#
# SPDX-License-Identifier: BSD-3-Clause

cmake_minimum_required(VERSION 3.22.0)

cmake_path(APPEND conf_file_path ${CMAKE_CURRENT_LIST_DIR} ${board}_${core_id} prj.conf)
set(CONF_FILE ${conf_file_path} ${CONF_FILE})
include(${SdkRootDirPath}/cmake/extension/mcux.cmake)

project(ichiping_02_servo_test LANGUAGES C CXX ASM)

include("${board}_${core_id}/board_files.cmake")
include(${SdkRootDirPath}/CMakeLists.txt)

# Override main.c defaults so I²C goes to LP_FLEXCOMM2 instead of LPI2C4
# (FC4 is already used by the OpenSDA debug UART and cannot serve both).
# Switch backend by changing SERVO_BACKEND_PCA9685 → SERVO_BACKEND_LU9685_I2C.
mcux_add_configuration(
    CC "-DSERVO_BACKEND_PCA9685\
        -DSERVO_I2C_BASE=LPI2C2\
        -DSERVO_I2C_CLK_FREQ=CLOCK_GetLPFlexCommClkFreq(2)"
)

mcux_add_source(
    SOURCES main.c
            ../../shared/source/pca9685.c
            ../../shared/source/lu9685.c
)

mcux_add_include(
    INCLUDES .
             ${board}_${core_id}/cm33_core0
             ${board}_${core_id}/pins
             ../../shared/include
)

mcux_convert_binary(BINARY ${APPLICATION_BINARY_DIR}/${MCUX_SDK_PROJECT_NAME}.bin)
