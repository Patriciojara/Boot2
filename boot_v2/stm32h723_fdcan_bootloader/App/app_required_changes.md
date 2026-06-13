# Cambios obligatorios en la aplicación principal

El bootloader vive en `0x08000000` y ocupa 128 KiB. La aplicación debe partir en:

```c
#define VECT_TAB_OFFSET  0x00020000U
```

## 1. Linker script de la aplicación

Cambia la región FLASH de la app a:

```ld
FLASH (rx) : ORIGIN = 0x08020000, LENGTH = 896K
```

Puedes usar el archivo incluido: `STM32H723VGTX_FLASH_APP.ld`.

## 2. Vector table offset

En `system_stm32h7xx.c`, deja:

```c
#define VECT_TAB_OFFSET  0x00020000U
```

o asegúrate de que al iniciar la app quede:

```c
SCB->VTOR = 0x08020000U;
```

## 3. Archivo .bin

Compila la aplicación y genera `.bin`. Ese `.bin` debe tener vector table inicial válido para `0x08020000`.

El flasher incluido revisa el vector table antes de enviar:

- SP debe estar en RAM: `0x200xxxxx`, `0x240xxxxx` o `0x300xxxxx`.
- RESET debe apuntar dentro de `0x08020000..0x080FFFFF`.

## 4. Programación inicial

La primera vez debes cargar el bootloader por ST-LINK en `0x08000000`. Después puedes actualizar la app por CAN-FD.
