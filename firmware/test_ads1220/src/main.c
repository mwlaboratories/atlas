/*
 * Atlas — Phase 1 ADS1220 bench bring-up test.
 *
 * Purpose: prove power + SPI + DRDY before adding the trackpoint and
 * reference resistors. Talks to the ADS1220, reads its 4 config
 * registers after a soft RESET, then sends START and watches DRDY.
 *
 * BENCH NOTE: this XIAO Plus has a broken MISO1 (D18 / P1.05) tab.
 * MISO is rerouted to D13 (I2S_WS / P1.01) in app.overlay. The PCB
 * design in tools/keyboard.yaml keeps D18 — this is bench-only.
 *
 * LED status (on-board RGB, active-LOW):
 *   blue flash on boot       — firmware loaded
 *   red solid                — SPI init or RESET command failed
 *   red blink                — register read failed mid-flight
 *   red+green (yellow)       — registers readable but values ≠ reset
 *                              defaults (chip alive, state unexpected)
 *   green solid              — SPI proven, registers match reset
 *   blue flicker             — DRDY firing (chip producing samples)
 */

#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/usb/usb_device.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(ads1220_test, LOG_LEVEL_INF);

/* ---- ADS1220 commands (datasheet table 15) ---- */
#define ADS1220_CMD_RESET        0x06
#define ADS1220_CMD_START        0x08
#define ADS1220_CMD_RREG(r, n)   (0x20 | (((r) & 0x3) << 2) | (((n) - 1) & 0x3))

/* Reset values: all four config registers read 0x00 after RESET. */
#define ADS1220_RESET_DEFAULT    0x00

/* ---- Devicetree handles ---- */
static const struct spi_dt_spec ads_spi = SPI_DT_SPEC_GET(
	DT_NODELABEL(ads1220),
	SPI_OP_MODE_MASTER | SPI_TRANSFER_MSB | SPI_WORD_SET(8) | SPI_MODE_CPHA,
	0);
static const struct gpio_dt_spec drdy   = GPIO_DT_SPEC_GET(DT_NODELABEL(drdy), gpios);
static const struct gpio_dt_spec led_r  = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);
static const struct gpio_dt_spec led_g  = GPIO_DT_SPEC_GET(DT_ALIAS(led1), gpios);
static const struct gpio_dt_spec led_b  = GPIO_DT_SPEC_GET(DT_ALIAS(led2), gpios);

/* ---- DRDY interrupt → blue flash ---- */
static struct gpio_callback drdy_cb;
static volatile uint32_t drdy_count;

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
	drdy_count++;
	led_set(&led_b, true);
	k_work_reschedule(&drdy_off_work, K_MSEC(40));
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

/* ---- Failure modes ---- */
static void fatal_solid_red(const char *why)
{
	LOG_ERR("FATAL: %s — solid red", why);
	led_set(&led_r, true);
	led_set(&led_g, false);
	led_set(&led_b, false);
	while (1) {
		k_msleep(1000);
	}
}

static void fatal_blink_red(const char *why)
{
	LOG_ERR("FATAL: %s — red blink", why);
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

	/* USB CDC console — wait briefly for host to connect before logging. */
	(void)usb_enable(NULL);
	k_msleep(2000);

	LOG_INF("=== Atlas ADS1220 Phase 1 bench test ===");
	LOG_INF("BENCH NOTE: MISO is on D13 (P1.01) — D18 broken on this XIAO");

	/* Configure LEDs as outputs (active-LOW). */
	if (!gpio_is_ready_dt(&led_r) || !gpio_is_ready_dt(&led_g) || !gpio_is_ready_dt(&led_b)) {
		return -1;
	}
	gpio_pin_configure_dt(&led_r, GPIO_OUTPUT_INACTIVE);
	gpio_pin_configure_dt(&led_g, GPIO_OUTPUT_INACTIVE);
	gpio_pin_configure_dt(&led_b, GPIO_OUTPUT_INACTIVE);

	/* Boot indicator: brief blue blink. */
	led_set(&led_b, true);
	k_msleep(400);
	led_set(&led_b, false);

	/* SPI ready? */
	if (!spi_is_ready_dt(&ads_spi)) {
		fatal_solid_red("SPI controller not ready");
	}

	/* DRDY input + edge interrupt. */
	if (!gpio_is_ready_dt(&drdy)) {
		fatal_solid_red("DRDY GPIO not ready");
	}
	gpio_pin_configure_dt(&drdy, GPIO_INPUT);
	gpio_pin_interrupt_configure_dt(&drdy, GPIO_INT_EDGE_TO_ACTIVE);
	gpio_init_callback(&drdy_cb, drdy_isr, BIT(drdy.pin));
	gpio_add_callback(drdy.port, &drdy_cb);

	/* Soft RESET; datasheet says wait ≥50µs before next command. */
	LOG_INF("Sending RESET...");
	err = ads_send_cmd(ADS1220_CMD_RESET);
	if (err) {
		fatal_solid_red("RESET command failed");
	}
	k_msleep(2);

	/* Read R0..R3. After reset all four should be 0x00. */
	LOG_INF("Reading config registers R0..R3");
	uint8_t reg[4] = { 0xFF, 0xFF, 0xFF, 0xFF };
	for (int i = 0; i < 4; i++) {
		err = ads_read_reg(i, &reg[i]);
		if (err) {
			fatal_blink_red("RREG failed");
		}
	}
	LOG_INF("R0=0x%02x  R1=0x%02x  R2=0x%02x  R3=0x%02x",
		reg[0], reg[1], reg[2], reg[3]);

	bool defaults_ok = (reg[0] == ADS1220_RESET_DEFAULT &&
			    reg[1] == ADS1220_RESET_DEFAULT &&
			    reg[2] == ADS1220_RESET_DEFAULT &&
			    reg[3] == ADS1220_RESET_DEFAULT);

	if (defaults_ok) {
		LOG_INF("Registers match reset defaults — wiring + SPI proven");
		led_set(&led_g, true);
	} else {
		LOG_WRN("SPI returned data, but values differ from reset defaults");
		led_set(&led_r, true);
		led_set(&led_g, true);  /* yellow-ish */
	}

	/* Send START — chip enters continuous-conversion mode at 20 SPS by
	 * default; DRDY should pulse low ~every 50 ms. */
	LOG_INF("Sending START — DRDY should now fire periodically");
	(void)ads_send_cmd(ADS1220_CMD_START);

	uint32_t last_count = 0;
	while (1) {
		k_msleep(2000);
		uint32_t now = drdy_count;
		LOG_INF("DRDY count: %u (+%u in last 2s)",
			now, now - last_count);
		last_count = now;
	}
}
