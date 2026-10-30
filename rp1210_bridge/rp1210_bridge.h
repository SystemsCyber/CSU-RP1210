/*
 * RP1210 32-to-64-bit bridge: definitions shared by
 *   rp1210_bridge64.dll  (x64, loaded by 64-bit RP1210 applications)
 *   rp1210_host32.exe    (x86, loads the vendor's 32-bit RP1210 DLL)
 *
 * A 64-bit process cannot load a 32-bit DLL, so every RP1210 call is
 * forwarded over a local named pipe to the 32-bit host process. Each
 * calling thread gets its own pipe connection, so a blocking
 * RP1210_ReadMessage in one thread never stalls sends in another.
 *
 * All wire structures use fixed-width types and are packed, so the layout
 * is identical in both bitnesses.
 *
 * Based on the RP1210 shim approach in SystemsCyber/ShimDLL (MIT).
 */
#ifndef RP1210_BRIDGE_H
#define RP1210_BRIDGE_H

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>

#define BRIDGE_PROTOCOL_VERSION 1
#define BRIDGE_MAX_PAYLOAD      65536
#define BRIDGE_PIPE_BUFFER      (BRIDGE_MAX_PAYLOAD + 64)
#define BRIDGE_HOST_EXE         "rp1210_host32.exe"
#define BRIDGE_ERROR_TEXT_SIZE  80   /* RP1210 description buffers are 80 characters */
#define BRIDGE_VERSION_FIELD    17   /* RP1210_ReadDetailedVersion buffers */
#define BRIDGE_HWSTATUS_EX_SIZE 256

enum BridgeOp {
    OP_HELLO = 1,
    OP_CLIENT_CONNECT,
    OP_CLIENT_DISCONNECT,
    OP_SEND_MESSAGE,
    OP_READ_MESSAGE,
    OP_SEND_COMMAND,
    OP_READ_VERSION,
    OP_READ_DETAILED_VERSION,
    OP_GET_HARDWARE_STATUS,
    OP_GET_HARDWARE_STATUS_EX,
    OP_GET_ERROR_MSG,
    OP_GET_LAST_ERROR_MSG
};

#pragma pack(push, 1)
typedef struct {
    uint32_t op;       /* enum BridgeOp */
    int32_t  arg[6];   /* scalar arguments, meaning depends on op */
    uint32_t len;      /* payload bytes that follow */
} BridgeRequest;

typedef struct {
    int32_t  ret;      /* RP1210 return value */
    int32_t  extra;    /* e.g. sub-error code, DLL-loaded flag */
    uint32_t len;      /* payload bytes that follow */
} BridgeResponse;
#pragma pack(pop)

/* ---- RP1210 return codes used by the bridge (RP1210C) ---- */
#define NO_ERRORS                   0
#define ERR_DLL_NOT_INITIALIZED     128
#define ERR_CLIENT_ALREADY_CONNECTED 130
#define ERR_HARDWARE_NOT_RESPONDING 142
#define ERR_COMMAND_NOT_SUPPORTED   143

/* ---- RP1210 function types (WINAPI = __stdcall on x86) ---- */
typedef short (WINAPI *PFN_CLIENTCONNECT)(HWND, short, const char *, long, long, short);
typedef short (WINAPI *PFN_CLIENTDISCONNECT)(short);
typedef short (WINAPI *PFN_SENDMESSAGE)(short, unsigned char *, short, short, short);
typedef short (WINAPI *PFN_READMESSAGE)(short, unsigned char *, short, short);
typedef short (WINAPI *PFN_SENDCOMMAND)(short, short, unsigned char *, short);
typedef void  (WINAPI *PFN_READVERSION)(char *, char *, char *, char *);
typedef short (WINAPI *PFN_READDETAILEDVERSION)(short, char *, char *, char *);
typedef short (WINAPI *PFN_GETHARDWARESTATUS)(short, unsigned char *, short, short);
typedef short (WINAPI *PFN_GETHARDWARESTATUSEX)(short, unsigned char *);
typedef short (WINAPI *PFN_GETERRORMSG)(short, char *);
typedef short (WINAPI *PFN_GETLASTERRORMSG)(short, int *, char *, short);

#endif /* RP1210_BRIDGE_H */
