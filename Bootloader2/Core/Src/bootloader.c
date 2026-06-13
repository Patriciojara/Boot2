#include "bootloader.h"
#include "main.h"
#include <string.h>

extern FDCAN_HandleTypeDef hfdcan1;

typedef void (*pFunction)(void);

static uint32_t expected_size = 0U;
static uint32_t expected_crc  = 0U;
static uint8_t erase_done = 0U;

static uint32_t get_u32_le(const uint8_t *p)
{
    return ((uint32_t)p[0]) |
           ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static void put_u32_le(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFFU);
    p[1] = (uint8_t)((v >> 8) & 0xFFU);
    p[2] = (uint8_t)((v >> 16) & 0xFFU);
    p[3] = (uint8_t)((v >> 24) & 0xFFU);
}

static uint32_t dlc_to_len(uint32_t dlc)
{
    switch (dlc)
    {
        case FDCAN_DLC_BYTES_0:  return 0;
        case FDCAN_DLC_BYTES_1:  return 1;
        case FDCAN_DLC_BYTES_2:  return 2;
        case FDCAN_DLC_BYTES_3:  return 3;
        case FDCAN_DLC_BYTES_4:  return 4;
        case FDCAN_DLC_BYTES_5:  return 5;
        case FDCAN_DLC_BYTES_6:  return 6;
        case FDCAN_DLC_BYTES_7:  return 7;
        case FDCAN_DLC_BYTES_8:  return 8;
        case FDCAN_DLC_BYTES_12: return 12;
        case FDCAN_DLC_BYTES_16: return 16;
        case FDCAN_DLC_BYTES_20: return 20;
        case FDCAN_DLC_BYTES_24: return 24;
        case FDCAN_DLC_BYTES_32: return 32;
        case FDCAN_DLC_BYTES_48: return 48;
        case FDCAN_DLC_BYTES_64: return 64;
        default: return 0;
    }
}

static uint32_t len_to_dlc(uint32_t len)
{
    if (len <= 0U)  return FDCAN_DLC_BYTES_0;
    if (len <= 1U)  return FDCAN_DLC_BYTES_1;
    if (len <= 2U)  return FDCAN_DLC_BYTES_2;
    if (len <= 3U)  return FDCAN_DLC_BYTES_3;
    if (len <= 4U)  return FDCAN_DLC_BYTES_4;
    if (len <= 5U)  return FDCAN_DLC_BYTES_5;
    if (len <= 6U)  return FDCAN_DLC_BYTES_6;
    if (len <= 7U)  return FDCAN_DLC_BYTES_7;
    if (len <= 8U)  return FDCAN_DLC_BYTES_8;
    if (len <= 12U) return FDCAN_DLC_BYTES_12;
    if (len <= 16U) return FDCAN_DLC_BYTES_16;
    if (len <= 20U) return FDCAN_DLC_BYTES_20;
    if (len <= 24U) return FDCAN_DLC_BYTES_24;
    if (len <= 32U) return FDCAN_DLC_BYTES_32;
    if (len <= 48U) return FDCAN_DLC_BYTES_48;
    return FDCAN_DLC_BYTES_64;
}

static HAL_StatusTypeDef bl_can_send(uint8_t cmd, uint8_t ack, uint8_t status, const uint8_t *payload, uint32_t payload_len)
{
    uint8_t tx[64] = {0};
    FDCAN_TxHeaderTypeDef txh = {0};
    uint32_t len = 3U + payload_len;

    if (len > 64U) return HAL_ERROR;

    tx[0] = ack;
    tx[1] = cmd;
    tx[2] = status;
    if (payload && payload_len)
    {
        memcpy(&tx[3], payload, payload_len);
    }

    txh.Identifier = BL_TX_ID;
    txh.IdType = FDCAN_STANDARD_ID;
    txh.TxFrameType = FDCAN_DATA_FRAME;
    txh.DataLength = len_to_dlc(len);
    txh.ErrorStateIndicator = FDCAN_ESI_ACTIVE;
    txh.BitRateSwitch = FDCAN_BRS_ON;
    txh.FDFormat = FDCAN_FD_CAN;
    txh.TxEventFifoControl = FDCAN_NO_TX_EVENTS;
    txh.MessageMarker = 0;

    uint32_t t0 = HAL_GetTick();
    while (HAL_FDCAN_GetTxFifoFreeLevel(&hfdcan1) == 0U)
    {
        if ((HAL_GetTick() - t0) > 100U) return HAL_TIMEOUT;
    }

    return HAL_FDCAN_AddMessageToTxFifoQ(&hfdcan1, &txh, tx);
}

static void bl_ack(uint8_t cmd, uint8_t status, const uint8_t *payload, uint32_t payload_len)
{
    (void)bl_can_send(cmd, BL_ACK, status, payload, payload_len);
}

static void bl_nack(uint8_t cmd, uint8_t error)
{
    (void)bl_can_send(cmd, BL_NACK, error, NULL, 0);
}

