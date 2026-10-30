/*
 * fake_rp1210.dll - a 32-bit loopback "vendor" RP1210 DLL for testing the
 * bridge without hardware. Every function returns deterministic values so
 * the marshalling of each argument and buffer can be checked exactly.
 *
 *   ClientConnect     protocol "CAN..." or "J1939..." -> client 1, 2, ...; else 136
 *   SendMessage       queues the bytes; ReadMessage returns them (loopback)
 *   ReadMessage       blocking reads wait until data or ClientDisconnect
 *   SendCommand 14    reverses the command buffer in place (copy-back test)
 *   ReadVersion       '7' '3' '1' 'C'
 *   GetHardwareStatus fills byte i with (i ^ 0x5A)
 *   GetErrorMsg       "fake error <code>"
 *   GetLastErrorMsg   sub-code = code + 1000
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <string.h>

#define MAX_CLIENTS 16
#define QUEUE_LEN   64
#define MSG_MAX     4200

typedef struct {
    int used;
    int connected;
    unsigned char msg[QUEUE_LEN][MSG_MAX];
    short len[QUEUE_LEN];
    int head, count;
} Client;

static Client g_clients[MAX_CLIENTS];
static CRITICAL_SECTION g_lock;
static CONDITION_VARIABLE g_cv;

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r)
{
    (void)h; (void)r;
    if (reason == DLL_PROCESS_ATTACH) {
        InitializeCriticalSection(&g_lock);
        InitializeConditionVariable(&g_cv);
    }
    return TRUE;
}

short WINAPI RP1210_ClientConnect(HWND hwnd, short device, const char *protocol, long tx, long rx, short app)
{
    (void)hwnd; (void)tx; (void)rx; (void)app;
    if (device != 1) return 134; /* ERR_INVALID_DEVICE */
    if (!protocol || (strncmp(protocol, "CAN", 3) != 0 && strncmp(protocol, "J1939", 5) != 0)) return 136;
    EnterCriticalSection(&g_lock);
    for (short i = 1; i < MAX_CLIENTS; i++) {
        if (!g_clients[i].used) {
            memset(&g_clients[i], 0, sizeof(Client));
            g_clients[i].used = g_clients[i].connected = 1;
            LeaveCriticalSection(&g_lock);
            return i;
        }
    }
    LeaveCriticalSection(&g_lock);
    return 131; /* ERR_CLIENT_AREA_FULL */
}

short WINAPI RP1210_ClientDisconnect(short id)
{
    if (id < 1 || id >= MAX_CLIENTS || !g_clients[id].used) return 129;
    EnterCriticalSection(&g_lock);
    g_clients[id].used = g_clients[id].connected = 0;
    WakeAllConditionVariable(&g_cv);
    LeaveCriticalSection(&g_lock);
    return 0;
}

short WINAPI RP1210_SendMessage(short id, unsigned char *msg, short size, short notify, short block)
{
    Client *c;
    (void)notify; (void)block;
    if (id < 1 || id >= MAX_CLIENTS || !g_clients[id].used) return 129;
    if (size < 0 || size > MSG_MAX) return 141;
    EnterCriticalSection(&g_lock);
    c = &g_clients[id];
    if (c->count == QUEUE_LEN) {
        LeaveCriticalSection(&g_lock);
        return 137; /* ERR_TX_QUEUE_FULL */
    }
    memcpy(c->msg[(c->head + c->count) % QUEUE_LEN], msg, size);
    c->len[(c->head + c->count) % QUEUE_LEN] = size;
    c->count++;
    WakeAllConditionVariable(&g_cv);
    LeaveCriticalSection(&g_lock);
    return 0;
}

short WINAPI RP1210_ReadMessage(short id, unsigned char *buf, short size, short block)
{
    Client *c;
    short n;
    if (id < 1 || id >= MAX_CLIENTS || !g_clients[id].used) return -129;
    EnterCriticalSection(&g_lock);
    c = &g_clients[id];
    while (block && c->connected && c->count == 0) SleepConditionVariableCS(&g_cv, &g_lock, INFINITE);
    if (!c->connected) {
        LeaveCriticalSection(&g_lock);
        return -148; /* ERR_CLIENT_DISCONNECTED */
    }
    if (c->count == 0) {
        LeaveCriticalSection(&g_lock);
        return 0;
    }
    n = c->len[c->head];
    if (n > size) {
        LeaveCriticalSection(&g_lock);
        return -141;
    }
    memcpy(buf, c->msg[c->head], n);
    c->head = (c->head + 1) % QUEUE_LEN;
    c->count--;
    LeaveCriticalSection(&g_lock);
    return n;
}

short WINAPI RP1210_SendCommand(short cmd, short id, unsigned char *buf, short size)
{
    (void)id;
    if (cmd == 14 && buf) {
        for (short i = 0; i < size / 2; i++) {
            unsigned char t = buf[i];
            buf[i] = buf[size - 1 - i];
            buf[size - 1 - i] = t;
        }
    }
    return cmd == 999 ? 144 : 0;
}

void WINAPI RP1210_ReadVersion(char *dmaj, char *dmin, char *amaj, char *amin)
{
    *dmaj = '7'; *dmin = '3'; *amaj = '1'; *amin = 'C';
}

short WINAPI RP1210_ReadDetailedVersion(short id, char *api, char *dll, char *fw)
{
    (void)id;
    strcpy_s(api, 17, "API-FAKE-1.0");
    strcpy_s(dll, 17, "DLL-FAKE-32BIT");
    strcpy_s(fw, 17, "FW-LOOPBACK");
    return 0;
}

short WINAPI RP1210_GetHardwareStatus(short id, unsigned char *info, short size, short block)
{
    (void)id; (void)block;
    for (short i = 0; i < size; i++) info[i] = (unsigned char)(i ^ 0x5A);
    return 0;
}

short WINAPI RP1210_GetHardwareStatusEx(short id, unsigned char *info)
{
    (void)id;
    for (int i = 0; i < 256; i++) info[i] = (unsigned char)(255 - i);
    return 0;
}

short WINAPI RP1210_GetErrorMsg(short code, char *text)
{
    sprintf_s(text, 80, "fake error %d", code);
    return 0;
}

short WINAPI RP1210_GetLastErrorMsg(short code, int *sub, char *text, short id)
{
    *sub = code + 1000;
    sprintf_s(text, 80, "fake last error %d on client %d", code, id);
    return 0;
}
