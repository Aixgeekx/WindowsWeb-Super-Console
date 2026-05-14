// sysinfo.cpp — Fast system info collection via Windows APIs
// Compile: cl /LD /O2 sysinfo.cpp /Fe:sysinfo.dll /link /EXPORT:get_system_info
// Or:      g++ -shared -O2 -static -o sysinfo.dll sysinfo.cpp -ladvapi32 -lole32 -luuid
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <psapi.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <string.h>

#pragma comment(lib, "psapi.lib")

static ULARGE_INTEGER s_prevIdle, s_prevKernel, s_prevUser;
static BOOL s_cpuInit = FALSE;
static wchar_t s_gpuName[256] = L"N/A";
static BOOL s_gpuCached = FALSE;
static SRWLOCK s_lock = SRWLOCK_INIT;

// Helper: append to buffer safely
static int app(char* buf, int sz, int pos, const char* fmt, ...) {
    if (pos >= sz) return sz;
    va_list args; va_start(args, fmt);
    int n = _vsnprintf(buf + pos, sz - pos, fmt, args);
    va_end(args);
    return (n > 0) ? pos + n : pos;
}

// CPU usage via GetSystemTimes delta
static double get_cpu() {
    FILETIME idle, kernel, user;
    if (!GetSystemTimes(&idle, &kernel, &user)) return 0.0;
    auto toUL = [](const FILETIME& ft) -> ULARGE_INTEGER {
        ULARGE_INTEGER u; u.LowPart = ft.dwLowDateTime; u.HighPart = ft.dwHighDateTime; return u;
    };
    ULARGE_INTEGER i = toUL(idle), k = toUL(kernel), u = toUL(user);
    if (!s_cpuInit) {
        s_prevIdle = i; s_prevKernel = k; s_prevUser = u;
        s_cpuInit = TRUE; return 0.0;
    }
    ULONGLONG dIdle = i.QuadPart - s_prevIdle.QuadPart;
    ULONGLONG dKernel = k.QuadPart - s_prevKernel.QuadPart;
    ULONGLONG dUser = u.QuadPart - s_prevUser.QuadPart;
    s_prevIdle = i; s_prevKernel = k; s_prevUser = u;
    ULONGLONG dTotal = dKernel + dUser;
    return (dTotal == 0) ? 0.0 : (double)(dTotal - dIdle) / (double)dTotal * 100.0;
}

// Memory info
static void get_mem(double* usedGB, double* totalGB, double* freeGB, double* pct) {
    MEMORYSTATUSEX ms; ms.dwLength = sizeof(ms);
    GlobalMemoryStatusEx(&ms);
    *totalGB = (double)ms.ullTotalPhys / (1073741824.0);
    *freeGB = (double)ms.ullAvailPhys / (1073741824.0);
    *usedGB = *totalGB - *freeGB;
    *pct = (double)ms.dwMemoryLoad;
}

// Disk info
struct DiskInfo { char drive[8]; double used, total, free, pct; };
static int get_disks(DiskInfo* out, int maxOut) {
    DWORD drives = GetLogicalDrives();
    int count = 0;
    for (int i = 0; i < 26 && count < maxOut; i++) {
        if (!(drives & (1 << i))) continue;
        char root[4] = { (char)('A' + i), ':', '\\', 0 };
        ULARGE_INTEGER freeBytes, totalBytes, totalFree;
        if (!GetDiskFreeSpaceExA(root, &freeBytes, &totalBytes, &totalFree)) continue;
        double tot = (double)totalBytes.QuadPart / 1073741824.0;
        double fre = (double)totalFree.QuadPart / 1073741824.0;
        if (tot < 0.1) continue;
        DiskInfo& d = out[count++];
        d.drive[0] = 'A' + i; d.drive[1] = ':'; d.drive[2] = 0;
        d.total = tot; d.free = fre; d.used = tot - fre;
        d.pct = (tot > 0) ? (d.used / tot * 100.0) : 0.0;
    }
    return count;
}

