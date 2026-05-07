/*
 * Atlas — Phase 2 ADS1220 strain-gauge bench test.
 *
 * Phase 1 (smoke test) is preserved as a boot-time sanity gate: RESET,
 * read R0..R3, expect all 0x00. If that passes, the firmware proceeds
 * to configure the chip for ratiometric strain-gauge reading and
 * streams X/Y conversion results over USB CDC serial.
 *
 * BENCH NOTE: this XIAO Plus has a broken MISO1 (D18 / P1.05) tab.
 * MISO is rerouted to D13 (I2S_WS / P1.01) in app.overlay. The PCB
 * design in tools/keyboard.yaml keeps D18 — bench-only override.
 *
 * Wiring expected by this firmware (Phase 2 complete):
 *   AIN0  ← trackpoint [x]    (X-axis strain output)
 *   AIN1  ← trackpoint [y]    (Y-axis strain output)
 *   REFP0 ← trackpoint [a]    (excitation high, IDAC1 sourced here)
 *   REFN0 ← trackpoint [b]    (excitation low, also tied to AGND)
 *   AIN2  = midpoint of 2x 2400Ω divider between REFP0 and REFN0
 *   shield → AGND             (one wire from outside tab to AGND)
 *
 * LED status:
 *   blue flash on boot    — firmware loaded
 *   solid red             — SPI/RESET fail
 *   red blink             — register read fail
 *   yellow                — Phase 1 reset check ≠ defaults
 *   solid green           — Phase 2 streaming OK
 *   blue flicker          — activity above threshold (stick deflected)
 */

#include <math.h>

#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/usb/usb_device.h>
#include <zephyr/usb/class/usb_hid.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(ads1220_test, LOG_LEVEL_INF);

/* ---- ADS1220 commands (datasheet table 15) ---- */
#define ADS1220_CMD_RESET        0x06
#define ADS1220_CMD_START        0x08
#define ADS1220_CMD_RREG(r, n)   (0x20 | (((r) & 0x3) << 2) | (((n) - 1) & 0x3))
#define ADS1220_CMD_WREG(r, n)   (0x40 | (((r) & 0x3) << 2) | (((n) - 1) & 0x3))

/* MUX settings for register 0 (upper nibble). AINP - AINN. */
#define MUX_AIN0_AIN2  0x1   /* X axis: strain output minus divider midpoint */
#define MUX_AIN1_AIN2  0x3   /* Y axis: same idea */

/* Gain field for register 0 (bits [3:1]). The strain bridge produces
 * mV-range differentials; with gain=1 we threw away ~5 bits of
 * resolution. Gain=64 doubles the per-push delta over gain=32, so
 * the cursor reaches HID full speed at half the physical deflection. */
#define GAIN_64X       0x6   /* 110: ×64 */

/* Configuration after RESET:
 *   R1 = 0x60  175 SPS, single-shot, normal mode, no temp, no burnout
 *              (faster sample rate → snappier mouse response)
 *   R2 = 0x44  external Vref via REFP0/REFN0, no 50/60Hz filter,
 *              low-side switch open, IDAC = 500 µA
 *   R3 = 0xA0  IDAC1 routed to REFP0, IDAC2 disabled, DRDY only on
 *              dedicated pin
 *   R0 alternates per axis: MUX<<4 | GAIN<<1 | PGA_BYPASS=0
 */
#define ADS1220_R1_VAL  0x60
#define ADS1220_R2_VAL  0x44
#define ADS1220_R3_VAL  0xA0

/* Baseline / activity tuning. */
#define BASELINE_SAMPLES    32        /* per-axis samples to average for zero */
#define ACTIVITY_THRESHOLD  20000     /* |centered x| or |y| > this → blue LED */

/* Mouse motion mapping. Tuned for gain=64. Deadzone is *radial* — the
 * dead region is a disc of radius MOUSE_DEADZONE in (cx, cy) space, not
 * a per-axis band. That makes diagonals respond identically to cardinal
 * pushes at the same magnitude. */
#define MOUSE_DEADZONE      20000     /* radius (LSB) of the dead disc */
#define MOUSE_DIVISOR       4000      /* full HID speed (±127) at ~530k deflection */

