# aorus-waterforce-linux

Fan control for the **GIGABYTE AORUS WATERFORCE 240 / 280 / 360 (non-X)** AIO liquid cooler on Linux.
USB ID `1044:7a51`, listed by `lsusb` as "GIGABYTE CPU Cooler".

One Python script and a systemd unit. The script sends the CPU temperature to the cooler, and that is all the cooler needs to run its own fan curve again.

## The problem

The fans and the pump plug into the cooler's hub instead of motherboard headers, so BIOS fan control has no effect on them.
On Linux the fans stay at about 1000 RPM and the pump at about 3200 RPM no matter how hot the CPU gets.
The kernel does not bind the cooler's vendor HID interface:

```
usbhid 1-12.2:1.2: can't add hid device: -22
```

Existing projects closed this model as not possible to support:

- liquidctl [#589](https://github.com/liquidctl/liquidctl/issues/589)
- gigabyte_waterforce-hwmon [#1](https://github.com/aleksamagicka/gigabyte_waterforce-hwmon/issues/1)

Both assumed that the Windows software uploads firmware to the cooler and that Linux would have to do the same. For this model that is not the case.

## What is actually going on

- The cooler runs its fan and pump curves on its own. The curve input is the CPU temperature, which the host sends once per second. On Windows the `AorusLcdService` service does this.
- When the temperature stops arriving, the cooler falls back to its default state within a few seconds: fans ~1000 RPM, pump ~3200 RPM. This is the state Linux users see.
- The current GIGABYTE Control Center uploads no firmware to the `7a51` model.

Protocol details are in [PROTOCOL.md](PROTOCOL.md).

## What this project does

`waterforce.py daemon` does the same job as the Windows service: it reads the CPU package temperature, smooths it and sends it to the cooler once per second.

- Transport: pyusb control transfers on EP0 to interface 2. No kernel driver is needed.
- It does not change modes or curves. The mode you set in Windows Control Center stays stored in the cooler and keeps working.

## Requirements

- Python 3
- pyusb: `sudo apt install python3-usb` (Debian/Ubuntu) or `pip install pyusb`
- Intel CPU, because the temperature comes from the `coretemp` driver (see [Limitations](#limitations))

## Installation

```bash
git clone https://github.com/Khaldor48/aorus-waterforce-linux.git
cd aorus-waterforce-linux
```

The udev rule lets the logged-in user run the script without `sudo`. The root service does not need it.

```bash
sudo cp 70-gigabyte-waterforce.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=usb
python3 waterforce.py status
```

The service runs as root, so it runs a root-owned copy of the script from `/usr/local/bin`:

```bash
sudo install -m 755 waterforce.py /usr/local/bin/waterforce.py
sudo cp waterforce-fan.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now waterforce-fan.service
journalctl -u waterforce-fan -f      # log
```

After an update (`git pull`) or an edit of the script, install it again and restart the service. `enable --now` does not restart a service that is already running.

```bash
sudo install -m 755 waterforce.py /usr/local/bin/waterforce.py && sudo systemctl restart waterforce-fan
```

## Usage

```bash
python3 waterforce.py status     # fan/pump RPM, modes, CPU temperature (read-only)
python3 waterforce.py daemon     # send the CPU temperature once per second (Ctrl+C stops it)
python3 waterforce.py selftest   # offline checks, no device needed
```

`status` works while the service is running:

```
$ python3 waterforce.py status
fan  : 1232 RPM, mode Quiet
pump : 2270 RPM, mode Balanced
cpu  : 36 C
```

## Modes and curves

Set the mode and the curve in GIGABYTE Control Center on Windows. Apply stores them in the cooler, and they stay there after you boot Linux.
This project only supplies the temperature input. The commands that read and write modes and curves are documented in [PROTOCOL.md](PROTOCOL.md), but the script does not send the write commands.

## Tuning

Both values are constants at the top of `waterforce.py`. After a change, install the script again and restart the service (see [Installation](#installation)).

- `ALPHA` (default `0.2`): smoothing weight per 1-second sample. CPU package temperature spikes for a second at a time (for example 37 → 67 °C), and without smoothing the fans would follow every spike. A higher value reacts faster but makes the fan speed less steady.
- `PANIC_C` (default `90`): at or above this temperature the raw value is sent without smoothing.

## Tested on

- AORUS WATERFORCE 280 (non-X), Intel Core i9-14900K, GIGABYTE Z790 AERO G, Linux kernel 6.17.
- The 240 and 360 non-X models report the same USB ID and should behave the same, but nobody has confirmed that yet. Reports are welcome.

## Limitations

- **Intel only.** On AMD the temperature driver is `k10temp`, not `coretemp`, so the daemon finds no sensor and sends 90 °C as a safety fallback, which keeps the fans high. A PR is welcome.
- **Cold start without Windows is not verified yet.** All tests so far ran with Windows booted in between. It is not yet known whether the cooler behaves the same after a full power-off (including the PSU switch) without Windows. Results are welcome.
- **Waterforce X models are different hardware** with a different protocol (for example `1044:7a4d`). Use the in-tree kernel driver `gigabyte_waterforce` or liquidctl for those.

## Safety

- `status` only reads. `daemon` only sends the CPU temperature, the same thing the official Windows service sends. Neither writes modes, curves or firmware.
- When the daemon is not running, the cooler runs in its default state, the same as on Linux without this project.

## How this came about

The protocol was worked out by decompiling the .NET module `GBT_Cooler` from GIGABYTE Control Center for interoperability, then checked against the hardware.
This repository contains no GIGABYTE files: no binaries, no firmware, no decompiled code.

## Disclaimer

This project is not affiliated with or endorsed by GIGABYTE. AORUS and WATERFORCE are trademarks of their owner. Use at your own risk.

## License

MIT, see [LICENSE](LICENSE).
