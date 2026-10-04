package CSU_Deployment {
    doc /* Physical view: Rust crates, execution platforms and the driver libraries each
         * process can load, plus requirement satisfaction for the configured system. */

    private import ScalarValues::*;
    private import CSU_Logical::*;
    private import CSU_Requirements::*;

    // ---- Software artifacts -------------------------------------------------

    part def Crate {
        attribute name : String;
        attribute path : String;
    }

    part def DriverLibrary {
        attribute fileName : String;
        attribute is64bit : Boolean;
    }

    part def Executable {
        attribute targetTriple : String;
        part crates : Crate[1..*];
    }

    // ---- Execution platforms ---------------------------------------------

    abstract part def ExecutionPlatform {
        attribute os : String;
    }

    part def WindowsWorkstation :> ExecutionPlatform {
        doc /* RP121032.ini lists RP1210 implementations; vendor DLLs live in System32
             * (64-bit) and SysWOW64 (32-bit). */
        attribute redefines os = "Windows 10/11";
        part x64Process : Executable { attribute redefines targetTriple = "x86_64-pc-windows-msvc"; }
        part x86Process : Executable[0..1] {
            doc /* Needed only for vendors that ship 32-bit-only RP1210 DLLs (e.g. DGDPA5MA). */
            attribute redefines targetTriple = "i686-pc-windows-msvc";
        }
        part drivers : DriverLibrary[0..*];
    }

    part def LinuxHost :> ExecutionPlatform {
        doc /* Laptop or Raspberry Pi; PEAK USB FD adapters appear as SocketCAN interfaces
             * through the peak_usb kernel driver. Optional kiosk browser for the web UI. */
        attribute redefines os = "Linux";
        part process : Executable { attribute redefines targetTriple = "x86_64-unknown-linux-gnu | aarch64-unknown-linux-gnu"; }
    }

    part def UiClient :> ExecutionPlatform {
        doc /* Any browser, a Tauri desktop webview (planned), or an MCP client rendering ui:// views. */
    }

    // ---- Configured system and allocation -----------------------------------

    part csuDeployment {
        part crates {
            part csuBus : Crate { attribute redefines name = "csu-bus"; attribute redefines path = "crates/csu-bus"; }
            part csuJ1939 : Crate { attribute redefines name = "csu-j1939"; attribute redefines path = "crates/csu-j1939"; }
            part csuSec : Crate { attribute redefines name = "csu-sec"; attribute redefines path = "crates/csu-sec"; }
            part csuApp : Crate { attribute redefines name = "csu"; attribute redefines path = "crates/csu"; }
        }

        part system : CsuSystem {
            part :>> bus {
                part rp1210 : Rp1210Backend :> backends;
                part pcan : PcanBackend :> backends;
                part socketcan : SocketCanBackend :> backends;
                part replay : ReplayBackend :> backends;
                part loopback : VirtualBackend :> backends;
            }
        }

        part workstation : WindowsWorkstation {
            part peakRp1210 : DriverLibrary :> drivers { attribute redefines fileName = "PEAKRP32.dll"; attribute redefines is64bit = true; }
            part dgDpa5 : DriverLibrary :> drivers { attribute redefines fileName = "DGDPA5MA.dll"; attribute redefines is64bit = false; }
            part pcanBasic : DriverLibrary :> drivers { attribute redefines fileName = "PCANBasic.dll"; attribute redefines is64bit = true; }
        }
        part linux : LinuxHost;
        part browser : UiClient;

        allocate system.bus to crates.csuBus;
        allocate system.j1939 to crates.csuJ1939;
        allocate system.security to crates.csuSec;
        allocate system.web to crates.csuApp;
        allocate system.cli to crates.csuApp;

        allocate system.bus.rp1210 to workstation.x64Process;
        allocate system.bus.pcan to workstation.x64Process;
        allocate system.bus.socketcan to linux.process;

        // ---- Requirement satisfaction ----
        satisfy reqPlat by system;
        satisfy reqRp1210 by system.bus.rp1210;
        satisfy reqPeak by system.bus.pcan;
        satisfy reqSocketCan by system.bus.socketcan;
        satisfy reqThroughput by system;
        satisfy reqLatency by system;
        satisfy reqTp by system.j1939.tp;
        satisfy reqFd by system.j1939.fd;
        satisfy reqDm by system.j1939.dm;
        satisfy reqUds by system.uds;
        satisfy reqDb by system.j1939.decoder;
        satisfy reqTree by system.web;
        satisfy reqPassive by system.policy;
        satisfy reqVectors by system.security.tagVerifier;
        satisfy reqKeys by system.security;
        satisfy reqProvenance by system.evidence;
        satisfy reqPlugins by system.plugins;
        satisfy reqAi by system.mcp;
    }
}
