# Copyright 2026 IchiPing project
#
# SPDX-License-Identifier: BSD-3-Clause

mcux_add_configuration(
    CC "-DSDK_DEBUGCONSOLE=1"
    CX "-DSDK_DEBUGCONSOLE=1"
)

mcux_add_source(
    SOURCES ${board}_${core_id}/frdmmcxn947/board.c
            ${board}_${core_id}/frdmmcxn947/board.h
            ${board}_${core_id}/frdmmcxn947/clock_config.c
            ${board}_${core_id}/frdmmcxn947/clock_config.h
)

mcux_add_include(
    INCLUDES ${board}_${core_id}/frdmmcxn947
)

mcux_add_source(
    SOURCES ${board}_${core_id}/pins/pin_mux.c
            ${board}_${core_id}/pins/pin_mux.h
)

mcux_add_include(
    INCLUDES ${board}_${core_id}/pins
)

mcux_add_source(
    SOURCES ${board}_${core_id}/cm33_core0/app.h
            ${board}_${core_id}/cm33_core0/hardware_init.c
)

mcux_add_include(
    INCLUDES ${board}_${core_id}/cm33_core0
)
