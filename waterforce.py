#!/usr/bin/env python3
"""
waterforce.py - Linux fan control for Gigabyte AORUS Waterforce 240/280/360 non-X.

Target device: USB 1044:7a51 ("GIGABYTE CPU Cooler"). Protocol: PROTOCOL.md.

The cooler runs its own fan/pump curve; its input is the CPU temperature the
host sends once per second (on Windows: AorusLcdService). Without that feed it
falls back to a fixed ~1000 RPM fan within a few seconds. `daemon` does what
the Windows service does.

Transport: HID feature reports on interface 2 via pyusb control transfers
(usbhid rejects that interface's descriptor, so there is no hidraw for it).
Needs root or the udev rule next to this file.

Usage:
    python3 waterforce.py status       # fan/pump RPM + modes (read-only)
    python3 waterforce.py daemon       # feed CPU temp once per second
    python3 waterforce.py selftest     # offline checks, no device needed
"""

import os
import sys
import glob
import time
import errno
import argparse

import usb.core
import usb.util

VID, PID, IFACE = 0x1044, 0x7A51, 2
CMD_RPM, CMD_MODE, CMD_CPU_TEMP = 0xD8, 0xDD, 0xE0
MODES = dict(enumerate(["Balanced", "Customized", "Default", "FixedRPM",
                        "Turbo", "Performance", "Quiet", "ZeroRPM"]))
INTERVAL_S = 1  # same as the Windows service
ALPHA = 0.2     # EMA weight per 1 s sample (calibration knob: higher = faster but noisier fans)
PANIC_C = 90    # at/above this the raw CPU temp is sent unsmoothed


# ---------------------------------------------------------------------------
# packets (pure, covered by selftest)
# ---------------------------------------------------------------------------
def packet(*payload):
    """8-byte feature report payload: command, args, zero padding."""
    return bytes(payload).ljust(8, b"\0")


def temp_packet(temp_c):
    return packet(CMD_CPU_TEMP, max(0, min(100, round(temp_c))))


def decode(cmd, r):
    """Check the echoed command, then unpack a D8 (fan, pump RPM) or DD (fan, pump mode) reply."""
    if r[0] != cmd:
        raise ValueError("reply %s does not echo command %02x" % (r.hex(" "), cmd))
    if cmd == CMD_RPM:
        return r[1] << 8 | r[2], r[3] << 8 | r[4]
    return r[1], r[2]


def smooth(ema, raw):
    """EMA against 1 s package-temp spikes; at PANIC_C and above the raw value goes out."""
    ema = raw if ema is None else ema + ALPHA * (raw - ema)
    return ema, (raw if raw >= PANIC_C else ema)


# ---------------------------------------------------------------------------
# USB transport (interface 2, EP0)
# ---------------------------------------------------------------------------
def open_cooler():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise usb.core.USBError("cooler %04x:%04x not found" % (VID, PID))
    return dev


def xfer(dev, payload, reply=False):
    """SET_REPORT feature (ID 0) on interface 2, optionally GET_REPORT the 8-byte reply."""
    for attempt in range(5):
        try:
            dev.ctrl_transfer(0x21, 0x09, 0x0300, IFACE, payload)
            if not reply:
                return None
            time.sleep(0.02)
            return bytes(dev.ctrl_transfer(0xA1, 0x01, 0x0300, IFACE, 8))
        except usb.core.USBError as e:
            # the other process (daemon or status) holds interface 2 for ~25 ms
            if e.errno != errno.EBUSY or attempt == 4:
                raise
            time.sleep(0.05)
        finally:
            # pyusb claims the interface implicitly; release it so status works next to the daemon
            usb.util.release_interface(dev, IFACE)


def query(dev, cmd):
    return decode(cmd, xfer(dev, packet(cmd), reply=True))


# ---------------------------------------------------------------------------
# CPU temperature
# ---------------------------------------------------------------------------
def read_cpu_temp_c():
    """Package id 0 from coretemp hwmon, in C; None if not found."""
    for name_path in glob.glob("/sys/class/hwmon/hwmon*/name"):
        try:
            if open(name_path).read().strip() != "coretemp":
                continue
        except OSError:
            continue
        base = os.path.dirname(name_path)
        # temp1_input is "Package id 0"
        for inp in sorted(glob.glob(os.path.join(base, "temp*_input"))):
            label_f = inp.replace("_input", "_label")
            try:
                label = open(label_f).read().strip() if os.path.exists(label_f) else ""
            except OSError:
                label = ""
            if "Package" in label or inp.endswith("temp1_input"):
                try:
                    return int(open(inp).read().strip()) / 1000.0
                except (OSError, ValueError):
                    pass
    return None


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------
def cmd_status(_args):
    dev = open_cooler()
    try:
        fan, pump = query(dev, CMD_RPM)
        fan_mode, pump_mode = query(dev, CMD_MODE)
    finally:
        usb.util.dispose_resources(dev)
    cpu = read_cpu_temp_c()
    print("fan  : %4d RPM, mode %s" % (fan, MODES.get(fan_mode, fan_mode)))
    print("pump : %4d RPM, mode %s" % (pump, MODES.get(pump_mode, pump_mode)))
    print("cpu  : %s" % ("n/a" if cpu is None else "%.0f C" % cpu))
    return 0


def cmd_daemon(_args):
    dev, ema, last_err = None, None, None
    try:
        while True:
            try:
                if dev is None:
                    dev = open_cooler()
                    print("cooler connected", flush=True)
                    last_err = None
                raw = read_cpu_temp_c()
                ema, t = smooth(ema, PANIC_C if raw is None else raw)  # no sensor -> fans up
                xfer(dev, temp_packet(t))
            except usb.core.USBError as e:
                if str(e) != last_err:  # log changes only, not every retry
                    print("USB: %s, retrying" % e, flush=True)
                    last_err = str(e)
                if dev is not None:
                    usb.util.dispose_resources(dev)
                dev = None
                time.sleep(2)
            time.sleep(INTERVAL_S)
    except KeyboardInterrupt:
        return 0


def cmd_selftest(_args):
    # packets: 8-byte feature payload, command first
    assert packet(CMD_RPM) == bytes([0xD8, 0, 0, 0, 0, 0, 0, 0])
    assert temp_packet(38.4) == bytes([0xE0, 38, 0, 0, 0, 0, 0, 0])
    assert temp_packet(-5)[1] == 0 and temp_packet(300)[1] == 100
    # replies: real captures from 24.10.2026
    assert decode(CMD_RPM, bytes.fromhex("d8040c0c80000000")) == (1036, 3200)
    assert decode(CMD_MODE, bytes.fromhex("dd06000000000000")) == (6, 0)
    try:
        decode(CMD_RPM, bytes.fromhex("dd06000000000000"))
        raise AssertionError("reply without command echo accepted")
    except ValueError:
        pass
    # smoothing: first sample passes, a 1 s spike is damped, panic bypasses
    assert smooth(None, 40) == (40, 40)
    assert 40 < smooth(40, 67)[1] < 50
    assert smooth(40, 95)[1] == 95
    print("selftest OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Gigabyte Waterforce 7a51 Linux fan control")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="fan/pump RPM and modes (read-only)")
    sub.add_parser("daemon", help="feed CPU temp to the cooler once per second")
    sub.add_parser("selftest", help="offline checks of packet building and smoothing")
    args = ap.parse_args()
    handlers = {"status": cmd_status, "daemon": cmd_daemon, "selftest": cmd_selftest}
    sys.exit(handlers[args.cmd](args))


if __name__ == "__main__":
    main()
