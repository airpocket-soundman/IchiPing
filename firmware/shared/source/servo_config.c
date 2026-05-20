/*
 * IchiPing — servo home/open store + coordinate conversion.
 * See servo_config.h for the public contract and docs/servo_coords.html
 * for the rationale behind the dual coordinate system.
 *
 * Flash persistence (MCXN947 PFlash, ROM API):
 *   One 128-byte page is stored at SERVO_CFG_FLASH_ADDR. That address sits
 *   in the very last sector of the second 1 MB flash block (m_flash1 region
 *   in the linker script), which the single-core core0 image does not use.
 *   The page carries a magic, version, payload size, CRC-16, and the
 *   home / open / kind arrays. servo_config_init() reads + verifies; on
 *   any mismatch (blank flash, wrong magic, bad CRC, version skew) it
 *   silently falls back to SERVO_CONFIG_DEFAULTS.
 */

#include "servo_config.h"
#include "ichiping_frame.h"   /* ichp_crc16_ccitt */

#include <math.h>
#include <string.h>

#include "fsl_common.h"
#include "fsl_flash.h"

/* Compile-time defaults — used at first boot before any SAVE HOME, or
 * when the flashed blob fails CRC/magic. After in-field calibration use
 * SET HOME / SET OPEN + SAVE HOME to persist your real positions; the
 * flashed values take precedence over these defaults.
 *
 * home = 180° (CLOSE) and open = 0° (OPEN) gives every servo the full
 * mechanical sweep that PCA9685_SG90_MIN/MAX_TICK can produce. The
 * reversed orientation (open < home) is normal — servo_config_to_logical
 * applies sign = -1 so the display still reads 0 at closed and grows
 * positive as the door/window swings open. */
const servo_config_t SERVO_CONFIG_DEFAULTS = {
    .home_deg = { 180.0f, 180.0f, 180.0f, 180.0f, 180.0f },
    .open_deg = {   0.0f,   0.0f,   0.0f,   0.0f,   0.0f },
    .kind     = {
        ICHP_SERVO_KIND_WINDOW,   /* window_a */
        ICHP_SERVO_KIND_WINDOW,   /* window_b */
        ICHP_SERVO_KIND_WINDOW,   /* window_c */
        ICHP_SERVO_KIND_DOOR,     /* door_AB  */
        ICHP_SERVO_KIND_DOOR,     /* door_BC  */
    },
};

static servo_config_t s_cfg;
static bool           s_inited = false;

/* ---- Flash blob layout (one PFlash page = 128 bytes) ---- */

#define SERVO_CFG_FLASH_ADDR    0x001FE000u  /* last sector of m_flash1 (8 KB) */
#define SERVO_CFG_FLASH_MAGIC   0x56535049u  /* 'IPSV' little-endian */
#define SERVO_CFG_FLASH_VERSION 1u
#define SERVO_CFG_BLOB_SIZE     128u         /* MCXN947 PFlash page granule */
#define SERVO_CFG_SECTOR_SIZE   0x2000u      /* MCXN947 PFlash sector = 8 KB */

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t payload_size;          /* covered payload bytes (sanity check) */
    uint16_t crc16;                 /* CRC-16/CCITT-FALSE over payload only */
    uint16_t reserved16;            /* must be 0 */
    /* ---- payload (60 bytes, CRC covers this region) ---- */
    float    home_deg[ICHP_SERVO_COUNT];   /* 20 */
    float    open_deg[ICHP_SERVO_COUNT];   /* 20 */
    uint32_t kind[ICHP_SERVO_COUNT];       /* 20 (enum widened to u32) */
    /* ---- pad to one page so FLASH_Program writes a full granule ---- */
    uint8_t  pad[SERVO_CFG_BLOB_SIZE - 16 - 60];
} servo_config_blob_t;

_Static_assert(sizeof(servo_config_blob_t) == SERVO_CFG_BLOB_SIZE,
               "servo_config_blob_t must fit in one PFlash page");

#define SERVO_CFG_PAYLOAD_OFFSET  16u
#define SERVO_CFG_PAYLOAD_SIZE    (3u * ICHP_SERVO_COUNT * 4u)  /* home+open+kind */

