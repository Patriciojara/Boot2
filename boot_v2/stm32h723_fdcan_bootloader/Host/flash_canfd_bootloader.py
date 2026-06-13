#!/usr/bin/env python3
"""
STM32H723 CAN-FD Custom Bootloader Flasher

Protocol:
  Host -> STM32: ID 0x100, CAN-FD+BRS
  STM32 -> Host: ID 0x101, CAN-FD+BRS
  ACK = 0x79, NACK = 0x1F

Usage:
  sudo ip link set can0 down
  sudo ip link set can0 up type can bitrate 500000 dbitrate 2000000 fd on
  python3 flash_canfd_bootloader.py firmware.bin --channel can0

Optional power cycle using Raspberry Pi GPIO17:
  python3 flash_canfd_bootloader.py firmware.bin --power-gpio 17
"""

from __future__ import annotations

import argparse
import binascii
import os
import struct
import subprocess
import sys
import time
from dataclasses import dataclass

try:
    import can
except ImportError:
    print("ERROR: falta python-can. Instala con: pip3 install python-can", file=sys.stderr)
    raise

BL_RX_ID = 0x100
BL_TX_ID = 0x101

CMD_SYNC = 0x10
CMD_INFO = 0x11
CMD_ERASE = 0x20
CMD_WRITE = 0x21
CMD_VERIFY = 0x30
CMD_RUN = 0x40

ACK = 0x79
NACK = 0x1F
BLOCK = 32
APP_ADDR = 0x08020000
APP_END = 0x08100000


@dataclass
class Ack:
    ack: int
    cmd: int
    status: int
    payload: bytes


def le32(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def u32(b: bytes) -> int:
    return struct.unpack("<I", b)[0]


def crc32(data: bytes) -> int:
    return binascii.crc32(data) & 0xFFFFFFFF


def check_vector_table(data: bytes) -> None:
    if len(data) < 8:
        raise ValueError("El binario es demasiado pequeño: no tiene vector table")

    sp, reset = struct.unpack_from("<II", data, 0)
    sp_ok = (0x20000000 <= sp < 0x20020000) or (0x24000000 <= sp < 0x24080000) or (0x30000000 <= sp < 0x30080000)
    reset_ok = (APP_ADDR <= (reset & ~1) < APP_END) and (reset & 1)

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
        "bitrate", str(bitrate), "dbitrate", str(dbitrate), "fd", "on",
        "berr-reporting", "on", "restart-ms", "100",
    ], check=True)


def power_cycle(gpio: int | None, off_s: float, on_s: float) -> None:
    if gpio is None:
        return
    print(f"Reiniciando alimentación con GPIO{gpio}...")
    try:
        from gpiozero import OutputDevice
    except ImportError:
        print("ERROR: falta gpiozero. Instala con: sudo apt install python3-gpiozero", file=sys.stderr)
        raise

    pwr = OutputDevice(gpio, active_high=True, initial_value=True)
    print("Apagando STM32...")
    pwr.off()
    time.sleep(off_s)
    print("Encendiendo STM32...")
    pwr.on()
    time.sleep(on_s)


def send(bus: can.BusABC, cmd: int, payload: bytes = b"") -> None:
    data = bytes([cmd]) + payload
    msg = can.Message(
        arbitration_id=BL_RX_ID,
        is_extended_id=False,
        is_fd=True,
        bitrate_switch=True,
        data=data,
    )
    bus.send(msg, timeout=0.2)


def recv_ack(bus: can.BusABC, cmd: int, timeout: float = 1.0) -> Ack:
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = bus.recv(timeout=max(0.0, deadline - time.time()))
        if msg is None:
            break
        if msg.arbitration_id != BL_TX_ID:
            continue
        d = bytes(msg.data)
        if len(d) < 3:
            continue
        if d[1] != cmd:
            continue
        return Ack(d[0], d[1], d[2], d[3:])
    raise TimeoutError(f"Timeout esperando ACK de cmd=0x{cmd:02X}")


def command(bus: can.BusABC, cmd: int, payload: bytes = b"", timeout: float = 1.0, retries: int = 4) -> Ack:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            send(bus, cmd, payload)
            a = recv_ack(bus, cmd, timeout=timeout)
            if a.ack == ACK:
                return a
            if a.ack == NACK:
                raise RuntimeError(f"NACK cmd=0x{cmd:02X} status=0x{a.status:02X} payload={a.payload.hex()}")
            raise RuntimeError(f"Respuesta inválida: {a}")
        except Exception as e:
            last_exc = e
            print(f"  Reintento {attempt}/{retries}: {e}")
            time.sleep(0.05)
    raise RuntimeError(f"Falló comando 0x{cmd:02X}: {last_exc}")