bool BL_IsValidApplication(void)
{
    uint32_t app_sp = *(volatile uint32_t *)FLASH_USER_START_ADDR;
    uint32_t app_pc = *(volatile uint32_t *)(FLASH_USER_START_ADDR + 4U);

    /* Accept DTCM SRAM, AXI SRAM and SRAM1/2/3 ranges used by STM32H7 projects. */
    bool sp_ok = ((app_sp >= 0x20000000UL) && (app_sp < 0x20020000UL)) ||
                 ((app_sp >= 0x24000000UL) && (app_sp < 0x24080000UL)) ||
                 ((app_sp >= 0x30000000UL) && (app_sp < 0x30080000UL));

    bool pc_ok = (app_pc >= FLASH_USER_START_ADDR) && (app_pc < FLASH_END_ADDR) && ((app_pc & 0x1U) == 1U);

    return sp_ok && pc_ok;
}

void BL_JumpToApplication(void)
{
    uint32_t app_sp = *(volatile uint32_t *)FLASH_USER_START_ADDR;
    uint32_t app_pc = *(volatile uint32_t *)(FLASH_USER_START_ADDR + 4U);
    pFunction app_reset_handler = (pFunction)app_pc;

    HAL_FDCAN_DeInit(&hfdcan1);
    HAL_DeInit();

    __disable_irq();

    SysTick->CTRL = 0;
    SysTick->LOAD = 0;
    SysTick->VAL  = 0;

    for (uint32_t i = 0; i < 8U; i++)
    {
        NVIC->ICER[i] = 0xFFFFFFFFU;
        NVIC->ICPR[i] = 0xFFFFFFFFU;
    }

    SCB->VTOR = FLASH_USER_START_ADDR;
    __set_MSP(app_sp);
    __DSB();
    __ISB();

    app_reset_handler();
}

uint32_t BL_Crc32(const uint8_t *data, uint32_t len, uint32_t crc_init)
{
    uint32_t crc = crc_init;
    for (uint32_t i = 0; i < len; i++)
    {
        crc ^= data[i];
        for (uint32_t bit = 0; bit < 8U; bit++)
        {
            if (crc & 1U)
                crc = (crc >> 1) ^ 0xEDB88320UL;
            else
                crc >>= 1;
        }
    }
    return crc;
}

uint32_t BL_Crc32Flash(uint32_t addr, uint32_t len)
{
    uint32_t crc = 0xFFFFFFFFUL;
    const uint8_t *p = (const uint8_t *)addr;
    crc = BL_Crc32(p, len, crc);
    return ~crc;
}

static uint8_t bl_erase_app(void)
{
    HAL_FLASH_Unlock();
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_ALL_ERRORS_BANK1);

    FLASH_EraseInitTypeDef erase = {0};
    uint32_t sector_error = 0U;

    erase.TypeErase = FLASH_TYPEERASE_SECTORS;
    erase.Banks = FLASH_BANK_1;
    erase.Sector = FLASH_APP_FIRST_SECTOR;
    erase.NbSectors = FLASH_APP_NB_SECTORS;
    erase.VoltageRange = FLASH_VOLTAGE_RANGE_3;

    if (HAL_FLASHEx_Erase(&erase, &sector_error) != HAL_OK)
    {
        HAL_FLASH_Lock();
        return BL_ERR_FLASH_ERASE;
    }

    HAL_FLASH_Lock();
    erase_done = 1U;
    return BL_STATUS_OK;
}

static uint8_t bl_write_block(uint32_t offset, const uint8_t *src, uint32_t len)
{
    if (!erase_done) return BL_ERR_SEQUENCE;
    if (len == 0U || len > BL_BLOCK_SIZE) return BL_ERR_BAD_LEN;
    if ((offset % BL_BLOCK_SIZE) != 0U) return BL_ERR_BAD_ADDR;
    if ((FLASH_USER_START_ADDR + offset) < FLASH_USER_START_ADDR) return BL_ERR_BAD_ADDR;
    if ((offset + len) > FLASH_APP_MAX_SIZE) return BL_ERR_BAD_ADDR;

    uint32_t dst = FLASH_USER_START_ADDR + offset;
    if ((dst % BL_BLOCK_SIZE) != 0U) return BL_ERR_BAD_ADDR;

    __attribute__((aligned(32))) uint32_t flashword[8];
    memset(flashword, 0xFF, sizeof(flashword));
    memcpy((uint8_t *)flashword, src, len);

    HAL_FLASH_Unlock();
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_ALL_ERRORS_BANK1);

    if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_FLASHWORD, dst, (uint32_t)(uintptr_t)flashword) != HAL_OK)
    {
        HAL_FLASH_Lock();
        return BL_ERR_FLASH_WRITE;
    }

    HAL_FLASH_Lock();

    if (memcmp((const void *)dst, flashword, BL_BLOCK_SIZE) != 0)
    {
        return BL_ERR_FLASH_WRITE;
    }

    return BL_STATUS_OK;
}

