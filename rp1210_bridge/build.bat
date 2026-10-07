@echo off
rem Builds the RP1210 32-to-64-bit bridge with the Visual Studio C compiler.
rem   bin\rp1210_bridge64.dll   (x64)  - load this from 64-bit RP1210 applications
rem   bin\rp1210_host32.exe     (x86)  - started by the bridge; loads the 32-bit vendor DLL
rem   bin\fake_rp1210.dll       (x86)  - loopback test DLL (tests only)
setlocal
cd /d "%~dp0"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSINSTALL=%%i"
if not defined VSINSTALL (
    echo Visual Studio C++ build tools not found.
    exit /b 1
)
set "VCVARS=%VSINSTALL%\VC\Auxiliary\Build\vcvarsall.bat"
rem vcvarsall calls vswhere without a path.
set "PATH=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer;%PATH%"
if not exist bin mkdir bin
if not exist obj\x64 mkdir obj\x64
if not exist obj\x86 mkdir obj\x86
set "CFLAGS=/nologo /O2 /W4 /WX /MT /D_CRT_SECURE_NO_WARNINGS"

setlocal
call "%VCVARS%" x64 >nul || exit /b 1
cl %CFLAGS% /LD /Foobj\x64\ bridge64.c /link /DEF:bridge64.def /OUT:bin\rp1210_bridge64.dll /IMPLIB:obj\x64\rp1210_bridge64.lib || exit /b 1
endlocal

setlocal
call "%VCVARS%" x86 >nul || exit /b 1
cl %CFLAGS% /Foobj\x86\ host32.c /link /OUT:bin\rp1210_host32.exe || exit /b 1
cl %CFLAGS% /LD /Foobj\x86\ test\fake_rp1210.c /link /DEF:test\fake_rp1210.def /OUT:bin\fake_rp1210.dll /IMPLIB:obj\x86\fake_rp1210.lib || exit /b 1
endlocal

echo Built bin\rp1210_bridge64.dll, bin\rp1210_host32.exe, bin\fake_rp1210.dll
