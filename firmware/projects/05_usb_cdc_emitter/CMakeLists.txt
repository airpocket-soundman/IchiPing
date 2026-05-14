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
            virtual_com.c
            virtual_com.h
            usb_device_descriptor.c
            usb_device_descriptor.h
            ../../shared/source/ichiping_frame.c
            ../../shared/source/dummy_audio.c
)

mcux_add_include(
    INCLUDES .
             ${board}_${core_id}/cm33_core0
             ${board}_${core_id}/pins
             ../../shared/include
)

# Match the SDK usb_device_cdc_vcom/bm example's build flags. USB_STACK_BM
# selects the bare-metal stack flavour; the 0x2000-byte heap/stack are sized
# for the CDC class's internal buffers and descriptor parsing.
mcux_add_macro(
    CC "-DUSB_STACK_BM"
)
mcux_add_armgcc_configuration(
    LD "-Xlinker --defsym=__heap_size__=0x2000"
)
mcux_add_armgcc_configuration(
    LD "-Xlinker --defsym=__stack_size__=0x2000"
)

mcux_convert_binary(BINARY ${APPLICATION_BINARY_DIR}/${MCUX_SDK_PROJECT_NAME}.bin)
