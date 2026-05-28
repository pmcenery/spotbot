# SpotBot BLE / Bosch TDL110 BLE PIN Finder

A Python script for finding the PIN on the Bosch SpotBot BLE / TDL110 Bluetooth Low Energy transport logger.

## Why

I bought a couple of these devices cheaply off eBay for a small project. After doing some research, it became clear that previously owned or job-lot sales regularly result in people spending money on unusable devices. Despite the manual claiming that not even the factory can reset them, I thought I'd give it a try. I bought two — and was locked out of both.

## Protocol

Derived from Wireshark capture:

| Direction | Handle | Characteristic UUID | Payload |
|-----------|--------|---------------------|---------|
| Write | `0x0037` | `...85e` | PIN as raw bytes — `1234` → `01 02 03 04` |
| Notify | `0x001e` | `...851` | Response code (see below) |

**Notification response codes:**

| Byte | Meaning |
|------|---------|
| `0x19` | PIN accepted |
| `0x16` | Wrong PIN — retry immediately |
| `0x1d` | Wrong PIN — wait 5 seconds before retrying |
| `0x1e` | Wrong PIN — wait 60 seconds before retrying |

There is no documentation about this protocol. The device tells you how long to wait via the response code — the script respects these delays automatically (5 seconds for `0x1d`, 60 seconds for `0x1e`). Worst case, you wait 7.2 days to find your PIN. That will put a dent in the battery, but at least you get to use your device.

## Requirements

```
pip install bleak
```

Or with conda:

```
conda env create -f environment.yml
```

## Usage

### 1. Find the device address

```
python td1110_pin.py --scan
```

Scans for 5 seconds and lists nearby BLE devices with their addresses and signal strength. Use the address from the output in subsequent commands.

### 2. Test a single PIN

```
python td1110_pin.py --address <UUID> --pin 1234
```

Connects, sends the PIN, and reports the result. Exit code `0` = accepted, `1` = rejected.

**Example output:**

```
  Connected.
  Writing PIN '1234' → 01 02 03 04
  Response: 1e  →  ❌ Rejected
```

### 3. Brute-force a range

```
python td1110_pin.py --address <UUID> --brute --start 0 --end 9999
```

Tries every PIN in the range over a single persistent BLE connection. A 62-second delay is enforced between each attempt. At 10,000 combinations this takes approximately 7 days to complete.

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--start` | `0` | First PIN to try |
| `--end` | `9999` | Last PIN to try (inclusive) |
| `--digits` | `4` | Zero-pad width (e.g. `4` → `0034`) |

**Example output:**

```
Starting brute-force: 0000 → 9999  (10000 combinations)

  Connected.

[34/10000] Trying PIN: 0033
  Response: 1e  →  ❌ Rejected
[35/10000] Trying PIN: 0034
  Response: 19  →  ✅ ACCEPTED

🎉  PIN FOUND: 0034
```

Exit code `0` if the PIN is found, `1` if the range is exhausted without a match.

## Notes

- On macOS, CoreBluetooth manages the BLE connection implicitly — no explicit pairing step is needed.
- The brute-forcer reuses a single connection across all attempts rather than reconnecting per PIN.
- The device responds with `0x1e` for an invalid PIN and any other byte value for a valid one.
