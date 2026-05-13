/*
 * Copyright 2026 IchiPing project
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Project 05_usb_cdc_emitter — USB-FS CDC ACM device on MCXN947 USB1.
 * Endpoints / class config come from the SDK usb_device_cdc_vcom example
 * (usb_device_config.h, usb_device_descriptor.h, virtual_com.h).
 */
#ifndef _APP_H_
#define _APP_H_

/* MCXN947 has a Full-Speed device controller exposed as USB1.
 * Bulk-IN endpoint number is conventional in the SDK CDC example. */
#define BOARD_USB_DEVICE_CONTROLLER_ID  kUSB_ControllerLpcIp3511Fs0
#define BOARD_USB_IRQ                   USB1_IRQn
#define BOARD_USB_IRQ_PRIO              6U

#endif /* _APP_H_ */
