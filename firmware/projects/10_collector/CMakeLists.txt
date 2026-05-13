# Copyright 2026 IchiPing project
#
# SPDX-License-Identifier: BSD-3-Clause

cmake_minimum_required(VERSION 3.22.0)

cmake_path(APPEND conf_file_path ${CMAKE_CURRENT_LIST_DIR} ${board}_${core_id} prj.conf)
set(CONF_FILE ${conf_file_path} ${CONF_FILE})
include(${SdkRootDirPath}/cmake/extension/mcux.cmake)

project(ichiping_10_collector LANGUAGES C CXX ASM)

include("${board}_${core_id}/board_files.cmake")
include(${SdkRootDirPath}/CMakeLists.txt)

mcux_add_source(
    SOURCES main.c
            ../../shared/source/sai_mic.c
            ../../shared/source/sai_speaker.c
            ../../shared/source/dummy_audio.c
            ../../shared/source/ichiping_frame.c
            ../../shared/source/ichp_cmd.c
)

mcux_add_include(
    INCLUDES .
             ${board}_${core_id}/cm33_core0
             ${board}_${core_id}/pins
             ../../shared/include
)

mcux_convert_binary(BINARY ${APPLICATION_BINARY_DIR}/${MCUX_SDK_PROJECT_NAME}.bin)
