"""
Phase 0: find the vLinker FD+ over BLE, map its GATT table, and run the
ELM327 AT handshake (ATZ / ATE0 / ATSP6) over whatever write+notify
characteristic pair it exposes.

This is the de-risking step: it only needs the dongle plugged into the car's
OBD2 port with the ignition in "Key On, Engine Off" — the car doesn't need to
be in Ready/driving state.

Usage:
    python3 -m fiskeretta.scan
"""

import asyncio
import sys

from . import core


def pick_device(pairs: list):
    hinted = core.best_guess(pairs)
    if hinted:
        print(f"\nAuto-selecting likely match: {hinted.name} ({hinted.address})")
        return hinted

    if not sys.stdin.isatty():
        return pairs[0][0] if pairs else None

    for i, (d, _adv) in enumerate(pairs):
        print(f"  [{i}] {d.name or '(unnamed)'}  {d.address}")
    raw = input("\nEnter the index of the vLinker device: ").strip()
    try:
        return pairs[int(raw)][0]
    except (ValueError, IndexError):
        print("Invalid index.")
        return None


async def main() -> None:
    pairs = await core.discover(log=print)
    if not pairs:
        return

    device = pick_device(pairs)
    if device is None:
        return

    await core.connect_and_handshake(device, log=print)


if __name__ == "__main__":
    asyncio.run(main())
