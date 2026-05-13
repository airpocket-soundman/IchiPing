# Copyright 2026 IchiPing project
#
# SPDX-License-Identifier: BSD-3-Clause

cmake_minimum_required(VERSION 3.22.0)

cmake_path(APPEND conf_file_path ${CMAKE_CURRENT_LIST_DIR} ${board}_${core_id} prj.conf)
set(CONF_FILE ${conf_file_path} ${CONF_FILE})
include(${SdkRootDirPath}/cmake/extension/mcux.cmake)

project(ichiping_04_lvgl_test LANGUAGES C CXX ASM)

include("${board}_${core_id}/board_files.cmake")
include(${SdkRootDirPath}/CMakeLists.txt)

mcux_add_source(
    SOURCES main.c
            ../../shared/source/ili9341.c
            ../../shared/source/lv_port_disp.c
)

mcux_add_include(
    INCLUDES .
             ${board}_${core_id}/cm33_core0
             ${board}_${core_id}/pins
             ../../shared/include
)

# LVGL v9 sources must be pulled in separately. Either enable the SDK middleware:
#   mcux_add_macro(LV_USE_CONF_PATH="lv_conf.h")
# or clone lvgl into firmware/third_party/lvgl and:
#   mcux_add_source(SOURCES ../../third_party/lvgl/src/...)
# See README for the recommended path.

mcux_convert_binary(BINARY ${APPLICATION_BINARY_DIR}/${MCUX_SDK_PROJECT_NAME}.bin)
