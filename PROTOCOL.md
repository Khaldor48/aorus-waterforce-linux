# AORUS Waterforce 280 non-X (USB 1044:7a51): protocol

Source: the official GIGABYTE Control Center, module `Lib/GBT_Cooler` from Windows
(`ucCooler.dll`, `Service/AorusLcdService.exe`, .NET decompiled with ilspycmd).
Verified on hardware on 2026-10-24, in the state after Windows (cold boot not verified yet, see the end).

## Transport (verified)
- Interface 2, HID usage page 0xFF02, no report ID. The descriptor (47 B) has a 64 B input, a 64 B output and an 8 B feature report.
  The software for 7A51 selects the collection with `uReportId = 0xFF02`.
- Kernel 6.17 does not bind interface 2 (`can't add hid device: -22`), so the transport is pyusb (EP0):
  - command: `SET_REPORT` feature, ID 0: `ctrl_transfer(0x21, 0x09, 0x0300, 2, payload8)`
  - reply: `GET_REPORT` feature, ID 0: `ctrl_transfer(0xA1, 0x01, 0x0300, 2, 8)`, 20 ms after the command
  - GET without a preceding SET stalls (EPIPE).
- The application works with a 9-byte buffer `99 <cmd> <args…>`, where byte 0 is the report ID slot.
  The payload on the wire is buffer[1..8]. The reply echoes the command at index 0.

## Commands (payload, reply r[0..7])
| command | payload | reply | in the application |
|---|---|---|---|
| RPM | `D8` | fan = r[1..2] BE16, pump = r[3..4] BE16 | GetRPMSpeed (UI every 2 s) |
| read mode | `DD` | fan = r[1], pump = r[2] (3 = 0) | GetMode |
| read curve | `D9 i type` | temperature r[3], RPM r[4..5] BE16 | i = 0..3, type 1 = fan, 2 = pump |
| write mode | `E5 01 fanMode`, `E5 02 pumpMode` | – | SetSpeedMode |
| write curve | `E6 i type temp rpmHi rpmLo` | – | SetSpeedCurve, 4 points |
| save | `B6` | – | Save, after Apply/Default |
| **CPU temperature** | `E0 °C` | – | AorusLcdService **every 1 s** |

Modes: 0 Balanced, 1 Customized, 2 Default, 3 FixedRPM, 4 Turbo, 5 Performance, 6 Quiet, 7 ZeroRPM.
Fan limits in the UI for 7A51: 950–2500 RPM. The Default button sets fan Quiet, pump Turbo.

## How it works
- The device holds the curve itself. Its input temperature is the **CPU temperature from the host** (`E0`).
  On Windows the AorusLcdService service sends it every second (rounded to °C); the UI only reads RPM and mode.
- Without `E0` the device falls back to its default state within a few seconds (< 10 s): fan ~1036 RPM, pump ~3220 RPM.
  That is why the fans on Linux always run "the same".
- Test on 2026-10-24 (fan mode Quiet, pump Balanced):
  - `E0 70`: fan 1036 → 2366 RPM in ~4 s
  - `E0 30`: fan 1036, pump 2250
  - real CPU temperature: short package temperature spikes (37 → 67 °C for 1 s) push the fans up to ~2270 RPM, so the daemon smooths the temperature.
- Example device state (2026-10-24): fan mode 6 (Quiet), pump 0. Example stored custom fan curve: 30 °C 1539, 49 °C 1956, 65 °C 2872.

## Firmware upload
The current software for 7A51 uploads nothing: it calls neither the 4 KB upload nor `E7`, and it has the LCD functions disabled for PID 7A51.
The upload from [hwmon issue #1](https://github.com/aleksamagicka/gigabyte_waterforce-hwmon/issues/1) (2023) comes from different or older software.

The zip from [liquidctl #589](https://github.com/liquidctl/liquidctl/issues/589) (sha256 b547e305…a522) contains only the service flashers `FlashAIOImage`, `LCD_Factory` and `Flash.exe`.
They program the AP firmware into internal flash and images (`images/{0,90,180}.bin`, 10 MiB for the LCD rotations) into external flash.
They do this in IAP mass-storage mode ("LCD_PLATFORM", SCSI pass-through), not over HID 7a51. Both are permanent flash.

## Open
- Cold boot (full power-off including the PSU switch): does `D8` reply, and does the device react to `E0` without Windows?
- Whether `B6` writes to flash (not called from Linux).
