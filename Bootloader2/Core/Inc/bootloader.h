#ifndef BOOTLOADER_H
#define BOOTLOADER_H

#include "bootloader_config.h"
#include <stdint.h>
#include <stdbool.h>

bool     BL_IsValidApplication(void);
void     BL_JumpToApplication(void);
void     BL_Loop(void);
uint32_t BL_Crc32(const uint8_t *data, uint32_t len, uint32_t crc_init);
uint32_t BL_Crc32Flash(uint32_t addr, uint32_t len);

#endif /* BOOTLOADER_H */
