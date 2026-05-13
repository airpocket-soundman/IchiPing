# Copyright 2026 IchiPing project
#
# SPDX-License-Identifier: BSD-3-Clause

cmake_minimum_required(VERSION 3.22.0)

cmake_path(APPEND conf_file_path ${CMAKE_CURRENT_LIST_DIR} ${board}_${core_id} prj.conf)
set(CONF_FILE ${conf_file_path} ${CONF_FILE})
include(${SdkRootDirPath}/cmake/extension/mcux.cmake)

project(ichiping_05_usb_cdc_emitter LANGUAGES C CXX ASM)

include("${board}_${core_id}/board_files.cmake")
include(${SdkRootDirPath}/CMakeLists.txt)

mcux_add_source(
    SOURCES main.c
            ../../shared/source/ichiping_frame.c
            ../../shared/source/dummy_audio.c
)

mcux_add_include(
    INCLUDES .
             ${board}_${core_id}/cm33_core0
             ${board}_${core_id}/pins
             ../../shared/include
)

# USB device stack (CDC ACM) lives under the SDK's middleware/usb. The
# usb_device_descriptor.c, virtual_com.c, usb_device_config.h, usb_phy.c
# from the SDK's usb_device_cdc_vcom example must be added here too; see
# README for the exact copy list.

mcux_convert_binary(BINARY ${APPLICATION_BINARY_DIR}/${MCUX_SDK_PROJECT_NAME}.bin)
