# CSU-RP1210
 Heavy Vehicle diagnostic prototyping and training software for ATA/TMC RP1210 compatible devices.

## Rust core (next generation, in progress)

A cross-platform Rust core lives in `crates/`. It supports RP1210 on Windows, PEAK PCAN-Basic with CAN FD on Windows and Linux, SocketCAN on Linux, and candump log replay. See [docs/ARCHITECTURE_PROPOSAL.md](docs/ARCHITECTURE_PROPOSAL.md) for the architecture and [model/sysml](model/sysml/README.md) for the SysML v2 model.

```
cargo build --release
target/release/csu devices                                   # installed RP1210 drivers and adapters
target/release/csu summary path/to/capture.log               # network inventory
target/release/csu decode pcan:USBBUS1,bitrate=500000 --limit 100
target/release/csu serve socketcan:can0                      # web tree view at http://127.0.0.1:8210
target/release/csu serve rp1210:PEAKRP32,device=1,bitrate=250000
```

Build a 32-bit binary (`cargo build --release --target i686-pc-windows-msvc`) for vendors whose RP1210 DLLs are 32-bit only.

### J1939 database

`J1939db.json` in this repository is a **skeleton**: it has the schema and no SAE J1939 Digital Annex content. To decode parameter names and values, generate a database from your licensed J1939DA (for example with [pretty_j1939](https://github.com/nmfta-repo/pretty_j1939)) and save it as `J1939db.licensed.json` (git-ignored). You can also point `CSU_J1939DB` at it, or pass `--db`. Both the Python application and the Rust core look for it in that order.

## Setup
Install a 32-bit version of Python onto a Windows computer.

Be sure to have pip installed.

Open the Command Prompt and confirm the Python install by typing `python`. 

If the following shows up

```Python 3.8.5 (tags/v3.8.5:580fbb0, Jul 20 2020, 15:57:54) [MSC v.1924 64 bit (AMD64)] on win32```

Then you'll have to use the windows python launcher `py -3.7` (or whatever your 32-bit version is). You should get a response at the command prompt like this:
``` 
C:\Users\Jeremy>py -3.7
Python 3.7.4 (tags/v3.7.4:e09359112e, Jul  8 2019, 19:29:22) [MSC v.1916 32 bit (Intel)] on win32
Type "help", "copyright", "credits" or "license" for more information.
>>>
```

Type `exit()` to quit the interpeter. The imortant thing is to check for 32-bit python.

### Install the Requirements
From the command prompt, navigate to the directory of this application (after you've cloned or downloaded it). Then execute the command:

`py -3.7 -m pip install -r requirements.txt`

Once the requirements are installed, run the program:

`py -3.7 -m CSU_RP1210`





