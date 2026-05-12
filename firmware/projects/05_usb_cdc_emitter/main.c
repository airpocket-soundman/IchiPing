/*
 * IchiPing — USB CDC emitter (MCUXpresso project skeleton)
 *
 * Replaces the OpenSDA LPUART path of project 01_dummy_emitter with a
 * USB CDC Virtual COM device on the MCXN947's native USB controller.
 *
 * Why bother:
 *   - OpenSDA UART caps out around 921 600 bps. A 64 KB ICHP frame
 *     therefore takes ~560 ms — fine for v0.1 PoC, painful once we
 *     stream real INMP441 audio every 100 ms.
 *   - USB Full-Speed (12 Mbps) carries the same frame in ~50 ms, leaving
 *     comfortable headroom for tighter cadence + bidirectional commands.
 *   - The PC side (pc/receiver.py / pc/verify.py) does NOT change — it
 *     opens whatever serial port the OS exposes, baud parameter is
 *     ignored by CDC, ICHP framing is identical on the wire.
 *
 * This file is intentionally a SKELETON. The full USB CDC stack lives in
 * MCUXpresso SDK middleware/usb. You need to:
 *
 *   1. Import the `usb_device_cdc_vcom` SDK example for `frdmmcxn947`.
 *   2. Copy this main.c on top of the example's main.c.
 *   3. Keep all of:
 *        usb_device_descriptor.c / .h
 *        virtual_com.c / .h          (CDC ACM glue from the example)
 *        usb_device_config.h
 *        usb_phy.c / .h
 *      from the SDK example unchanged. They define USB_DeviceTask(),
 *      USB_DeviceCdcAcmSend(), s_cdcVcom, etc.
 *   4. Add ../../shared/source/ichiping_frame.c
 *           and  ../../shared/source/dummy_audio.c
 *      to the project, with include path ../../shared/include.
 *
 * Reference: NXP AN12153 (MCXN94x USB Device Stack Bring-up).
 *
 * Wiring:
 *   - Use the **target** USB-C connector (J21 on FRDM-MCXN947), NOT
 *     OpenSDA USB. OpenSDA USB stays for power + SWD debug; J21 is the
 *     enumerated CDC device.
 *   - 5 V VBUS comes from the USB host (or use the dual-USB power
 *     selector on the FRDM jumpers).
 */

#include "pin_mux.h"
#include "clock_config.h"
#include "board.h"
#include "fsl_debug_console.h"

/* These three headers come from the SDK USB stack — keep the example's
 * versions unmodified. */
#include "usb_device_config.h"
#include "usb.h"
#include "usb_device.h"

/* Provided by the cdc_vcom example, NOT by this file. */
extern void USB_DeviceApplicationInit(void);
extern void USB_DeviceTask(void *handle);
extern usb_status_t USB_DeviceCdcAcmSend(class_handle_t handle,
                                         uint8_t ep, uint8_t *buffer, uint32_t length);

/* Shared frame format + dummy audio (linked from firmware/shared/source). */
#include "ichiping_frame.h"
#include "dummy_audio.h"

/* ----- ICHP framing knobs (identical to 01_dummy_emitter for parity) ----- */
#define ICHP_SAMPLE_RATE     16000u
#define ICHP_SAMPLE_COUNT    32000u                                   /* 2 s @ 16 kHz */
#define ICHP_FRAME_PERIOD_MS 1000u                                    /* USB lets us run faster */
#define ICHP_BULK_IN_EP      USB_CDC_VCOM_BULK_IN_ENDPOINT            /* defined by the SDK example */

/* Buffers — placed in regular SRAM. The MCXN947 has plenty for this. */
static int16_t s_audio_buf[ICHP_SAMPLE_COUNT];
static uint8_t s_tx_buf[ICHP_HEADER_SIZE + ICHP_SAMPLE_COUNT * sizeof(int16_t) + ICHP_CRC_SIZE]
    __attribute__((aligned(4)));

/* ----- SysTick uptime ----- */
static volatile uint32_t s_uptime_ms = 0;
void SysTick_Handler(void) { s_uptime_ms++; }
static void systick_init_1ms(void) { (void)SysTick_Config(SystemCoreClock / 1000u); }
static void delay_ms(uint32_t ms) {
    uint32_t end = s_uptime_ms + ms;
    while ((int32_t)(s_uptime_ms - end) < 0) { __WFI(); }
}

/* The SDK example exposes a flag for "the host has opened the COM port and
 * we have an attached terminal". Names vary by SDK version — adjust to
 * whichever your imported example uses. Common spellings:
 *     s_cdcVcom.attach        // older NXP SDK
 *     g_deviceComposite->...  // composite device path
 * The check below assumes the simpler vcom example. */
extern volatile uint8_t g_UsbCdcVcomReady;   /* you wire this up in virtual_com.c */

static void random_servo_angles(uint16_t seq, float out[5]) {
    for (int i = 0; i < 5; i++) {
        uint32_t x = (uint32_t)seq * 2654435761u + (uint32_t)i * 0x9E3779B1u;
        out[i] = (float)(x % 91u);
    }
}

int main(void) {
    BOARD_InitBootPins();
    BOARD_InitBootClocks();
    BOARD_InitDebugConsole();
    systick_init_1ms();
    dummy_audio_seed(0xC4C4C4C4u);

    PRINTF("IchiPing USB CDC emitter: %u Hz, %u samples/frame, USB FS 12 Mbps\r\n",
           (unsigned)ICHP_SAMPLE_RATE, (unsigned)ICHP_SAMPLE_COUNT);

    /* Brings up USB PHY, USB device controller, CDC ACM class, registers
     * descriptors, kicks the device into "attached, awaiting host". From
     * the SDK example, unchanged. */
    USB_DeviceApplicationInit();

    /* Wait for host enumeration + COM port open. The example example sets
     * g_UsbCdcVcomReady from the DTR control line handler. */
    PRINTF("waiting for host to open the virtual COM port...\r\n");
    while (!g_UsbCdcVcomReady) {
        USB_DeviceTask(NULL);
    }
    PRINTF("host ready, streaming\r\n");

    uint16_t seq = 0;
    for (;;) {
        USB_DeviceTask(NULL);   /* must run frequently — events, transfers */

        float servo_deg[5];
        random_servo_angles(seq, servo_deg);
        dummy_audio_generate(s_audio_buf, ICHP_SAMPLE_COUNT, ICHP_SAMPLE_RATE);

        size_t frame_size = ichp_pack_frame(
            s_tx_buf, sizeof(s_tx_buf),
            seq, s_uptime_ms,
            ICHP_SAMPLE_RATE, ICHP_SAMPLE_COUNT,
            servo_deg, s_audio_buf);

        if (frame_size > 0) {
            /* CDC ACM bulk-IN endpoint. Split a 64 KB frame into 512 B
             * chunks if your SDK version doesn't auto-fragment. Most do. */
            usb_status_t s = USB_DeviceCdcAcmSend(NULL, ICHP_BULK_IN_EP,
                                                  s_tx_buf, (uint32_t)frame_size);
            if (s != kStatus_USB_Success) {
                PRINTF("USB send failed: 0x%x\r\n", (unsigned)s);
                /* On disconnect or stall, drop back to wait-for-host. */
                while (!g_UsbCdcVcomReady) { USB_DeviceTask(NULL); }
            }
        }

        seq++;
        delay_ms(ICHP_FRAME_PERIOD_MS);
    }
}