static void handle_info(void)
{
    uint8_t p[20] = {0};
    put_u32_le(&p[0], FLASH_USER_START_ADDR);
    put_u32_le(&p[4], FLASH_APP_MAX_SIZE);
    put_u32_le(&p[8], BL_BLOCK_SIZE);
    put_u32_le(&p[12], BL_RX_ID);
    put_u32_le(&p[16], BL_TX_ID);
    bl_ack(BL_CMD_INFO, BL_STATUS_OK, p, sizeof(p));
}

static void handle_frame(const uint8_t *rx, uint32_t len)
{
    if (len == 0U) return;
    uint8_t cmd = rx[0];

    switch (cmd)
    {
        case BL_CMD_SYNC:
        {
            uint8_t p[1] = { BL_PROTOCOL_VERSION };
            bl_ack(cmd, BL_STATUS_OK, p, 1);
            break;
        }

        case BL_CMD_INFO:
        {
            handle_info();
            break;
        }

        case BL_CMD_ERASE_APP:
        {
            if (len < 13U)
            {
                bl_nack(cmd, BL_ERR_BAD_LEN);
                break;
            }
            expected_size = get_u32_le(&rx[1]);
            expected_crc  = get_u32_le(&rx[5]);
            if (expected_size == 0U || expected_size > FLASH_APP_MAX_SIZE)
            {
                bl_nack(cmd, BL_ERR_BAD_ADDR);
                break;
            }

            uint8_t st = bl_erase_app();
            if (st == BL_STATUS_OK)
            {
                uint8_t p[8];
                put_u32_le(&p[0], expected_size);
                put_u32_le(&p[4], expected_crc);
                bl_ack(cmd, BL_STATUS_OK, p, sizeof(p));
            }
            else
            {
                bl_nack(cmd, st);
            }
            break;
        }

        case BL_CMD_WRITE_BLOCK:
        {
            if (len < 6U)
            {
                bl_nack(cmd, BL_ERR_BAD_LEN);
                break;
            }
            uint32_t offset = get_u32_le(&rx[1]);
            uint32_t n = rx[5];
            if (n == 0U || n > BL_BLOCK_SIZE || len < (6U + n))
            {
                bl_nack(cmd, BL_ERR_BAD_LEN);
                break;
            }

            uint8_t st = bl_write_block(offset, &rx[6], n);
            if (st == BL_STATUS_OK)
            {
                uint8_t p[4];
                put_u32_le(p, offset);
                bl_ack(cmd, BL_STATUS_OK, p, sizeof(p));
            }
            else
            {
                bl_nack(cmd, st);
            }
            break;
        }

        case BL_CMD_VERIFY_CRC:
        {
            uint32_t size = expected_size;
            uint32_t want = expected_crc;
            if (len >= 9U)
            {
                size = get_u32_le(&rx[1]);
                want = get_u32_le(&rx[5]);
            }
            if (size == 0U || size > FLASH_APP_MAX_SIZE)
            {
                bl_nack(cmd, BL_ERR_BAD_ADDR);
                break;
            }
            uint32_t got = BL_Crc32Flash(FLASH_USER_START_ADDR, size);
            uint8_t p[8];
            put_u32_le(&p[0], got);
            put_u32_le(&p[4], want);
            if (got == want)
                bl_ack(cmd, BL_STATUS_OK, p, sizeof(p));
            else
                bl_can_send(cmd, BL_NACK, BL_ERR_BAD_CRC, p, sizeof(p));
            break;
        }

        case BL_CMD_RUN_APP:
        {
            if (!BL_IsValidApplication())
            {
                bl_nack(cmd, BL_ERR_BAD_APP);
                break;
            }
            bl_ack(cmd, BL_STATUS_OK, NULL, 0);
            HAL_Delay(50);
            BL_JumpToApplication();
            break;
        }

        default:
            bl_nack(cmd, BL_ERR_UNKNOWN_CMD);
            break;
    }
}

void BL_Loop(void)
{
    FDCAN_RxHeaderTypeDef rxh = {0};
    uint8_t rx[64];

    while (1)
    {
        if (HAL_FDCAN_GetRxFifoFillLevel(&hfdcan1, FDCAN_RX_FIFO0) > 0U)
        {
            memset(rx, 0, sizeof(rx));
            if (HAL_FDCAN_GetRxMessage(&hfdcan1, FDCAN_RX_FIFO0, &rxh, rx) == HAL_OK)
            {
                uint32_t len = dlc_to_len(rxh.DataLength);
                if ((rxh.IdType == FDCAN_STANDARD_ID) &&
                    (rxh.Identifier == BL_RX_ID) &&
                    (rxh.RxFrameType == FDCAN_DATA_FRAME) &&
                    (rxh.FDFormat == FDCAN_FD_CAN))
                {
                    handle_frame(rx, len);
                }
            }
        }
    }
}
