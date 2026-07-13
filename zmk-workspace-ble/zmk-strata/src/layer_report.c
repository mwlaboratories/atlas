/*
 * strata layer reporter — broadcast the active ZMK layer to the host.
 *
 * Subscribes to layer state changes and, on each change, sends a raw-HID report
 * via zmk-raw-hid. The strata desktop overlay reads it from /dev/hidraw* and
 * shows the active layer's keymap.
 *
 * Only the (rare) layer change is sent here — keypresses reach the host as
 * normal HID input, so strata reads those from the host's evdev stream instead,
 * at no extra BLE/battery cost.
 *
 * Frame: [0x4C 'L', highest_layer, bitmask u32 LE].
 *
 * SPDX-License-Identifier: MIT
 */

#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include <zmk/event_manager.h>
#include <zmk/events/layer_state_changed.h>
#include <zmk/keymap.h>

#include <raw_hid/events.h>

LOG_MODULE_REGISTER(strata, CONFIG_ZMK_LOG_LEVEL);

#define STRATA_MSG_LAYER 0x4C /* 'L' */

/* Persistent: raw_hid_sent_event carries a pointer, read by the BLE transport
 * during synchronous event dispatch. Layer changes are infrequent and dispatched
 * in-thread, so a single static buffer is safe. */
static uint8_t report_buf[CONFIG_RAW_HID_REPORT_SIZE];

static void send_layer_report(void) {
    const uint32_t bitmask = (uint32_t)zmk_keymap_layer_state();
    memset(report_buf, 0, sizeof(report_buf));
    report_buf[0] = STRATA_MSG_LAYER;
    report_buf[1] = (uint8_t)zmk_keymap_highest_layer_active();
    report_buf[2] = (uint8_t)(bitmask & 0xFF);
    report_buf[3] = (uint8_t)((bitmask >> 8) & 0xFF);
    report_buf[4] = (uint8_t)((bitmask >> 16) & 0xFF);
    report_buf[5] = (uint8_t)((bitmask >> 24) & 0xFF);

    raise_raw_hid_sent_event((struct raw_hid_sent_event){
        .data = report_buf,
        .length = CONFIG_RAW_HID_REPORT_SIZE,
    });
}

static int layer_state_listener(const zmk_event_t *eh) {
    if (as_zmk_layer_state_changed(eh) != NULL) {
        send_layer_report();
    }
    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(strata_layer, layer_state_listener);
ZMK_SUBSCRIPTION(strata_layer, zmk_layer_state_changed);
