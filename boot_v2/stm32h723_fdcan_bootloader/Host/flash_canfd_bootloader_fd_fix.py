#!/usr/bin/env python3
"""
STM32H723 CAN-FD Custom Bootloader Flasher

Protocol:
  Host -> STM32: ID 0x100, CAN-FD + BRS
  STM32 -> Host: ID 0x101, CAN-FD + BRS
  ACK  = 0x79
  NACK = 0x1F

Uso típico:
  sudo ip link set can0 down
  sudo ip link set can0 up type can bitrate 500000 dbitrate 2000000 fd on berr-reporting on restart-ms 100
  python3 flash_canfd_bootloader.py Codigo-STM32-SUCHAI4.bin --channel can0 --power-gpio 17 --no-setup-can
"""

from __future__ import annotations

import argparse
import binascii
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

try:
    import can
except ImportError as exc:
    print("ERROR: falta python-can. Instala con: sudo apt install python3-can", file=sys.stderr)
    raise exc


BL_RX_ID = 0x100
BL_TX_ID = 0x101

CMD_SYNC   = 0x10
CMD_INFO   = 0x11
CMD_ERASE  = 0x20
CMD_WRITE  = 0x21
CMD_VERIFY = 0x30
CMD_RUN    = 0x40

ACK  = 0x79
NACK = 0x1F

BLOCK = 32
APP_ADDR = 0x08020000
APP_END  = 0x08100000


@dataclass
class AckPacket:
    ack: int
    cmd: int
    status: int
    payload: bytes