static uint16_t blob_crc(const servo_config_blob_t *b) {
    return ichp_crc16_ccitt((const uint8_t *)b + SERVO_CFG_PAYLOAD_OFFSET,
                            SERVO_CFG_PAYLOAD_SIZE);
}

/* After erase/program the flash data cache may hold stale 0xFF lines; clear
 * the cache and speculation buffer so a subsequent read sees programmed
 * bytes. Mirrors the NXP romapi/flashiap example. */
static void flash_post_write_sync(void) {
    if ((SYSCON->NVM_CTRL & SYSCON_NVM_CTRL_DIS_FLASH_CACHE_MASK) == 0U) {
        SYSCON->NVM_CTRL |= SYSCON_NVM_CTRL_CLR_FLASH_CACHE_MASK;
        SYSCON->NVM_CTRL &= ~SYSCON_NVM_CTRL_CLR_FLASH_CACHE_MASK;
    }
    if ((SYSCON->NVM_CTRL & SYSCON_NVM_CTRL_DIS_MBECC_ERR_INST_MASK) == 0U
        && (SYSCON->NVM_CTRL & SYSCON_NVM_CTRL_DIS_MBECC_ERR_DATA_MASK) == 0U) {
        if ((SYSCON->NVM_CTRL & SYSCON_NVM_CTRL_DIS_FLASH_SPEC_MASK) == 0U) {
            SYSCON->NVM_CTRL |= SYSCON_NVM_CTRL_DIS_FLASH_SPEC_MASK;
            SYSCON->NVM_CTRL &= ~SYSCON_NVM_CTRL_DIS_FLASH_SPEC_MASK;
        }
        if ((SYSCON->NVM_CTRL & SYSCON_NVM_CTRL_DIS_DATA_SPEC_MASK) == 0U) {
            SYSCON->NVM_CTRL |= SYSCON_NVM_CTRL_DIS_DATA_SPEC_MASK;
            SYSCON->NVM_CTRL &= ~SYSCON_NVM_CTRL_DIS_DATA_SPEC_MASK;
        }
    }
}

/* Read + validate the persisted blob. Returns true on a good copy and
 * fills *out with the payload. */
static bool flash_load(servo_config_t *out) {
    const servo_config_blob_t *b =
        (const servo_config_blob_t *)SERVO_CFG_FLASH_ADDR;

    if (b->magic        != SERVO_CFG_FLASH_MAGIC)   return false;
    if (b->version      != SERVO_CFG_FLASH_VERSION) return false;
    if (b->payload_size != SERVO_CFG_PAYLOAD_SIZE)  return false;
    if (b->crc16        != blob_crc(b))             return false;

    memcpy(out->home_deg, b->home_deg, sizeof(out->home_deg));
    memcpy(out->open_deg, b->open_deg, sizeof(out->open_deg));
    for (uint8_t i = 0; i < ICHP_SERVO_COUNT; i++) {
        out->kind[i] = (ichp_servo_kind_t)b->kind[i];
    }
    return true;
}

bool servo_config_init(void)
{
    s_inited = true;
    if (flash_load(&s_cfg)) {
        /* Preserve kind[] from defaults if flashed copy has bogus values.
         * Anything other than the two known enum constants is treated as a
         * window (safest fallback for display range). */
        for (uint8_t i = 0; i < ICHP_SERVO_COUNT; i++) {
            if (s_cfg.kind[i] != ICHP_SERVO_KIND_WINDOW
             && s_cfg.kind[i] != ICHP_SERVO_KIND_DOOR) {
                s_cfg.kind[i] = SERVO_CONFIG_DEFAULTS.kind[i];
            }
        }
        return true;
    }
    s_cfg = SERVO_CONFIG_DEFAULTS;
    return false;
}

const servo_config_t *servo_config_get(void)
{
    if (!s_inited) (void)servo_config_init();
    return &s_cfg;
}