// Uptime
static void get_uptime(char* buf, int sz) {
    ULONGLONG ms = GetTickCount64();
    DWORD sec = (DWORD)(ms / 1000);
    DWORD days = sec / 86400; sec %= 86400;
    DWORD hours = sec / 3600; sec %= 3600;
    DWORD mins = sec / 60;
    if (days > 0) _snprintf(buf, sz, "%ud %uh", days, hours);
    else if (hours > 0) _snprintf(buf, sz, "%uh %um", hours, mins);
    else _snprintf(buf, sz, "%um", mins);
}

// Process list (top by CPU)
struct ProcInfo { char name[64]; double cpu; double memMB; };
static int get_top_procs(ProcInfo* out, int maxOut) {
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return 0;
    PROCESSENTRY32W pe; pe.dwSize = sizeof(pe);
    int count = 0;
    if (Process32FirstW(snap, &pe)) {
        do {
            if (count >= maxOut) break;
            ProcInfo& p = out[count];
            // Copy name (truncate)
            WideCharToMultiByte(CP_UTF8, 0, pe.szExeFile, -1, p.name, sizeof(p.name), NULL, NULL);
            p.name[sizeof(p.name)-1] = 0;
            // Get memory usage
            HANDLE hProc = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, FALSE, pe.th32ProcessID);
            if (hProc) {
                PROCESS_MEMORY_COUNTERS pmc;
                if (GetProcessMemoryInfo(hProc, &pmc, sizeof(pmc))) {
                    p.memMB = (double)pmc.WorkingSetSize / 1048576.0;
                } else { p.memMB = 0; }
                // Get CPU time
                FILETIME creation, exit, kernel, user;
                if (GetProcessTimes(hProc, &creation, &exit, &kernel, &user)) {
                    ULARGE_INTEGER k, u;
                    k.LowPart = kernel.dwLowDateTime; k.HighPart = kernel.dwHighDateTime;
                    u.LowPart = user.dwLowDateTime; u.HighPart = user.dwHighDateTime;
                    p.cpu = (double)((k.QuadPart + u.QuadPart) / 10000.0); // ms
                } else { p.cpu = 0; }
                CloseHandle(hProc);
            } else { p.memMB = 0; p.cpu = 0; }
            count++;
        } while (Process32NextW(snap, &pe));
    }
    CloseHandle(snap);
    // Sort by CPU descending
    for (int i = 0; i < count - 1; i++)
        for (int j = i + 1; j < count; j++)
            if (out[j].cpu > out[i].cpu) { ProcInfo t = out[i]; out[i] = out[j]; out[j] = t; }
    return count;
}

// GPU name (cached, queried once)
static const wchar_t* get_gpu() {
    if (s_gpuCached) return s_gpuName;
    DISPLAY_DEVICEA dd; dd.cb = sizeof(dd);
    for (int i = 0; EnumDisplayDevicesA(NULL, i, &dd, 0); i++) {
        if (!(dd.StateFlags & DISPLAY_DEVICE_ACTIVE)) continue;
        // Skip virtual/remote adapters
        if (strstr(dd.DeviceString, "Oray") || strstr(dd.DeviceString, "Remote") ||
            strstr(dd.DeviceString, "Virtual") || strstr(dd.DeviceString, "Basic")) continue;
        // Convert to wide char
        MultiByteToWideChar(CP_UTF8, 0, dd.DeviceString, -1, s_gpuName, 256);
        s_gpuCached = TRUE;
        return s_gpuName;
    }
    s_gpuCached = TRUE;
    return s_gpuName;
}

