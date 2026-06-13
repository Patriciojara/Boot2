#ifndef BOOTLOADER_CONFIG_H
#define BOOTLOADER_CONFIG_H

#include "stm32h7xx_hal.h"

/*
 * Custom FDCAN bootloader for STM32H723VGT6TR
 * Board checked from STM32-salidas.kicad_sch:
 *   FDCAN1_RX = PA11  -> net CAN RXD -> MCP2562 RXD
 *   FDCAN1_TX = PA12  -> net CAN TXD -> MCP2562 TXD
 *   Transceiver = MCP2562FDT-E/MF
 *   BOOT_STM net goes to BOOT0 pin.
 */

#define BL_RX_ID                 0x100U
#define BL_TX_ID                 0x101U

#define BL_PROTOCOL_VERSION      0x01U
#define BL_BLOCK_SIZE            32U
#define BL_BOOT_WAIT_MS          1500U

#define FLASH_USER_START_ADDR    0x08020000UL  /* Application starts after 128 KiB bootloader */
#define FLASH_BASE_ADDR          0x08000000UL
#define FLASH_TOTAL_SIZE_BYTES   (1024UL * 1024UL) /* STM32H723VGT6TR = 1 MiB flash */
#define FLASH_END_ADDR           (FLASH_BASE_ADDR + FLASH_TOTAL_SIZE_BYTES)
#define FLASH_APP_MAX_SIZE       (FLASH_END_ADDR - FLASH_USER_START_ADDR)

/* STM32H723 1 MiB flash, single bank: 8 sectors of 128 KiB.
 * Bootloader uses sector 0: 0x08000000..0x0801FFFF.
 * Application uses sectors 1..7: 0x08020000..0x080FFFFF.
 */
#define FLASH_APP_FIRST_SECTOR   FLASH_SECTOR_1
#define FLASH_APP_NB_SECTORS     7U

/* Commands */
#define BL_CMD_SYNC              0x10U
#define BL_CMD_INFO              0x11U
#define BL_CMD_ERASE_APP         0x20U
#define BL_CMD_WRITE_BLOCK       0x21U
#define BL_CMD_VERIFY_CRC        0x30U
#define BL_CMD_RUN_APP           0x40U

/* Replies */
#define BL_ACK                   0x79U
#define BL_NACK                  0x1FU

/* Error codes returned in ACK/NACK frame byte 2 */
#define BL_STATUS_OK             0x00U
#define BL_ERR_BAD_LEN           0x01U
#define BL_ERR_BAD_ADDR          0x02U
#define BL_ERR_FLASH_ERASE       0x03U
#define BL_ERR_FLASH_WRITE       0x04U
#define BL_ERR_BAD_CRC           0x05U
#define BL_ERR_BAD_APP           0x06U
#define BL_ERR_SEQUENCE          0x07U
#define BL_ERR_UNKNOWN_CMD       0x08U

#endif /* BOOTLOADER_CONFIG_H */