/* ---- USB HID mouse ---- */
static const uint8_t hid_report_desc[] = {
	HID_USAGE_PAGE(HID_USAGE_GEN_DESKTOP),
	HID_USAGE(HID_USAGE_GEN_DESKTOP_MOUSE),
	HID_COLLECTION(HID_COLLECTION_APPLICATION),
		HID_USAGE(HID_USAGE_GEN_DESKTOP_POINTER),
		HID_COLLECTION(HID_COLLECTION_PHYSICAL),
			/* 3 buttons */
			HID_USAGE_PAGE(HID_USAGE_GEN_BUTTON),
			HID_USAGE_MIN8(1),
			HID_USAGE_MAX8(3),
			HID_LOGICAL_MIN8(0),
			HID_LOGICAL_MAX8(1),
			HID_REPORT_COUNT(3),
			HID_REPORT_SIZE(1),
			HID_INPUT(0x02),
			/* 5 bits padding to round byte 0 */
			HID_REPORT_COUNT(1),
			HID_REPORT_SIZE(5),
			HID_INPUT(0x03),
			/* X / Y, signed 8-bit relative */
			HID_USAGE_PAGE(HID_USAGE_GEN_DESKTOP),
			HID_USAGE(HID_USAGE_GEN_DESKTOP_X),
			HID_USAGE(HID_USAGE_GEN_DESKTOP_Y),
			HID_LOGICAL_MIN8(-127),
			HID_LOGICAL_MAX8(127),
			HID_REPORT_SIZE(8),
			HID_REPORT_COUNT(2),
			HID_INPUT(0x06),
		HID_END_COLLECTION,
	HID_END_COLLECTION,
};

static const struct device *hid_dev;

static void map_mouse_xy(int32_t cx, int32_t cy, int8_t *dx_out, int8_t *dy_out)
{
	float fx = (float)cx;
	float fy = (float)cy;
	float r2 = fx * fx + fy * fy;
	float dz = (float)MOUSE_DEADZONE;

	if (r2 < dz * dz) {
		*dx_out = 0;
		*dy_out = 0;
		return;
	}

	float r = sqrtf(r2);
	/* Subtract the dead radius from the magnitude, then scale by the
	 * divisor. Direction is preserved by multiplying back through
	 * (cx/r, cy/r). */
	float scale = (r - dz) / (r * (float)MOUSE_DIVISOR);

	float fdx = fx * scale;
	float fdy = fy * scale;

	if (fdx >  127.0f) fdx =  127.0f;
	if (fdx < -127.0f) fdx = -127.0f;
	if (fdy >  127.0f) fdy =  127.0f;
	if (fdy < -127.0f) fdy = -127.0f;

	*dx_out = (int8_t)fdx;
	*dy_out = (int8_t)fdy;
}

static void hid_send(int8_t dx, int8_t dy)
{
	if (hid_dev == NULL) {
		return;
	}
	uint8_t report[3] = { 0, (uint8_t)dx, (uint8_t)dy };
	(void)hid_int_ep_write(hid_dev, report, sizeof(report), NULL);
}

/* ---- Devicetree handles ---- */
static const struct spi_dt_spec ads_spi = SPI_DT_SPEC_GET(
	DT_NODELABEL(ads1220),
	SPI_OP_MODE_MASTER | SPI_TRANSFER_MSB | SPI_WORD_SET(8) | SPI_MODE_CPHA,
	0);
static const struct gpio_dt_spec drdy   = GPIO_DT_SPEC_GET(DT_NODELABEL(drdy), gpios);
static const struct gpio_dt_spec led_r  = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);
static const struct gpio_dt_spec led_g  = GPIO_DT_SPEC_GET(DT_ALIAS(led1), gpios);
static const struct gpio_dt_spec led_b  = GPIO_DT_SPEC_GET(DT_ALIAS(led2), gpios);

/* ---- DRDY synchronisation ---- */
static K_SEM_DEFINE(drdy_sem, 0, 1);
static struct gpio_callback drdy_cb;

static void drdy_off_handler(struct k_work *w);
K_WORK_DELAYABLE_DEFINE(drdy_off_work, drdy_off_handler);

static void led_set(const struct gpio_dt_spec *l, bool on)
{
	gpio_pin_set_dt(l, on ? 1 : 0);
}

static void drdy_off_handler(struct k_work *w)
{
	led_set(&led_b, false);
}

static void drdy_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
	k_sem_give(&drdy_sem);
}

/* ---- ADS1220 SPI helpers ---- */
static int ads_send_cmd(uint8_t cmd)
{
	struct spi_buf tx = { .buf = &cmd, .len = 1 };
	struct spi_buf_set tx_set = { .buffers = &tx, .count = 1 };
	return spi_write_dt(&ads_spi, &tx_set);
}

static int ads_read_reg(uint8_t reg, uint8_t *val)
{
	uint8_t tx_buf[2] = { ADS1220_CMD_RREG(reg, 1), 0xFF };
	uint8_t rx_buf[2] = { 0, 0 };
	struct spi_buf tx = { .buf = tx_buf, .len = 2 };
	struct spi_buf rx = { .buf = rx_buf, .len = 2 };
	struct spi_buf_set tx_set = { .buffers = &tx, .count = 1 };
	struct spi_buf_set rx_set = { .buffers = &rx, .count = 1 };

	int err = spi_transceive_dt(&ads_spi, &tx_set, &rx_set);
	if (err == 0) {
		*val = rx_buf[1];
	}
	return err;
}

