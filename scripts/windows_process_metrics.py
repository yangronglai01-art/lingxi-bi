"""Read-only Windows process counters; all opened handles are closed."""
import ctypes
from ctypes import wintypes
import sys

def snapshot(pid):
    if sys.platform!='win32':raise RuntimeError('Windows resource sampler required')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    psapi=ctypes.WinDLL('psapi',use_last_error=True)
    size=ctypes.c_size_t
    class Memory(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,size) for name in
            ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
             'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage']]
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    kernel.GetProcessHandleCount.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessHandleCount.restype=wintypes.BOOL
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.GetProcessTimes.restype=wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Memory),wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype=wintypes.BOOL
    process=kernel.OpenProcess(0x410,False,pid)
    if not process:raise ctypes.WinError(ctypes.get_last_error())
    try:
        memory=Memory();memory.cb=ctypes.sizeof(memory)
        handles=wintypes.DWORD()
        times=[wintypes.FILETIME() for _ in range(4)]
        for ok in [psapi.GetProcessMemoryInfo(process,ctypes.byref(memory),memory.cb),
                   kernel.GetProcessHandleCount(process,ctypes.byref(handles)),
                   kernel.GetProcessTimes(process,*[ctypes.byref(t) for t in times])]:
            if not ok:raise ctypes.WinError(ctypes.get_last_error())
        seconds=lambda t:((t.dwHighDateTime<<32)+t.dwLowDateTime)/10_000_000
        return dict(rss_mb=round(memory.WorkingSetSize/1024**2,3),
            private_commit_mb=round(memory.PagefileUsage/1024**2,3),
            handles=handles.value,cpu_seconds=round(seconds(times[2])+seconds(times[3]),4))
    finally:kernel.CloseHandle(process)
