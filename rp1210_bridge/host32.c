/*
 * rp1210_host32.exe - 32-bit side of the RP1210 bridge.
 *
 * Usage (started by rp1210_bridge64.dll, not by hand):
 *     rp1210_host32.exe <pipe name> <vendor DLL name or path> <parent pid>
 *
 * Loads the vendor's 32-bit RP1210 DLL and serves RP1210 calls on a named
 * pipe. Each pipe connection (one per calling thread in the 64-bit app) is
 * served by its own thread. Exits when the parent process exits.
 * Set RP1210_BRIDGE_LOG=<file> to log every call.
 */
#include "rp1210_bridge.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static HMODULE g_dll;
static char g_status[512];
static char g_pipe_name[256];
static FILE *g_log;
static CRITICAL_SECTION g_log_lock;

static PFN_CLIENTCONNECT       p_ClientConnect;
static PFN_CLIENTDISCONNECT    p_ClientDisconnect;
static PFN_SENDMESSAGE         p_SendMessage;
static PFN_READMESSAGE         p_ReadMessage;
static PFN_SENDCOMMAND         p_SendCommand;
static PFN_READVERSION         p_ReadVersion;
static PFN_READDETAILEDVERSION p_ReadDetailedVersion;
static PFN_GETHARDWARESTATUS   p_GetHardwareStatus;
static PFN_GETHARDWARESTATUSEX p_GetHardwareStatusEx;
static PFN_GETERRORMSG         p_GetErrorMsg;
static PFN_GETLASTERRORMSG     p_GetLastErrorMsg;

static void logf_(const char *fmt, ...)
{
    va_list ap;
    if (!g_log) return;
    EnterCriticalSection(&g_log_lock);
    fprintf(g_log, "[%lu] ", GetCurrentThreadId());
    va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fputc('\n', g_log);
    fflush(g_log);
    LeaveCriticalSection(&g_log_lock);
}

/* Load by full path so a renamed 64-bit bridge DLL sitting next to this exe is
 * never picked up: SysWOW64 first, then the Windows directory, then the
 * normal search order. */