static int ads_write_reg(uint8_t reg, uint8_t val)
{
	uint8_t cmd[2] = { ADS1220_CMD_WREG(reg, 1), val };
	struct spi_buf tx = { .buf = cmd, .len = 2 };
	struct spi_buf_set tx_set = { .buffers = &tx, .count = 1 };
	return spi_write_dt(&ads_spi, &tx_set);
}

/* Read a 24-bit two's-complement conversion result. */
static int ads_read_data(int32_t *out)
{
	uint8_t tx_buf[3] = { 0xFF, 0xFF, 0xFF };
	uint8_t rx_buf[3] = { 0, 0, 0 };
	struct spi_buf tx = { .buf = tx_buf, .len = 3 };
	struct spi_buf rx = { .buf = rx_buf, .len = 3 };
	struct spi_buf_set tx_set = { .buffers = &tx, .count = 1 };
	struct spi_buf_set rx_set = { .buffers = &rx, .count = 1 };

	int err = spi_transceive_dt(&ads_spi, &tx_set, &rx_set);
	if (err) {
		return err;
	}

	int32_t raw = ((int32_t)rx_buf[0] << 24) |
		      ((int32_t)rx_buf[1] << 16) |
		      ((int32_t)rx_buf[2] << 8);
	*out = raw >> 8;     /* arithmetic shift sign-extends from 24 to 32 bits */
	return 0;
}

/* Run one single-shot conversion on the given MUX setting and return the
 * 24-bit signed result. Blocks on DRDY with a 100 ms timeout. */
static int ads_read_axis(uint8_t mux, int32_t *out)
{
	uint8_t r0 = (mux << 4) | (GAIN_64X << 1);  /* PGA enabled */
	int err = ads_write_reg(0, r0);
	if (err) {
		return err;
	}

	k_sem_reset(&drdy_sem);
	err = ads_send_cmd(ADS1220_CMD_START);
	if (err) {
		return err;
	}

	if (k_sem_take(&drdy_sem, K_MSEC(100)) != 0) {
		return -ETIMEDOUT;
	}

	return ads_read_data(out);
}

/* ---- Failure modes ---- */
static void fatal_solid_red(const char *why)
{
	LOG_ERR("FATAL: %s", why);
	led_set(&led_r, true);
	led_set(&led_g, false);
	led_set(&led_b, false);
	while (1) {
		k_msleep(1000);
	}
}

static void fatal_blink_red(const char *why)
{
	LOG_ERR("FATAL: %s", why);
	led_set(&led_g, false);
	led_set(&led_b, false);
	while (1) {
		led_set(&led_r, true);
		k_msleep(200);
		led_set(&led_r, false);
		k_msleep(200);
	}
}

