# Copyright 2026 IchiPing project
#
# SPDX-License-Identifier: BSD-3-Clause

# Paths below are relative to this file's directory
# (firmware/projects/<n>/${board}_${core_id}/), not the project root.

mcux_add_configuration(
    CC "-DSDK_DEBUGCONSOLE=1"
    CX "-DSDK_DEBUGCONSOLE=1"
)

mcux_add_source(
    SOURCES frdmmcxn947/board.c
            frdmmcxn947/board.h
            frdmmcxn947/clock_config.c
            frdmmcxn947/clock_config.h
)

mcux_add_include(
    INCLUDES frdmmcxn947
)

mcux_add_source(
    SOURCES pins/pin_mux.c
            pins/pin_mux.h
)

mcux_add_include(
    INCLUDES pins
)

mcux_add_source(
    SOURCES cm33_core0/app.h
            cm33_core0/hardware_init.c
)

mcux_add_include(
    INCLUDES cm33_core0
)