def le32(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def u32(data: bytes) -> int:
    return struct.unpack("<I", data)[0]


def crc32(data: bytes) -> int:
    return binascii.crc32(data) & 0xFFFFFFFF


def check_vector_table(data: bytes) -> None:
    if len(data) < 8:
        raise ValueError("El binario es demasiado pequeño: no tiene vector table")

    sp, reset = struct.unpack_from("<II", data, 0)

    sp_ok = (
        (0x20000000 <= sp < 0x20020000) or
        (0x24000000 <= sp < 0x24080000) or
        (0x30000000 <= sp < 0x30080000) or
        (0x38000000 <= sp < 0x38010000)
    )

    reset_ok = (APP_ADDR <= (reset & ~1) < APP_END) and bool(reset & 1)

    print(f"Vector table: SP=0x{sp:08X}, RESET=0x{reset:08X}")

    if not sp_ok:
        raise ValueError("SP inválido. ¿La app fue compilada para STM32H7 y RAM correcta?")

    if not reset_ok:
        raise ValueError("RESET inválido. La app debe estar linkeada en 0x08020000")

    print("Vector table OK para aplicación en 0x08020000.")


def setup_can(channel: str, bitrate: int, dbitrate: int, do_setup: bool) -> None:
    if not do_setup:
        return

    print(f"Configurando {channel} en CAN-FD {bitrate} / {dbitrate}...")

    subprocess.run(["sudo", "ip", "link", "set", channel, "down"], check=False)
    subprocess.run([
        "sudo", "ip", "link", "set", channel, "up", "type", "can",
        "bitrate", str(bitrate),
        "dbitrate", str(dbitrate),
        "fd", "on",
        "berr-reporting", "on",
        "restart-ms", "100",
    ], check=True)


def power_cycle(gpio: Optional[int], off_s: float, on_s: float) -> None:
    if gpio is None:
        return

    print(f"Reiniciando alimentación con GPIO{gpio}...")

    try:
        from gpiozero import OutputDevice
    except ImportError as exc:
        print("ERROR: falta gpiozero. Instala con: sudo apt install python3-gpiozero", file=sys.stderr)
        raise exc

    pwr = OutputDevice(gpio, active_high=True, initial_value=True)

    print("Apagando STM32...")
    pwr.off()
    time.sleep(off_s)

    print("Encendiendo STM32...")
    pwr.on()
    time.sleep(on_s)

    pwr.close()


def power_cycle_active_low(gpio: Optional[int], off_s: float, on_s: float) -> None:
    if gpio is None:
        return

    print(f"Reiniciando alimentación con GPIO{gpio} en modo active_low...")

    try:
        from gpiozero import OutputDevice
    except ImportError as exc:
        print("ERROR: falta gpiozero. Instala con: sudo apt install python3-gpiozero", file=sys.stderr)
        raise exc

    pwr = OutputDevice(gpio, active_high=False, initial_value=True)

    print("Apagando STM32...")
    pwr.off()
    time.sleep(off_s)

    print("Encendiendo STM32...")
    pwr.on()
    time.sleep(on_s)

    pwr.close()


def open_can_bus(channel: str):
    try:
        return can.Bus(interface="socketcan", channel=channel, fd=True)
    except TypeError:
        return can.interface.Bus(channel=channel, interface="socketcan", fd=True)


def send_frame(bus, cmd: int, payload: bytes = b"") -> None:
    data = bytes([cmd]) + payload

    if len(data) > 64:
        raise ValueError(f"CAN-FD permite máximo 64 bytes. Intentaste enviar {len(data)} bytes")

    msg = can.Message(
        arbitration_id=BL_RX_ID,
        is_extended_id=False,
        is_fd=True,
        bitrate_switch=True,
        data=data,
    )

    bus.send(msg, timeout=1.0)


def recv_ack(bus, cmd: int, timeout: float = 1.0) -> AckPacket:
    deadline = time.time() + timeout

    while time.time() < deadline:
        remaining = max(0.0, deadline - time.time())
        msg = bus.recv(timeout=remaining)

        if msg is None:
            break

        if msg.arbitration_id != BL_TX_ID:
            continue

        data = bytes(msg.data)

        if len(data) < 3:
            continue

        if data[1] != cmd:
            continue

        return AckPacket(
            ack=data[0],
            cmd=data[1],
            status=data[2],
            payload=data[3:],
        )

    raise TimeoutError(f"Timeout esperando ACK de cmd=0x{cmd:02X}")


def command(bus, cmd: int, payload: bytes = b"", timeout: float = 1.0, retries: int = 4) -> AckPacket:
    last_exc: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            send_frame(bus, cmd, payload)
            ack = recv_ack(bus, cmd, timeout=timeout)

            if ack.ack == ACK:
                return ack

            if ack.ack == NACK:
                raise RuntimeError(
                    f"NACK cmd=0x{cmd:02X}, status=0x{ack.status:02X}, payload={ack.payload.hex()}"
                )

            raise RuntimeError(
                f"Respuesta inválida cmd=0x{cmd:02X}: ack=0x{ack.ack:02X}, status=0x{ack.status:02X}"
            )

        except Exception as exc:
            last_exc = exc
            print(f"  Reintento {attempt}/{retries}: {exc}")
            time.sleep(0.05)

    raise RuntimeError(f"Falló comando 0x{cmd:02X}: {last_exc}")


def wait_bootloader(bus, seconds: float) -> None:
    print("Buscando bootloader...")

    deadline = time.time() + seconds

    while time.time() < deadline:
        try:
            ack = command(bus, CMD_SYNC, timeout=0.2, retries=1)
            version = ack.payload[0] if len(ack.payload) >= 1 else 0
            print(f"Bootloader detectado. Protocolo v{version}")
            return
        except Exception as exc:
            print(f"  SYNC sin respuesta: {exc}")
            time.sleep(0.05)

    raise TimeoutError("No se detectó el bootloader. Revisa energía, FDCAN, IDs y bitrates.")


def flash_firmware(bus, firmware: bytes, no_run: bool, block_delay: float) -> None:
    size = len(firmware)
    fw_crc = crc32(firmware)

    padded = firmware + b"\xFF" * ((BLOCK - (len(firmware) % BLOCK)) % BLOCK)
    blocks = len(padded) // BLOCK

    print("========================================")
    print(" STM32H723 CAN-FD Bootloader Flasher")
    print("========================================")
    print(f"Tamaño:   {size} bytes")
    print(f"CRC32:    0x{fw_crc:08X}")
    print(f"Bloques:  {blocks} bloques de {BLOCK} bytes")
    print("========================================")

    print("Pidiendo información...")
    info = command(bus, CMD_INFO, timeout=1.0, retries=4)

    if len(info.payload) >= 20:
        app_addr = u32(info.payload[0:4])
        app_max  = u32(info.payload[4:8])
        block    = u32(info.payload[8:12])
        rxid     = u32(info.payload[12:16])
        txid     = u32(info.payload[16:20])
        print(f"Info STM32: app=0x{app_addr:08X}, max={app_max}, block={block}, rx=0x{rxid:X}, tx=0x{txid:X}")

    print("Borrando área de aplicación...")
    command(bus, CMD_ERASE, le32(size) + le32(fw_crc) + le32(0), timeout=10.0, retries=4)

    print("Programando...")
    t0 = time.time()

    for i in range(blocks):
        offset = i * BLOCK
        chunk = padded[offset:offset + BLOCK]

        payload = le32(offset) + bytes([BLOCK]) + chunk
        command(bus, CMD_WRITE, payload, timeout=0.6, retries=4)

        if (i + 1) % 50 == 0 or (i + 1) == blocks:
            pct = 100.0 * (i + 1) / blocks
            print(f"  {i + 1:5d}/{blocks} bloques ({pct:5.1f}%)")

        if block_delay > 0:
            time.sleep(block_delay)

    print("Verificando CRC...")
    verify = command(bus, CMD_VERIFY, le32(size) + le32(fw_crc), timeout=5.0, retries=2)

    if len(verify.payload) >= 8:
        got = u32(verify.payload[0:4])
        expected = u32(verify.payload[4:8])
        print(f"CRC STM32=0x{got:08X}, esperado=0x{expected:08X}")

    elapsed = time.time() - t0
    print(f"OK: firmware grabado en {elapsed:.2f} s")

    if no_run:
        print("--no-run activo: no se salta a la aplicación.")
        return

    print("Ejecutando aplicación...")
    try:
        command(bus, CMD_RUN, timeout=1.0, retries=1)
    except Exception as exc:
        print(f"Aviso: no se recibió ACK final de RUN o el bootloader ya saltó a la app: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Flasher STM32H723 por CAN-FD")

    parser.add_argument("firmware", help="Archivo .bin de la aplicación linkeada en 0x08020000")
    parser.add_argument("--channel", default="can0", help="Interfaz SocketCAN, por defecto can0")
    parser.add_argument("--bitrate", type=int, default=500000, help="Bitrate nominal CAN")
    parser.add_argument("--dbitrate", type=int, default=2000000, help="Bitrate data CAN-FD")
    parser.add_argument("--no-setup-can", action="store_true", help="No configurar can0; usar la configuración actual")

    parser.add_argument("--power-gpio", type=int, default=None, help="GPIO BCM para cortar/activar energía del STM32, ejemplo: 17")
    parser.add_argument("--power-active-low", action="store_true", help="Usar si tu enable de alimentación funciona invertido")
    parser.add_argument("--power-off-time", type=float, default=0.5, help="Tiempo apagado al hacer power-cycle")
    parser.add_argument("--power-on-wait", type=float, default=0.15, help="Espera después de encender antes de abrir CAN")

    parser.add_argument("--boot-wait", type=float, default=3.0, help="Segundos buscando el bootloader")
    parser.add_argument("--no-run", action="store_true", help="No ejecutar la app al terminar")
    parser.add_argument("--block-delay", type=float, default=0.0, help="Pausa entre bloques, ej: 0.002")

    args = parser.parse_args()

    with open(args.firmware, "rb") as f:
        firmware = f.read()

    check_vector_table(firmware)

    setup_can(
        channel=args.channel,
        bitrate=args.bitrate,
        dbitrate=args.dbitrate,
        do_setup=not args.no_setup_can,
    )

    if args.power_active_low:
        power_cycle_active_low(args.power_gpio, args.power_off_time, args.power_on_wait)
    else:
        power_cycle(args.power_gpio, args.power_off_time, args.power_on_wait)

    print(f"Abriendo interfaz {args.channel} en CAN-FD...")
    bus = open_can_bus(args.channel)

    try:
        wait_bootloader(bus, args.boot_wait)
        flash_firmware(bus, firmware, no_run=args.no_run, block_delay=args.block_delay)
    finally:
        try:
            bus.shutdown()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrumpido por usuario")
        raise SystemExit(130)