bool servo_config_set_home(uint8_t servo_idx, float mech_deg)
{
    if (!s_inited) (void)servo_config_init();
    if (servo_idx >= ICHP_SERVO_COUNT) return false;
    s_cfg.home_deg[servo_idx] = mech_deg;
    return true;
}

bool servo_config_set_open(uint8_t servo_idx, float mech_deg)
{
    if (!s_inited) (void)servo_config_init();
    if (servo_idx >= ICHP_SERVO_COUNT) return false;
    s_cfg.open_deg[servo_idx] = mech_deg;
    return true;
}

int servo_config_save_flash(void)
{
    if (!s_inited) (void)servo_config_init();

    /* Compose the blob in RAM. Page-aligned 128 B; pad bytes left at 0. */
    servo_config_blob_t blob;
    memset(&blob, 0, sizeof(blob));
    blob.magic        = SERVO_CFG_FLASH_MAGIC;
    blob.version      = SERVO_CFG_FLASH_VERSION;
    blob.payload_size = SERVO_CFG_PAYLOAD_SIZE;
    blob.reserved16   = 0;
    memcpy(blob.home_deg, s_cfg.home_deg, sizeof(blob.home_deg));
    memcpy(blob.open_deg, s_cfg.open_deg, sizeof(blob.open_deg));
    for (uint8_t i = 0; i < ICHP_SERVO_COUNT; i++) {
        blob.kind[i] = (uint32_t)s_cfg.kind[i];
    }
    blob.crc16 = blob_crc(&blob);

    flash_config_t fc;
    memset(&fc, 0, sizeof(fc));
    if (FLASH_Init(&fc) != kStatus_Success) return -1;

    /* Erase the whole sector that contains our page. The other 7 KB of the
     * sector stay 0xFF — fine since nothing else uses them. */
    status_t s = FLASH_Erase(&fc, SERVO_CFG_FLASH_ADDR,
                             SERVO_CFG_SECTOR_SIZE, kFLASH_ApiEraseKey);
    if (s != kStatus_Success) return -2;
    flash_post_write_sync();

    s = FLASH_VerifyErase(&fc, SERVO_CFG_FLASH_ADDR, SERVO_CFG_SECTOR_SIZE);
    if (s != kStatus_Success) return -3;

    s = FLASH_Program(&fc, SERVO_CFG_FLASH_ADDR,
                      (uint8_t *)&blob, sizeof(blob));
    if (s != kStatus_Success) return -4;
    flash_post_write_sync();

    /* Read-back verify against the just-written copy. */
    servo_config_t check;
    if (!flash_load(&check)) return -5;
    if (memcmp(check.home_deg, s_cfg.home_deg, sizeof(check.home_deg)) != 0) return -6;
    if (memcmp(check.open_deg, s_cfg.open_deg, sizeof(check.open_deg)) != 0) return -6;

    return 0;
}

float servo_config_logical_max(uint8_t servo_idx)
{
    if (!s_inited) (void)servo_config_init();
    if (servo_idx >= ICHP_SERVO_COUNT) return 0.0f;
    return (s_cfg.kind[servo_idx] == ICHP_SERVO_KIND_DOOR)
         ? ICHP_LOGICAL_MAX_DOOR
         : ICHP_LOGICAL_MAX_WINDOW;
}

float servo_config_to_logical(uint8_t servo_idx, float mech_deg)
{
    if (!s_inited) (void)servo_config_init();
    if (servo_idx >= ICHP_SERVO_COUNT) return 0.0f;
    const float home = s_cfg.home_deg[servo_idx];
    const float open = s_cfg.open_deg[servo_idx];
    const float sign = (open >= home) ? 1.0f : -1.0f;
    return sign * (mech_deg - home);
}

float servo_config_to_mechanical(uint8_t servo_idx, float logical_deg)
{
    if (!s_inited) (void)servo_config_init();
    if (servo_idx >= ICHP_SERVO_COUNT) return 0.0f;
    const float home = s_cfg.home_deg[servo_idx];
    const float open = s_cfg.open_deg[servo_idx];
    const float sign = (open >= home) ? 1.0f : -1.0f;
    return home + sign * logical_deg;
}
