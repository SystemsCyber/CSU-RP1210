# RP1210 32-to-64-bit bridge

Many vehicle diagnostic adapters still ship **32-bit-only** RP1210 DLLs (for example DG DPA5 `DGDPA5MA.dll`). A 64-bit program cannot load a 32-bit DLL, so Windows reports "not a valid Win32 application". This bridge lets 64-bit RP1210 applications, including the 64-bit `CSU_RP1210.exe` and `csu.exe`, use those drivers unchanged.

```
64-bit application ──► rp1210_bridge64.dll ══ named pipe ══► rp1210_host32.exe ──► vendor 32-bit RP1210 DLL
      (x64)               (x64, RP1210 API)    (one per thread)      (x86)              (e.g. DGDPA5MA.dll)
```

- `rp1210_bridge64.dll` exports the standard RP1210 API (`RP1210_ClientConnect`, `ReadMessage`, `SendMessage`, `SendCommand`, `ReadVersion`, `ReadDetailedVersion`, `GetHardwareStatus(Ex)`, `GetErrorMsg`, `GetLastErrorMsg`, `Ioctl`). It forwards each call over a local named pipe.
- `rp1210_host32.exe` loads the vendor DLL by full path from `SysWOW64` and makes the real calls. It exits automatically when the application exits.
- **One pipe connection per calling thread.** A blocking `RP1210_ReadMessage` in a reader thread never delays `SendMessage` or `SendCommand` from other threads.

It follows the same approach as [SystemsCyber/ShimDLL](https://github.com/SystemsCyber/ShimDLL), which is a same-bitness pass-through, but runs the vendor DLL in a separate 32-bit process.

## Build

Requires Visual Studio 2022 (or Build Tools) with the C++ workload. It uses the same `cl.exe` as ShimDLL.

```
rp1210_bridge\build.bat
```

Output in `rp1210_bridge\bin\`:
- `rp1210_bridge64.dll` (x64)
- `rp1210_host32.exe` (x86)
- `fake_rp1210.dll` (x86 loopback DLL used only by the tests)

## Use

**CSU-RP1210 (automatic).** When only a 32-bit vendor DLL is installed, `RP1210.py` and `csu.exe` load the bridge instead. The bridge must sit with `rp1210_host32.exe` next to the program, in `rp1210_bridge\bin`, or inside the portable exe; `build_exe.py` bundles both. `csu devices` marks such adapters `[via RP1210 32-to-64-bit bridge]`.

**Your own 64-bit program.** Load `rp1210_bridge64.dll`, then name the vendor DLL before the first RP1210 call:

```c
RP1210Bridge_SetTarget("DGDPA5MA");     // or a full path to a 32-bit DLL
short id = RP1210_ClientConnect(NULL, 1, "J1939:Baud=250", 8192, 8192, 0);
```

You can set `RP1210_BRIDGE_TARGET=DGDPA5MA` instead. `RP1210Bridge_GetStatus(text, size)` reports which DLL the host loaded, or why it failed.

**Unmodified third-party 64-bit programs.** Copy `rp1210_bridge64.dll` under the vendor's name (e.g. `DGDPA5MA.dll`) somewhere the program loads it from, with `rp1210_host32.exe` beside it. A renamed copy serves the DLL it is named after. Placing it in `C:\Windows\System32` (administrator rights required) makes it visible to all 64-bit programs, while 32-bit programs keep loading the vendor's original from `SysWOW64`. Remove it if the vendor later installs a native 64-bit DLL.

**Options**
- `RP1210_BRIDGE_HOST`: full path of `rp1210_host32.exe` if it is not beside the DLL.
- `RP1210_BRIDGE_LOG`: file to which the host logs connects, commands and errors.

## Limitations

- `hwndClient` window-message notifications are not forwarded (pass `NULL`; polling and blocking reads work).
- `RP1210_Ioctl` returns `ERR_COMMAND_NOT_SUPPORTED`, because its buffers have no defined size.
- `RP1210_ReadVersion` returns one character per field, as the RP1210 API specifies.

## Tests

`python -m pytest tests/test_rp1210_bridge.py` exercises every function through the bridge, against the loopback DLL. It covers buffer copy-back, sub-error codes, a blocking read alongside sends from another thread, disconnect releasing a blocked reader, missing DLLs, renamed copies, and host clean-up.

Verified on hardware (2026-10-04, from 64-bit Python):
- **DG DPA5 `DGDPA5MA.dll` (32-bit only):** connects through the bridge and returns DG's own "Hardware not responding" when no adapter is attached.
- **PEAK `PEAKRP32.dll`, 32-bit version:** about 300 J1939 messages per second from a live 250 kbit/s bus on PCAN-PCI Express FD channel 4.