extern "C" __declspec(dllexport) int __cdecl get_system_info(char* buf, int bufSize) {
    AcquireSRWLockExclusive(&s_lock);
    int pos = 0;

    // CPU
    double cpu = get_cpu();
    pos = app(buf, bufSize, pos, "{\"cpu\":%.1f,", cpu);

    // Memory
    double usedGB, totalGB, freeGB, memPct;
    get_mem(&usedGB, &totalGB, &freeGB, &memPct);
    pos = app(buf, bufSize, pos, "\"mem\":{\"used\":%.2f,\"total\":%.2f,\"free\":%.2f,\"percent\":%.1f},",
              usedGB, totalGB, freeGB, memPct);

    // Disks
    DiskInfo disks[26];
    int nDisks = get_disks(disks, 26);
    pos = app(buf, bufSize, pos, "\"disks\":[");
    for (int i = 0; i < nDisks; i++) {
        if (i > 0) pos = app(buf, bufSize, pos, ",");
        pos = app(buf, bufSize, pos, "{\"drive\":\"%s\",\"used\":%.1f,\"total\":%.1f,\"free\":%.1f,\"pct\":%.1f}",
                  disks[i].drive, disks[i].used, disks[i].total, disks[i].free, disks[i].pct);
    }
    pos = app(buf, bufSize, pos, "],");

    // Uptime
    char uptimeStr[32]; get_uptime(uptimeStr, sizeof(uptimeStr));
    pos = app(buf, bufSize, pos, "\"uptime\":\"%s\",", uptimeStr);

    // Processes
    ProcInfo procs[64];
    int nProcs = get_top_procs(procs, 64);
    pos = app(buf, bufSize, pos, "\"procs\":%d,\"top\":[", nProcs);
    for (int i = 0; i < nProcs && i < 8; i++) {
        if (i > 0) pos = app(buf, bufSize, pos, ",");
        pos = app(buf, bufSize, pos, "{\"name\":\"%s\",\"cpu\":%.1f,\"mem_mb\":%.1f}",
                  procs[i].name, procs[i].cpu, procs[i].memMB);
    }
    pos = app(buf, bufSize, pos, "],");

    // GPU
    const wchar_t* gpu = get_gpu();
    char gpuUtf8[512];
    WideCharToMultiByte(CP_UTF8, 0, gpu, -1, gpuUtf8, sizeof(gpuUtf8), NULL, NULL);
    pos = app(buf, bufSize, pos, "\"gpu\":\"%s\"}", gpuUtf8);

    ReleaseSRWLockExclusive(&s_lock);

    // Ensure null-terminated
    if (pos < bufSize) buf[pos] = 0; else buf[bufSize-1] = 0;
    return 0;
}

