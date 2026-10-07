# CSUCAN: RP1210 driver for PEAK adapters and SocketCAN

`csucan.dll` (Windows) / `libcsucan.so` (Linux) is an **RP1210 C** driver built on the CSU-RP1210 Rust core. It lets RP1210 applications use:

| DeviceID | Device | `Channel=N` selects |
|---|---|---|
| 1 | PEAK USB adapters (PCAN-USB, PCAN-USB FD, PCAN-USB Pro FD, …) | the N-th attached `USBBUSx` |
| 2 | PEAK PCI/PCIe cards (e.g. PCAN-PCI Express FD) | the N-th attached `PCIBUSx` |
| 3 | PEAK LAN gateways | the N-th attached `LANBUSx` |
| 10 | Linux SocketCAN (`can0`, `vcan0`, …) | the N-th CAN interface, sorted by name |
| 99 | Virtual loopback (no hardware; tests and demos) | an independent in-process bus |

**Why it exists.** PEAK's own RP1210 driver (`PEAKRP32`) only serves devices registered in `C:\Windows\PEAKRP32.ini`, and it has no CAN FD. CSUCAN asks PCAN-Basic which channels are attached each time devices are listed, so a newly plugged-in PCAN-USB FD appears with no vendor configuration. `CSUCAN_WriteIni(path)` writes a standard RP1210 vendor INI describing them.

## Protocols and parameters

- **`CAN` and `J1939`**, in the RP1210C message formats, with:
  - echo (`Echo_Transmitted_Messages`) and pass/discard filters;
  - message receive on/off;
  - blocking reads with `Set_BlockTimeout`;
  - `Flush_Tx_Rx_Buffers` and `Disallow_Further_Connections`.
- **J1939 transport in the driver.** It reassembles BAM and RTS/CTS, and sends BAM (when the destination is global or `0x80` is set in the how/priority byte) or RTS/CTS for messages over 8 bytes. `Set_J1939_Interpacket_Time` sets the BAM packet spacing.
  - `Protect_J1939_Address` claims an address. The driver then answers RTS with CTS and EoMA, and answers requests for Address Claimed.
- **`Baud=`** takes kbit/s (`125`, `250`, `500`, `1000`). **CAN FD extension:** `Baud=<nominal>/<data>`, e.g. `J1939:Baud=250/2000,Channel=1`, opens the channel in CAN FD mode.
  - `FDFlags=1` (CAN client only) sets bit 1 (FD) and bit 2 (BRS) of the message-type byte. Otherwise the formats are plain RP1210C.
- **`Channel=N`** follows RP1210C/D.
- **Not supported:** J1708 and ISO15765 clients (`ERR_INVALID_PROTOCOL`); the J1939/CAN message-filter list commands and `RP1210_Ioctl` (`ERR_COMMAND_NOT_SUPPORTED`).

Clients on the same device and channel share one bus. Frames sent by one client are delivered to the others, as on a real adapter.

## Build

```
cargo build --release -p csucan        # target\release\csucan.dll
```

CSU-RP1210 finds it in `target\release`, next to the program, or inside `CSU_RP1210.exe` (`build_exe.py` bundles it). It appears in the RP1210 dialog as **CSUCAN – CSU native CAN**. Other RP1210 applications can use it by copying `csucan.dll` to `C:\Windows\System32` and the generated `CSUCAN.ini` to `C:\Windows`, and adding `CSUCAN` to `[RP1210Support] APIImplementations` in `RP121032.ini`. That requires administrator rights.

## Notes

- PCAN-Basic allows one program per channel. PCAN-View can share a channel only when it uses the same bit rates.
- `RP1210_GetLastErrorMsg` and `CSUCAN_LastError` return the underlying driver message, e.g. "The PCAN-Hardware is already being used by a PCAN-Net".
- Timestamps are microseconds (`TimeStampWeight=1`) from the PCAN hardware clock, aligned to the PC clock.

## Tests

`python -m pytest tests/test_csucan.py` drives the C API on the virtual device and on a candump replay device (`CSUCAN_DEVICE_<id>=<bus spec>`). It covers:
- message formats, echo, sibling delivery and filters;
- BAM transmit and reassembly;
- RTS/CTS with a claimed address (RTS → CTS → EoMA), and an RTS timeout;
- error codes and blocked-read wake-up;
- reassembly of a recorded DM1 (BAM) and VIN (RTS/CTS).

Verified on hardware (2026-10-04):
- **PCAN-USB FD (device 1):** 250 kbit/s classic and 250/2000 CAN FD.
- **PCAN-PCI Express FD (device 2, `Channel=4`):** classic and FD.
- **Live J1939 bus:** about 290–300 messages/s, including the CSU-RP1210 GUI and the portable exe.