int main(void)
{
	int err;

	/* Set up HID device before usb_enable so the descriptor is registered. */
	hid_dev = device_get_binding("HID_0");
	if (hid_dev != NULL) {
		usb_hid_register_device(hid_dev,
					hid_report_desc, sizeof(hid_report_desc),
					NULL);
		usb_hid_init(hid_dev);
	}

	(void)usb_enable(NULL);
	k_msleep(2000);

	LOG_INF("=== Atlas ADS1220 Phase 2 strain-gauge bench test ===");
	LOG_INF("BENCH NOTE: MISO is on D13 (P1.01) — D18 broken on this XIAO");

	/* ---- LEDs ---- */
	if (!gpio_is_ready_dt(&led_r) || !gpio_is_ready_dt(&led_g) || !gpio_is_ready_dt(&led_b)) {
		return -1;
	}
	gpio_pin_configure_dt(&led_r, GPIO_OUTPUT_INACTIVE);
	gpio_pin_configure_dt(&led_g, GPIO_OUTPUT_INACTIVE);
	gpio_pin_configure_dt(&led_b, GPIO_OUTPUT_INACTIVE);

	led_set(&led_b, true);
	k_msleep(400);
	led_set(&led_b, false);

	/* ---- Peripherals ---- */
	if (!spi_is_ready_dt(&ads_spi)) {
		fatal_solid_red("SPI controller not ready");
	}
	if (!gpio_is_ready_dt(&drdy)) {
		fatal_solid_red("DRDY GPIO not ready");
	}
	gpio_pin_configure_dt(&drdy, GPIO_INPUT);
	gpio_pin_interrupt_configure_dt(&drdy, GPIO_INT_EDGE_TO_ACTIVE);
	gpio_init_callback(&drdy_cb, drdy_isr, BIT(drdy.pin));
	gpio_add_callback(drdy.port, &drdy_cb);

	/* ---- Phase 1 sanity gate: reset → registers should all be 0x00 ---- */
	LOG_INF("Phase 1: RESET + register check");
	err = ads_send_cmd(ADS1220_CMD_RESET);
	if (err) {
		fatal_solid_red("RESET command failed");
	}
	k_msleep(2);

	uint8_t reg[4];
	for (int i = 0; i < 4; i++) {
		err = ads_read_reg(i, &reg[i]);
		if (err) {
			fatal_blink_red("RREG failed");
		}
	}
	LOG_INF("Reset state: R0=0x%02x R1=0x%02x R2=0x%02x R3=0x%02x",
		reg[0], reg[1], reg[2], reg[3]);

	bool defaults_ok = (reg[0] == 0 && reg[1] == 0 && reg[2] == 0 && reg[3] == 0);
	if (!defaults_ok) {
		LOG_WRN("Reset values unexpected — proceeding anyway");
		led_set(&led_r, true);
		led_set(&led_g, true);  /* yellow warning */
	}

	/* ---- Phase 2: configure for strain-gauge measurement ---- */
	LOG_INF("Phase 2: configuring registers for ratiometric strain reading");
	if (ads_write_reg(1, ADS1220_R1_VAL) ||
	    ads_write_reg(2, ADS1220_R2_VAL) ||
	    ads_write_reg(3, ADS1220_R3_VAL)) {
		fatal_solid_red("WREG failed during Phase 2 setup");
	}

	/* Read back to confirm. */
	for (int i = 1; i < 4; i++) {
		ads_read_reg(i, &reg[i]);
	}
	LOG_INF("Configured:  R1=0x%02x R2=0x%02x R3=0x%02x",
		reg[1], reg[2], reg[3]);
	LOG_INF("  → 20 SPS single-shot, ext Vref REFP0/REFN0, IDAC=500uA→REFP0");

	/* IDAC + PGA settling. Datasheet: IDAC needs ~200 µs to reach final
	 * value; give it a margin to let the bridge stabilise too. */
	k_msleep(50);

	/* ---- Baseline auto-zero: average BASELINE_SAMPLES at rest ---- */
	LOG_INF("Capturing %d-sample baseline per axis (don't touch the stick)...",
		BASELINE_SAMPLES);
	int64_t sum_x = 0, sum_y = 0;
	for (int i = 0; i < BASELINE_SAMPLES; i++) {
		int32_t x, y;
		if (ads_read_axis(MUX_AIN0_AIN2, &x) ||
		    ads_read_axis(MUX_AIN1_AIN2, &y)) {
			fatal_blink_red("baseline read failed");
		}
		sum_x += x;
		sum_y += y;
	}
	int32_t base_x = (int32_t)(sum_x / BASELINE_SAMPLES);
	int32_t base_y = (int32_t)(sum_y / BASELINE_SAMPLES);
	LOG_INF("Baseline: X=%d Y=%d", base_x, base_y);

	if (defaults_ok) {
		led_set(&led_g, true);  /* solid green = streaming */
	}

	/* ---- Phase 2 main loop ---- */
	LOG_INF("Streaming X/Y centered deltas (gain=32x, baseline-zeroed)...");
	uint32_t err_count = 0;
	while (1) {
		int32_t x = 0, y = 0;

		err = ads_read_axis(MUX_AIN0_AIN2, &x);
		if (err) {
			err_count++;
			LOG_ERR("X read err %d (total %u)", err, err_count);
			k_msleep(100);
			continue;
		}

		err = ads_read_axis(MUX_AIN1_AIN2, &y);
		if (err) {
			err_count++;
			LOG_ERR("Y read err %d (total %u)", err, err_count);
			k_msleep(100);
			continue;
		}

		int32_t cx = x - base_x;
		int32_t cy = y - base_y;

		/* HID mouse report. Radial deadzone + linear scale. */
		int8_t dx, dy;
		map_mouse_xy(cx, cy, &dx, &dy);
		if (dx || dy) {
			hid_send(dx, dy);
		}

		LOG_INF("X=%8d  Y=%8d  dx=%4d dy=%4d", cx, cy, dx, dy);

		/* Activity flicker on the centered values. */
		int32_t ax = (cx < 0) ? -cx : cx;
		int32_t ay = (cy < 0) ? -cy : cy;
		if (ax > ACTIVITY_THRESHOLD || ay > ACTIVITY_THRESHOLD) {
			led_set(&led_b, true);
			k_work_reschedule(&drdy_off_work, K_MSEC(80));
		}

		/* No explicit sleep — each axis read takes ~50 ms (20 SPS),
		 * so full X+Y cycle is ~100 ms naturally (10 Hz). */
	}
}