// Screenshot via GDI+ — returns base64 JPEG, 0=success
extern "C" __declspec(dllexport) int take_screenshot(char* buf, int bufSize) {
    typedef BOOL (WINAPI *SetProcessDPIAware_t)();
    HMODULE hU32 = GetModuleHandleW(L"user32.dll");
    if (hU32) { auto p = (SetProcessDPIAware_t)GetProcAddress(hU32, "SetProcessDPIAware"); if (p) p(); }

    int x = GetSystemMetrics(76), y = GetSystemMetrics(77);
    int w = GetSystemMetrics(78), h = GetSystemMetrics(79);
    if (w <= 0 || h <= 0) { w = GetSystemMetrics(0); h = GetSystemMetrics(1); x = y = 0; }

    HDC hScreen = GetDC(NULL);
    HDC hMem = CreateCompatibleDC(hScreen);
    HBITMAP hBmp = CreateCompatibleBitmap(hScreen, w, h);
    HBITMAP hOld = (HBITMAP)SelectObject(hMem, hBmp);
    BitBlt(hMem, 0, 0, w, h, hScreen, x, y, SRCCOPY);
    SelectObject(hMem, hOld);

    // Init GDI+
    typedef int (WINAPI *GdiplusStartup_t)(ULONG_PTR*, void*, void*);
    typedef void (WINAPI *GdiplusShutdown_t)(ULONG_PTR);
    typedef int (WINAPI *GdipCreateBitmapFromHBITMAP_t)(HBITMAP, HPALETTE, void**);
    typedef int (WINAPI *GdipGetImageEncodersSize_t)(UINT*, UINT*);
    typedef int (WINAPI *GdipGetImageEncoders_t)(UINT, UINT, void*);
    typedef int (WINAPI *GdipSaveImageToStream_t)(void*, void*, const wchar_t*, void*);
    typedef int (WINAPI *GdipCreateStreamOnMemory_t)(BYTE*, int, void**);
    typedef int (WINAPI *GdipDisposeImage_t)(void*);
    typedef int (WINAPI *GdipCreateHBITMAPFromBitmap_t)(void*, HBITMAP*, unsigned int);

    HMODULE hGdip = LoadLibraryW(L"gdiplus.dll");
    if (!hGdip) { DeleteObject(hBmp); DeleteDC(hMem); ReleaseDC(NULL, hScreen); buf[0] = 0; return -1; }

    auto pStartup = (GdiplusStartup_t)GetProcAddress(hGdip, "GdiplusStartup");
    auto pShutdown = (GdiplusShutdown_t)GetProcAddress(hGdip, "GdiplusShutdown");
    auto pCreateBmp = (GdipCreateBitmapFromHBITMAP_t)GetProcAddress(hGdip, "GdipCreateBitmapFromHBITMAP");
    auto pEncSize = (GdipGetImageEncodersSize_t)GetProcAddress(hGdip, "GdipGetImageEncodersSize");
    auto pEncoders = (GdipGetImageEncoders_t)GetProcAddress(hGdip, "GdipGetImageEncoders");
    auto pSave = (GdipSaveImageToStream_t)GetProcAddress(hGdip, "GdipSaveImageToStream");
    auto pCreateStream = (GdipCreateStreamOnMemory_t)GetProcAddress(hGdip, "GdipCreateStreamOnMemory");
    auto pDispose = (GdipDisposeImage_t)GetProcAddress(hGdip, "GdipDisposeImage");

    ULONG_PTR gdiplusToken;
    char startupBuf[24] = {0};
    pStartup(&gdiplusToken, startupBuf, NULL);

    void* pGdipBmp = NULL;
    pCreateBmp(hBmp, NULL, &pGdipBmp);

    // Find JPEG encoder
    UINT num = 0, size = 0;
    pEncSize(&num, &size);
    void* encoders = malloc(size);
    pEncoders(num, size, encoders);
    CLSID jpegClsid = {0};
    for (UINT i = 0; i < num; i++) {
        wchar_t* mime = (wchar_t*)((BYTE*)encoders + i * (76 + 48*2) + 48);
        if (wcsstr(mime, L"image/jpeg")) {
            memcpy(&jpegClsid, (BYTE*)encoders + i * (76 + 48*2), 16);
            break;
        }
    }
    free(encoders);

    // Save to IStream in memory
    void* pStream = NULL;
    pCreateStream(NULL, 0, &pStream);
    int quality = 60;
    // Encoder parameter: quality
    struct { GUID Guid; ULONG NumberOfValues; ULONG Type; void* Value; } param;
    GUID qualityParam = {0x1d5be4b5, 0xfa4a, 0x452d, {0x9c,0xdd,0x5a,0x38,0x29,0x41,0x93,0x25}};
    param.Guid = qualityParam; param.NumberOfValues = 1; param.Type = 1; param.Value = &quality;
    pSave(pGdipBmp, pStream, L"image/jpeg", &param);

    // Read from IStream
    STATSTG stat;
    pStream->lpVtbl->Stat(pStream, &stat, 1);
    ULONG len = (ULONG)stat.cbSize.QuadPart;
    if (len > 0 && len < (ULONG)(bufSize - 8)) {
        IStream* pSeek = pStream;
        LARGE_INTEGER zero = {0};
        pSeek->lpVtbl->Seek(pSeek, zero, STREAM_SEEK_SET, NULL);
        ULONG read = 0;
        pSeek->lpVtbl->Read(pSeek, (void*)buf, len, &read);
        // Base64 encode
        const char* tbl = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        int o = 0;
        for (ULONG i = 0; i < len; i += 3) {
            unsigned int n = ((unsigned char)buf[i]) << 16;
            if (i + 1 < len) n |= ((unsigned char)buf[i+1]) << 8;
            if (i + 2 < len) n |= ((unsigned char)buf[i+2]);
            buf[o++] = tbl[(n >> 18) & 63];
            buf[o++] = tbl[(n >> 12) & 63];
            buf[o++] = (i + 1 < len) ? tbl[(n >> 6) & 63] : '=';
            buf[o++] = (i + 2 < len) ? tbl[n & 63] : '=';
        }
        buf[o] = 0;
    } else {
        buf[0] = 0; len = 0;
    }

    pStream->lpVtbl->Release(pStream);
    pDispose(pGdipBmp);
    pShutdown(gdiplusToken);
    FreeLibrary(hGdip);
    DeleteObject(hBmp); DeleteDC(hMem); ReleaseDC(NULL, hScreen);
    return (len > 0) ? 0 : -1;
}