static void load_vendor_dll(const char *target)
{
    char path[MAX_PATH];
    char name[MAX_PATH];
    char dir[MAX_PATH];
    DWORD err = 0;

    if (strchr(target, '\\') || strchr(target, '/')) {
        g_dll = LoadLibraryExA(target, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
        strncpy_s(path, sizeof(path), target, _TRUNCATE);
        err = GetLastError();
    } else {
        size_t n = strlen(target);
        if (n > 4 && _stricmp(target + n - 4, ".dll") == 0)
            strncpy_s(name, sizeof(name), target, _TRUNCATE);
        else
            _snprintf_s(name, sizeof(name), _TRUNCATE, "%s.dll", target);

        path[0] = '\0';
        if (GetSystemWow64DirectoryA(dir, sizeof(dir)) > 0) {
            _snprintf_s(path, sizeof(path), _TRUNCATE, "%s\\%s", dir, name);
            g_dll = LoadLibraryExA(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
            err = GetLastError();
        }
        if (!g_dll && GetWindowsDirectoryA(dir, sizeof(dir)) > 0) {
            _snprintf_s(path, sizeof(path), _TRUNCATE, "%s\\%s", dir, name);
            g_dll = LoadLibraryExA(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
            err = GetLastError();
        }
        if (!g_dll) {
            strncpy_s(path, sizeof(path), name, _TRUNCATE);
            g_dll = LoadLibraryA(name);
            err = GetLastError();
        }
    }

    if (!g_dll) {
        _snprintf_s(g_status, sizeof(g_status), _TRUNCATE,
                    "RP1210 bridge: cannot load 32-bit %s (Windows error %lu)", target, err);
        logf_("%s", g_status);
        return;
    }
    GetModuleFileNameA(g_dll, path, sizeof(path));
    _snprintf_s(g_status, sizeof(g_status), _TRUNCATE, "RP1210 bridge: loaded %s", path);
    logf_("%s", g_status);

#define LOAD(var, type, sym) var = (type)(void *)GetProcAddress(g_dll, sym)
    LOAD(p_ClientConnect, PFN_CLIENTCONNECT, "RP1210_ClientConnect");
    LOAD(p_ClientDisconnect, PFN_CLIENTDISCONNECT, "RP1210_ClientDisconnect");
    LOAD(p_SendMessage, PFN_SENDMESSAGE, "RP1210_SendMessage");
    LOAD(p_ReadMessage, PFN_READMESSAGE, "RP1210_ReadMessage");
    LOAD(p_SendCommand, PFN_SENDCOMMAND, "RP1210_SendCommand");
    LOAD(p_ReadVersion, PFN_READVERSION, "RP1210_ReadVersion");
    LOAD(p_ReadDetailedVersion, PFN_READDETAILEDVERSION, "RP1210_ReadDetailedVersion");
    LOAD(p_GetHardwareStatus, PFN_GETHARDWARESTATUS, "RP1210_GetHardwareStatus");
    LOAD(p_GetHardwareStatusEx, PFN_GETHARDWARESTATUSEX, "RP1210_GetHardwareStatusEx");
    LOAD(p_GetErrorMsg, PFN_GETERRORMSG, "RP1210_GetErrorMsg");
    LOAD(p_GetLastErrorMsg, PFN_GETLASTERRORMSG, "RP1210_GetLastErrorMsg");
#undef LOAD
}

/* Execute one request; fills resp and out (up to BRIDGE_MAX_PAYLOAD bytes). */
static void dispatch(const BridgeRequest *req, unsigned char *payload, BridgeResponse *resp, unsigned char *out)
{
    const int32_t *a = req->arg;
    resp->ret = ERR_COMMAND_NOT_SUPPORTED;
    resp->extra = 0;
    resp->len = 0;

    if (req->op == OP_HELLO) {
        size_t n = strlen(g_status) + 1;
        resp->ret = BRIDGE_PROTOCOL_VERSION;
        resp->extra = g_dll != NULL;
        memcpy(out, g_status, n);
        resp->len = (uint32_t)n;
        return;
    }
    if (!g_dll) {
        resp->ret = ERR_DLL_NOT_INITIALIZED;
        return;
    }

    switch (req->op) {
    case OP_CLIENT_CONNECT:
        if (p_ClientConnect) {
            payload[req->len ? req->len - 1 : 0] = '\0';
            resp->ret = p_ClientConnect(NULL, (short)a[0], (const char *)payload, (long)a[1], (long)a[2], (short)a[3]);
        }
        logf_("ClientConnect(device=%d, \"%s\") -> %d", a[0], (const char *)payload, resp->ret);
        break;

    case OP_CLIENT_DISCONNECT:
        if (p_ClientDisconnect) resp->ret = p_ClientDisconnect((short)a[0]);
        logf_("ClientDisconnect(%d) -> %d", a[0], resp->ret);
        break;

    case OP_SEND_MESSAGE:
        if (p_SendMessage)
            resp->ret = p_SendMessage((short)a[0], payload, (short)a[1], (short)a[2], (short)a[3]);
        break;

    case OP_READ_MESSAGE: {
        int32_t size = a[1];
        if (size < 0) size = 0;
        if (size > BRIDGE_MAX_PAYLOAD) size = BRIDGE_MAX_PAYLOAD;
        if (p_ReadMessage) {
            resp->ret = p_ReadMessage((short)a[0], out, (short)size, (short)a[2]);
            if (resp->ret > 0) resp->len = (uint32_t)(resp->ret <= size ? resp->ret : size);
        }
        break;
    }

    case OP_SEND_COMMAND: {
        /* The command buffer is copied back: some commands return data in it. */
        int32_t size = a[2] < 0 ? 0 : a[2];
        int has_buffer = a[3];
        if (p_SendCommand) {
            memcpy(out, payload, req->len);
            resp->ret = p_SendCommand((short)a[0], (short)a[1], has_buffer ? out : NULL, (short)size);
            if (has_buffer) resp->len = req->len;
        }
        logf_("SendCommand(%d, client=%d, size=%d) -> %d", a[0], a[1], size, resp->ret);
        break;
    }

    case OP_READ_VERSION: {
        char v[4][32];
        memset(v, 0, sizeof(v));
        if (p_ReadVersion) {
            p_ReadVersion(v[0], v[1], v[2], v[3]);
            resp->ret = NO_ERRORS;
        }
        out[0] = (unsigned char)v[0][0];
        out[1] = (unsigned char)v[1][0];
        out[2] = (unsigned char)v[2][0];
        out[3] = (unsigned char)v[3][0];
        resp->len = 4;
        break;
    }

    case OP_READ_DETAILED_VERSION:
        memset(out, 0, 3 * BRIDGE_VERSION_FIELD);
        if (p_ReadDetailedVersion) {
            char f[3][64];
            memset(f, 0, sizeof(f));
            resp->ret = p_ReadDetailedVersion((short)a[0], f[0], f[1], f[2]);
            for (int i = 0; i < 3; i++) {
                memcpy(out + i * BRIDGE_VERSION_FIELD, f[i], BRIDGE_VERSION_FIELD - 1);
            }
        }
        resp->len = 3 * BRIDGE_VERSION_FIELD;
        break;

    case OP_GET_HARDWARE_STATUS: {
        int32_t size = a[1];
        if (size < 0) size = 0;
        if (size > BRIDGE_MAX_PAYLOAD) size = BRIDGE_MAX_PAYLOAD;
        memset(out, 0, (size_t)size);
        if (p_GetHardwareStatus) resp->ret = p_GetHardwareStatus((short)a[0], out, (short)size, (short)a[2]);
        resp->len = (uint32_t)size;
        break;
    }

    case OP_GET_HARDWARE_STATUS_EX:
        memset(out, 0, BRIDGE_HWSTATUS_EX_SIZE);
        if (p_GetHardwareStatusEx) resp->ret = p_GetHardwareStatusEx((short)a[0], out);
        resp->len = BRIDGE_HWSTATUS_EX_SIZE;
        break;

    case OP_GET_ERROR_MSG: {
        char text[256];
        memset(text, 0, sizeof(text));
        if (p_GetErrorMsg) resp->ret = p_GetErrorMsg((short)a[0], text);
        text[BRIDGE_ERROR_TEXT_SIZE - 1] = '\0';
        memcpy(out, text, BRIDGE_ERROR_TEXT_SIZE);
        resp->len = BRIDGE_ERROR_TEXT_SIZE;
        break;
    }

    case OP_GET_LAST_ERROR_MSG: {
        char text[256];
        int sub = -1;
        memset(text, 0, sizeof(text));
        if (p_GetLastErrorMsg) resp->ret = p_GetLastErrorMsg((short)a[0], &sub, text, (short)a[1]);
        text[BRIDGE_ERROR_TEXT_SIZE - 1] = '\0';
        memcpy(out, text, BRIDGE_ERROR_TEXT_SIZE);
        resp->extra = sub;
        resp->len = BRIDGE_ERROR_TEXT_SIZE;
        break;
    }

    default:
        break;
    }
}

static DWORD WINAPI serve_client(LPVOID param)
{
    HANDLE pipe = (HANDLE)param;
    unsigned char *in = (unsigned char *)malloc(BRIDGE_PIPE_BUFFER);
    unsigned char *reply = (unsigned char *)malloc(BRIDGE_PIPE_BUFFER);

    while (in && reply) {
        DWORD got = 0, wrote = 0;
        BridgeRequest req;
        BridgeResponse resp;
        if (!ReadFile(pipe, in, BRIDGE_PIPE_BUFFER, &got, NULL) || got < sizeof(BridgeRequest)) break;
        memcpy(&req, in, sizeof(req));
        if (req.len > BRIDGE_MAX_PAYLOAD || got != sizeof(req) + req.len) break;
        /* NUL-pad the payload so string arguments are always terminated. */
        in[sizeof(req) + req.len] = '\0';
        dispatch(&req, in + sizeof(req), &resp, reply + sizeof(resp));
        memcpy(reply, &resp, sizeof(resp));
        if (!WriteFile(pipe, reply, (DWORD)(sizeof(resp) + resp.len), &wrote, NULL)) break;
    }
    free(in);
    free(reply);
    FlushFileBuffers(pipe);
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
    return 0;
}

static DWORD WINAPI watch_parent(LPVOID param)
{
    HANDLE parent = (HANDLE)param;
    WaitForSingleObject(parent, INFINITE);
    logf_("parent exited; host shutting down");
    ExitProcess(0);
}

int main(int argc, char **argv)
{
    const char *log_path;
    HANDLE parent;

    if (argc < 4) {
        fprintf(stderr, "usage: %s <pipe name> <vendor DLL> <parent pid>\n"
                        "Started by rp1210_bridge64.dll; not intended to be run by hand.\n", argv[0]);
        return 2;
    }
    InitializeCriticalSection(&g_log_lock);
    log_path = getenv("RP1210_BRIDGE_LOG");
    if (log_path && *log_path) fopen_s(&g_log, log_path, "a");

    strncpy_s(g_pipe_name, sizeof(g_pipe_name), argv[1], _TRUNCATE);
    parent = OpenProcess(SYNCHRONIZE, FALSE, (DWORD)strtoul(argv[3], NULL, 10));
    if (!parent) {
        logf_("parent process %s not found", argv[3]);
        return 3;
    }
    CloseHandle(CreateThread(NULL, 0, watch_parent, parent, 0, NULL));

    load_vendor_dll(argv[2]);

    for (;;) {
        HANDLE pipe = CreateNamedPipeA(g_pipe_name, PIPE_ACCESS_DUPLEX,
                                       PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                                       PIPE_UNLIMITED_INSTANCES, BRIDGE_PIPE_BUFFER, BRIDGE_PIPE_BUFFER, 0, NULL);
        if (pipe == INVALID_HANDLE_VALUE) {
            logf_("CreateNamedPipe failed: %lu", GetLastError());
            return 4;
        }
        if (ConnectNamedPipe(pipe, NULL) || GetLastError() == ERROR_PIPE_CONNECTED) {
            HANDLE t = CreateThread(NULL, 0, serve_client, pipe, 0, NULL);
            if (t) CloseHandle(t);
            else CloseHandle(pipe);
        } else {
            CloseHandle(pipe);
        }
    }
}
