# Bootloader CAN-FD para STM32H723VGT6TR

Paquete base para un bootloader propio por FDCAN1 para la placa `STM32-salidas`.

## Resumen de hardware detectado en el esquemático

- MCU: `STM32H723VGT6TR`.
- Transceiver CAN-FD: `MCP2562FDT-E/MF` (`IC9`).
- FDCAN1:
  - `PA11` = `FDCAN1_RX` = net `CAN RXD`.
  - `PA12` = `FDCAN1_TX` = net `CAN TXD`.
- Bus externo: `CAN_H` y `CAN_L`.
- Net `BOOT_STM` conectado al pin dedicado `BOOT0`.

> Nota importante sobre BOOT0: este bootloader es una aplicación ubicada en Flash desde `0x08000000`. Si BOOT0 arranca el ROM bootloader interno, no se ejecutará este código. Por eso este bootloader entra siempre primero, espera una ventana corta por FDCAN y luego salta a la app. Para forzar actualización sin tocar BOOT0, reinicia la placa y envía SYNC durante esa ventana. El script puede cortar/activar alimentación con GPIO17 de Raspberry Pi.

## Mapa de memoria

| Zona | Dirección | Tamaño | Uso |
|---|---:|---:|---|
| Bootloader | `0x08000000` | 128 KiB | Sector 0 |
| Aplicación | `0x08020000` | 896 KiB | Sectores 1 a 7 |

## Parámetros CAN-FD

- Interfaz STM32: `FDCAN1`.
- ID host -> STM32: `0x100`.
- ID STM32 -> host: `0x101`.
- Frame: CAN-FD con BRS.
- Arbitration bitrate: `500000`.
- Data bitrate: `2000000`.
- FDCAN kernel clock esperado: `80 MHz`.

Timings usados en STM32:

```c
NominalPrescaler = 10;
NominalTimeSeg1 = 13;
NominalTimeSeg2 = 2;
NominalSyncJumpWidth = 2;

DataPrescaler = 4;
DataTimeSeg1 = 7;
DataTimeSeg2 = 2;
DataSyncJumpWidth = 2;
```

## Protocolo implementado

| Comando | Byte | Descripción |
|---|---:|---|
| SYNC | `0x10` | Detecta bootloader |
| INFO | `0x11` | Devuelve dirección app, tamaño, bloque, IDs |
| ERASE_APP | `0x20` | Borra área de aplicación |
| WRITE_BLOCK | `0x21` | Escribe bloque de 32 bytes |
| VERIFY_CRC | `0x30` | Verifica CRC32 del binario |
| RUN_APP | `0x40` | Salta a la aplicación |

Respuesta:

- ACK: `0x79`.
- NACK: `0x1F`.

## Archivos incluidos

```text
Bootloader/
  Core/Inc/
    bootloader_config.h
    bootloader.h
    main.h
  Core/Src/
    bootloader.c
    main.c
  STM32H723VGTX_FLASH_BL.ld

App/
  STM32H723VGTX_FLASH_APP.ld
  app_required_changes.md

Host/
  flash_canfd_bootloader.py
```

## Cómo usar en STM32CubeIDE

### 1. Crear proyecto bootloader

1. Crear proyecto nuevo para `STM32H723VGT6TR`.
2. Activar `FDCAN1` en `PA11/PA12`.
3. Configurar HSE como cristal de 8 MHz.
4. Asegurar FDCAN kernel clock = 80 MHz.
5. Copiar estos archivos al proyecto:
   - `Bootloader/Core/Src/main.c`
   - `Bootloader/Core/Src/bootloader.c`
   - `Bootloader/Core/Inc/main.h`
   - `Bootloader/Core/Inc/bootloader.h`
   - `Bootloader/Core/Inc/bootloader_config.h`
6. Cambiar linker script por `Bootloader/STM32H723VGTX_FLASH_BL.ld`.
7. Compilar y grabar por ST-LINK en `0x08000000`.

### 2. Preparar la aplicación principal

En la aplicación normal, cambiar:

```ld
FLASH (rx) : ORIGIN = 0x08020000, LENGTH = 896K
```

Y dejar el vector table offset:

```c
#define VECT_TAB_OFFSET  0x00020000U
```

o asegurar al inicio:

```c
SCB->VTOR = 0x08020000U;
```

### 3. Generar binario de aplicación

En CubeIDE, activar conversión a `.bin`, o usar:

```bash
arm-none-eabi-objcopy -O binary App.elf App.bin
```

### 4. Instalar dependencias en Raspberry Pi

```bash
sudo apt update
sudo apt install -y can-utils python3-pip python3-gpiozero
pip3 install python-can
```

### 5. Flashear por CAN-FD

Sin control de energía:

```bash
sudo ip link set can0 down
sudo ip link set can0 up type can bitrate 500000 dbitrate 2000000 fd on berr-reporting on restart-ms 100
python3 Host/flash_canfd_bootloader.py App.bin --channel can0
```

Con control de energía por GPIO17:

```bash
python3 Host/flash_canfd_bootloader.py App.bin --channel can0 --power-gpio 17
```

Para grabar y no ejecutar inmediatamente:

```bash
python3 Host/flash_canfd_bootloader.py App.bin --channel can0 --power-gpio 17 --no-run
```

## Prueba rápida con can-utils

Configurar CAN-FD:

```bash
sudo ip link set can0 down
sudo ip link set can0 up type can bitrate 500000 dbitrate 2000000 fd on berr-reporting on restart-ms 100
```

Escuchar:

```bash
candump -tz -x can0,100:7FF,101:7FF
```

Enviar SYNC:

```bash
cansend can0 100##110
```

Respuesta esperada:

```text
101  [..]  79 10 00 01 ...
```

## Si no responde

Revisar:

1. `can0` está en modo FD y BRS.
2. Bitrate nominal/data: `500k/2M`.
3. FDCAN kernel clock del STM32 realmente es `80 MHz`.
4. PA11/PA12 están en AF9 FDCAN1.
5. El transceiver MCP2562 está alimentado y no está en standby.
6. Hay terminación de 120 ohm en el bus CAN si corresponde.
7. La app está linkeada en `0x08020000`, no en `0x08000000`.
8. Si presionas BOOT0 al reset, puedes estar entrando al ROM bootloader, no a este bootloader.
