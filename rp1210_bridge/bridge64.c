/*
 * rp1210_bridge64.dll - 64-bit RP1210 DLL that forwards every call to a
 * 32-bit vendor RP1210 DLL running in rp1210_host32.exe.
 *
 * Choosing the vendor DLL (first match wins):
 *   1. RP1210Bridge_SetTarget("DGDPA5MA") called by the application
 *   2. the RP1210_BRIDGE_TARGET environment variable
 *   3. this DLL's own file name: a copy renamed DGDPA5MA.dll serves DGDPA5MA,
 *      so unmodified 64-bit RP1210 applications can use it.
 *
 * The host executable is found next to this DLL, or at %RP1210_BRIDGE_HOST%.
 *
 * Limitations: window-message notification (hwndClient) is not forwarded;
 * RP1210_Ioctl returns ERR_COMMAND_NOT_SUPPORTED because its buffers are
 * untyped.
 */
#include "rp1210_bridge.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define EXPORT __declspec(dllexport)

static HMODULE g_self;
static CRITICAL_SECTION g_lock;
static char g_target[MAX_PATH];
static char g_pipe_name[128];
static HANDLE g_host;               /* host process handle once started */
static char g_last_error[256];      /* bridge-level failure text */
static __declspec(thread) HANDLE t_pipe = INVALID_HANDLE_VALUE;

static void set_error(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    _vsnprintf_s(g_last_error, sizeof(g_last_error), _TRUNCATE, fmt, ap);
    va_end(ap);
}

static void default_target(void)
{
    char path[MAX_PATH];
    const char *env;
    char *base, *dot;

    if (g_target[0]) return;
    env = getenv("RP1210_BRIDGE_TARGET");
    if (env && *env) {
        strncpy_s(g_target, sizeof(g_target), env, _TRUNCATE);
        return;
    }
    GetModuleFileNameA(g_self, path, sizeof(path));
    base = strrchr(path, '\\');
    base = base ? base + 1 : path;
    dot = strrchr(base, '.');
    if (dot) *dot = '\0';
    if (_stricmp(base, "rp1210_bridge64") != 0)
        strncpy_s(g_target, sizeof(g_target), base, _TRUNCATE);
}

