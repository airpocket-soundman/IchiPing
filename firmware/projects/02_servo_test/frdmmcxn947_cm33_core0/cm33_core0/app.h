/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Project 02_servo_test — I²C bus to PCA9685 / LU9685.
 * SERVO_I2C_BASE / SERVO_I2C_CLK_FREQ are forced to LPI2C2 / FlexComm 2 at
 * the CMake -D level (see CMakeLists.txt) because LPI2C4 collides with the
 * OpenSDA debug UART on this board.
 */
#ifndef _APP_H_
#define _APP_H_

#define BOARD_SERVO_I2C_BASEADDR  LPI2C2
#define BOARD_SERVO_I2C_CLK_ATTACH kFRO12M_to_FLEXCOMM2
#define BOARD_SERVO_I2C_CLK_DIV    kCLOCK_DivFlexcom2Clk

#endif /* _APP_H_ */