def wait_bootloader(bus: can.BusABC, seconds: float) -> None:
    print("Buscando bootloader...")
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            a = command(bus, CMD_SYNC, timeout=0.2, retries=1)
            print(f"Bootloader detectado. Protocolo v{a.payload[0] if a.payload else 0}")
            return
        except Exception:
            pass
        time.sleep(0.05)
    raise TimeoutError("No se detectó el bootloader. Revisa energía, FDCAN, IDs y bitrates.")


def flash(bus: can.BusABC, fw: bytes, no_run: bool, block_delay: float) -> None:
    size = len(fw)
    crc = crc32(fw)
    padded = fw + b"\xFF" * ((BLOCK - (len(fw) % BLOCK)) % BLOCK)
    blocks = len(padded) // BLOCK

    print("========================================")
    print(" STM32H723 CAN-FD Bootloader Flasher")
    print("========================================")
    print(f"Tamaño:   {size} bytes")
    print(f"CRC32:    0x{crc:08X}")
    print(f"Bloques:  {blocks} bloques de {BLOCK} bytes")
    print("========================================")

    print("Pidiendo información...")
    info = command(bus, CMD_INFO, timeout=1.0)
    if len(info.payload) >= 20:
        app_addr = u32(info.payload[0:4])
        app_max = u32(info.payload[4:8])
        block = u32(info.payload[8:12])
        rxid = u32(info.payload[12:16])
        txid = u32(info.payload[16:20])
        print(f"Info STM32: app=0x{app_addr:08X}, max={app_max}, block={block}, rx=0x{rxid:X}, tx=0x{txid:X}")

    print("Borrando área de aplicación...")
    command(bus, CMD_ERASE, le32(size) + le32(crc) + le32(0), timeout=10.0, retries=4)

    print("Programando...")
    t0 = time.time()
    for i in range(blocks):
        off = i * BLOCK
        chunk = padded[off:off + BLOCK]
        payload = le32(off) + bytes([BLOCK]) + chunk
        command(bus, CMD_WRITE, payload, timeout=0.6, retries=4)
        if (i + 1) % 50 == 0 or (i + 1) == blocks:
            pct = 100.0 * (i + 1) / blocks
            print(f"  {i + 1:5d}/{blocks} bloques ({pct:5.1f}%)")
        if block_delay > 0:
            time.sleep(block_delay)

    print("Verificando CRC...")
    ver = command(bus, CMD_VERIFY, le32(size) + le32(crc), timeout=5.0, retries=2)
    if len(ver.payload) >= 8:
        got = u32(ver.payload[0:4])
        want = u32(ver.payload[4:8])
        print(f"CRC STM32=0x{got:08X}, esperado=0x{want:08X}")

    elapsed = time.time() - t0
    print(f"OK: firmware grabado en {elapsed:.2f} s")

    if not no_run:
        print("Ejecutando aplicación...")
        command(bus, CMD_RUN, timeout=1.0, retries=1)
    else:
        print("--no-run activo: no se salta a la aplicación.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("firmware", help="Archivo .bin de la aplicación linkeada en 0x08020000")
    ap.add_argument("--channel", default="can0")
    ap.add_argument("--bitrate", type=int, default=500000)
    ap.add_argument("--dbitrate", type=int, default=2000000)
    ap.add_argument("--no-setup-can", action="store_true")
    ap.add_argument("--power-gpio", type=int, default=None, help="GPIO BCM para cortar/activar energía del STM32, ej: 17")
    ap.add_argument("--power-off-time", type=float, default=0.5)
    ap.add_argument("--power-on-wait", type=float, default=0.1)
    ap.add_argument("--boot-wait", type=float, default=3.0)
    ap.add_argument("--no-run", action="store_true")
    ap.add_argument("--block-delay", type=float, default=0.0)
    args = ap.parse_args()

    with open(args.firmware, "rb") as f:
        fw = f.read()

    check_vector_table(fw)
    setup_can(args.channel, args.bitrate, args.dbitrate, not args.no_setup_can)
    power_cycle(args.power_gpio, args.power_off_time, args.power_on_wait)

    print(f"Abriendo interfaz {args.channel}...")
    with can.interface.Bus(channel=args.channel, bustype="socketcan") as bus:
        wait_bootloader(bus, args.boot_wait)
        flash(bus, fw, no_run=args.no_run, block_delay=args.block_delay)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrumpido por usuario")
        raise SystemExit(130)