/* Start rp1210_host32.exe if needed. Caller holds g_lock. */
static BOOL ensure_host(void)
{
    char host[MAX_PATH], cmd[1024];
    const char *env;
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;

    if (g_host) {
        if (WaitForSingleObject(g_host, 0) == WAIT_TIMEOUT) return TRUE;
        CloseHandle(g_host);
        g_host = NULL;
    }
    default_target();
    if (!g_target[0]) {
        set_error("RP1210 bridge: no target DLL (call RP1210Bridge_SetTarget or set RP1210_BRIDGE_TARGET)");
        return FALSE;
    }

    env = getenv("RP1210_BRIDGE_HOST");
    if (env && *env) {
        strncpy_s(host, sizeof(host), env, _TRUNCATE);
    } else {
        char *slash;
        GetModuleFileNameA(g_self, host, sizeof(host));
        slash = strrchr(host, '\\');
        if (slash) slash[1] = '\0';
        strncat_s(host, sizeof(host), BRIDGE_HOST_EXE, _TRUNCATE);
    }

    _snprintf_s(g_pipe_name, sizeof(g_pipe_name), _TRUNCATE, "\\\\.\\pipe\\rp1210bridge-%lu-%llu",
                GetCurrentProcessId(), (unsigned long long)GetTickCount64());
    _snprintf_s(cmd, sizeof(cmd), _TRUNCATE, "\"%s\" %s \"%s\" %lu", host, g_pipe_name, g_target, GetCurrentProcessId());

    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    if (!CreateProcessA(host, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) {
        set_error("RP1210 bridge: cannot start %s (Windows error %lu)", host, GetLastError());
        return FALSE;
    }
    CloseHandle(pi.hThread);
    g_host = pi.hProcess;
    return TRUE;
}

/* This thread's pipe connection to the host. */
static HANDLE thread_pipe(void)
{
    DWORD mode = PIPE_READMODE_MESSAGE;
    ULONGLONG deadline;
    char name[128];

    if (t_pipe != INVALID_HANDLE_VALUE) return t_pipe;

    EnterCriticalSection(&g_lock);
    if (!ensure_host()) {
        LeaveCriticalSection(&g_lock);
        return INVALID_HANDLE_VALUE;
    }
    strncpy_s(name, sizeof(name), g_pipe_name, _TRUNCATE);
    LeaveCriticalSection(&g_lock);

    deadline = GetTickCount64() + 10000;
    while (GetTickCount64() < deadline) {
        HANDLE h = CreateFileA(name, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
        if (h != INVALID_HANDLE_VALUE) {
            SetNamedPipeHandleState(h, &mode, NULL, NULL);
            t_pipe = h;
            return h;
        }
        if (WaitForSingleObject(g_host, 0) != WAIT_TIMEOUT) {
            set_error("RP1210 bridge: %s exited during startup", BRIDGE_HOST_EXE);
            return INVALID_HANDLE_VALUE;
        }
        if (GetLastError() == ERROR_PIPE_BUSY) WaitNamedPipeA(name, 200);
        else Sleep(20);
    }
    set_error("RP1210 bridge: timed out connecting to %s", BRIDGE_HOST_EXE);
    return INVALID_HANDLE_VALUE;
}

static void drop_thread_pipe(void)
{
    if (t_pipe != INVALID_HANDLE_VALUE) {
        CloseHandle(t_pipe);
        t_pipe = INVALID_HANDLE_VALUE;
    }
}

/*
 * One request/response round trip. Copies up to out_cap response payload
 * bytes into out. Returns FALSE (and sets resp->ret) if the host is unreachable.
 */
static BOOL call(uint32_t op, const int32_t *args, int nargs, const void *payload, uint32_t len,
                 BridgeResponse *resp, void *out, uint32_t out_cap)
{
    static const uint32_t req_size = sizeof(BridgeRequest);
    unsigned char *buf;
    BridgeRequest req;
    DWORD n = 0;
    HANDLE pipe;
    BOOL ok = FALSE;

    resp->ret = ERR_DLL_NOT_INITIALIZED;
    resp->extra = 0;
    resp->len = 0;
    if (len > BRIDGE_MAX_PAYLOAD) {
        resp->ret = 141; /* ERR_MESSAGE_TOO_LONG */
        return FALSE;
    }
    pipe = thread_pipe();
    if (pipe == INVALID_HANDLE_VALUE) return FALSE;

    buf = (unsigned char *)malloc(BRIDGE_PIPE_BUFFER);
    if (!buf) return FALSE;
    ZeroMemory(&req, sizeof(req));
    req.op = op;
    for (int i = 0; i < nargs && i < 6; i++) req.arg[i] = args[i];
    req.len = len;
    memcpy(buf, &req, req_size);
    if (len) memcpy(buf + req_size, payload, len);

    if (WriteFile(pipe, buf, req_size + len, &n, NULL) &&
        ReadFile(pipe, buf, BRIDGE_PIPE_BUFFER, &n, NULL) && n >= sizeof(BridgeResponse)) {
        memcpy(resp, buf, sizeof(BridgeResponse));
        if (resp->len > n - sizeof(BridgeResponse)) resp->len = n - sizeof(BridgeResponse);
        if (out && out_cap) memcpy(out, buf + sizeof(BridgeResponse), resp->len < out_cap ? resp->len : out_cap);
        ok = TRUE;
    } else {
        set_error("RP1210 bridge: lost connection to %s (Windows error %lu)", BRIDGE_HOST_EXE, GetLastError());
        drop_thread_pipe();
        resp->ret = ERR_HARDWARE_NOT_RESPONDING;
    }
    free(buf);
    return ok;
}

/* ---- Bridge-specific exports ------------------------------------------- */

/* Select the 32-bit vendor DLL (name such as "DGDPA5MA", or a full path).
 * Must be called before the first RP1210 call. Returns 0 on success. */
EXPORT short WINAPI RP1210Bridge_SetTarget(const char *target)
{
    short rc = 0;
    EnterCriticalSection(&g_lock);
    if (g_host) {
        rc = ERR_CLIENT_ALREADY_CONNECTED; /* host already running for another target */
    } else if (target && *target) {
        strncpy_s(g_target, sizeof(g_target), target, _TRUNCATE);
    }
    LeaveCriticalSection(&g_lock);
    return rc;
}

/* Copies the host's load status ("loaded C:\\...\\X.dll" or the failure). */
EXPORT short WINAPI RP1210Bridge_GetStatus(char *text, short size)
{
    BridgeResponse resp;
    char status[512];
    memset(status, 0, sizeof(status));
    if (call(OP_HELLO, NULL, 0, NULL, 0, &resp, status, sizeof(status) - 1)) {
        if (text && size > 0) strncpy_s(text, size, status, _TRUNCATE);
        return resp.extra ? NO_ERRORS : ERR_DLL_NOT_INITIALIZED;
    }
    if (text && size > 0) strncpy_s(text, size, g_last_error, _TRUNCATE);
    return ERR_DLL_NOT_INITIALIZED;
}

/* ---- RP1210 API ------------------------------------------------------------ */

EXPORT short WINAPI RP1210_ClientConnect(HWND hwndClient, short nDeviceID, const char *fpchProtocol,
                                         long lTxBufferSize, long lRxBufferSize, short nIsAppPacketizingInMsgs)
{
    BridgeResponse resp;
    int32_t args[4] = {nDeviceID, lTxBufferSize, lRxBufferSize, nIsAppPacketizingInMsgs};
    const char *proto = fpchProtocol ? fpchProtocol : "";
    (void)hwndClient;
    call(OP_CLIENT_CONNECT, args, 4, proto, (uint32_t)strlen(proto) + 1, &resp, NULL, 0);
    return (short)resp.ret;
}

EXPORT short WINAPI RP1210_ClientDisconnect(short nClientID)
{
    BridgeResponse resp;
    int32_t args[1] = {nClientID};
    call(OP_CLIENT_DISCONNECT, args, 1, NULL, 0, &resp, NULL, 0);
    return (short)resp.ret;
}

EXPORT short WINAPI RP1210_SendMessage(short nClientID, unsigned char *fpchClientMessage, short nMessageSize,
                                       short nNotifyStatusOnTx, short nBlockOnSend)
{
    BridgeResponse resp;
    int32_t args[4] = {nClientID, nMessageSize, nNotifyStatusOnTx, nBlockOnSend};
    uint32_t len = (fpchClientMessage && nMessageSize > 0) ? (uint32_t)nMessageSize : 0;
    call(OP_SEND_MESSAGE, args, 4, fpchClientMessage, len, &resp, NULL, 0);
    return (short)resp.ret;
}

EXPORT short WINAPI RP1210_ReadMessage(short nClientID, unsigned char *fpchAPIMessage, short nBufferSize,
                                       short nBlockOnRead)
{
    BridgeResponse resp;
    int32_t args[3] = {nClientID, nBufferSize, nBlockOnRead};
    uint32_t cap = (fpchAPIMessage && nBufferSize > 0) ? (uint32_t)nBufferSize : 0;
    call(OP_READ_MESSAGE, args, 3, NULL, 0, &resp, fpchAPIMessage, cap);
    /* ReadMessage reports errors as negative values; a positive value with no
     * data can only be a bridge or host error code. */
    if (resp.ret > 0 && resp.len == 0) return (short)-resp.ret;
    return (short)resp.ret;
}

EXPORT short WINAPI RP1210_SendCommand(short nCommandNumber, short nClientID, unsigned char *fpchClientCommand,
                                       short nMessageSize)
{
    BridgeResponse resp;
    int has_buffer = fpchClientCommand != NULL;
    uint32_t len = (has_buffer && nMessageSize > 0) ? (uint32_t)nMessageSize : 0;
    int32_t args[4] = {nCommandNumber, nClientID, nMessageSize, has_buffer};
    call(OP_SEND_COMMAND, args, 4, fpchClientCommand, len, &resp, fpchClientCommand, len);
    return (short)resp.ret;
}

EXPORT void WINAPI RP1210_ReadVersion(char *fpchDLLMajorVersion, char *fpchDLLMinorVersion,
                                      char *fpchAPIMajorVersion, char *fpchAPIMinorVersion)
{
    BridgeResponse resp;
    char v[4] = {0, 0, 0, 0};
    call(OP_READ_VERSION, NULL, 0, NULL, 0, &resp, v, sizeof(v));
    if (fpchDLLMajorVersion) *fpchDLLMajorVersion = v[0];
    if (fpchDLLMinorVersion) *fpchDLLMinorVersion = v[1];
    if (fpchAPIMajorVersion) *fpchAPIMajorVersion = v[2];
    if (fpchAPIMinorVersion) *fpchAPIMinorVersion = v[3];
}

EXPORT short WINAPI RP1210_ReadDetailedVersion(short nClientID, char *fpchAPIVersionInfo, char *fpchDLLVersionInfo,
                                               char *fpchFWVersionInfo)
{
    BridgeResponse resp;
    char f[3 * BRIDGE_VERSION_FIELD];
    int32_t args[1] = {nClientID};
    memset(f, 0, sizeof(f));
    call(OP_READ_DETAILED_VERSION, args, 1, NULL, 0, &resp, f, sizeof(f));
    if (fpchAPIVersionInfo) memcpy(fpchAPIVersionInfo, f, BRIDGE_VERSION_FIELD);
    if (fpchDLLVersionInfo) memcpy(fpchDLLVersionInfo, f + BRIDGE_VERSION_FIELD, BRIDGE_VERSION_FIELD);
    if (fpchFWVersionInfo) memcpy(fpchFWVersionInfo, f + 2 * BRIDGE_VERSION_FIELD, BRIDGE_VERSION_FIELD);
    return (short)resp.ret;
}

EXPORT short WINAPI RP1210_GetHardwareStatus(short nClientID, unsigned char *fpchClientInfo, short nInfoSize,
                                             short nBlockOnRequest)
{
    BridgeResponse resp;
    int32_t args[3] = {nClientID, nInfoSize, nBlockOnRequest};
    uint32_t cap = (fpchClientInfo && nInfoSize > 0) ? (uint32_t)nInfoSize : 0;
    call(OP_GET_HARDWARE_STATUS, args, 3, NULL, 0, &resp, fpchClientInfo, cap);
    return (short)resp.ret;
}

EXPORT short WINAPI RP1210_GetHardwareStatusEx(short nClientID, unsigned char *fpchClientInfo)
{
    BridgeResponse resp;
    int32_t args[1] = {nClientID};
    call(OP_GET_HARDWARE_STATUS_EX, args, 1, NULL, 0, &resp, fpchClientInfo,
         fpchClientInfo ? BRIDGE_HWSTATUS_EX_SIZE : 0);
    return (short)resp.ret;
}

EXPORT short WINAPI RP1210_GetErrorMsg(short errorCode, char *fpchDescription)
{
    BridgeResponse resp;
    char text[BRIDGE_ERROR_TEXT_SIZE];
    int32_t args[1] = {errorCode};
    memset(text, 0, sizeof(text));
    if (!call(OP_GET_ERROR_MSG, args, 1, NULL, 0, &resp, text, sizeof(text))) {
        /* Host unavailable: describe the bridge failure instead. */
        strncpy_s(text, sizeof(text), g_last_error[0] ? g_last_error : "RP1210 bridge not initialized", _TRUNCATE);
        resp.ret = NO_ERRORS;
    }
    text[sizeof(text) - 1] = '\0';
    if (fpchDescription) memcpy(fpchDescription, text, sizeof(text));
    return (short)resp.ret;
}

EXPORT short WINAPI RP1210_GetLastErrorMsg(short errorCode, int *SubErrorCode, char *fpchDescription,
                                           short nClientID)
{
    BridgeResponse resp;
    char text[BRIDGE_ERROR_TEXT_SIZE];
    int32_t args[2] = {errorCode, nClientID};
    memset(text, 0, sizeof(text));
    call(OP_GET_LAST_ERROR_MSG, args, 2, NULL, 0, &resp, text, sizeof(text));
    text[sizeof(text) - 1] = '\0';
    if (SubErrorCode) *SubErrorCode = resp.extra;
    if (fpchDescription) memcpy(fpchDescription, text, sizeof(text));
    return (short)resp.ret;
}

EXPORT short WINAPI RP1210_Ioctl(short nClientID, long nIoctlID, void *pInput, void *pOutput)
{
    (void)nClientID; (void)nIoctlID; (void)pInput; (void)pOutput;
    return ERR_COMMAND_NOT_SUPPORTED;
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    switch (reason) {
    case DLL_PROCESS_ATTACH:
        g_self = hinst;
        InitializeCriticalSection(&g_lock);
        break;
    case DLL_THREAD_DETACH:
        drop_thread_pipe();
        break;
    case DLL_PROCESS_DETACH:
        drop_thread_pipe();
        /* On FreeLibrary (reserved == NULL) stop the host; at process exit the
         * host's parent watchdog ends it. */
        if (reserved == NULL && g_host) {
            TerminateProcess(g_host, 0);
            CloseHandle(g_host);
            g_host = NULL;
        }
        break;
    }
    return TRUE;
}
