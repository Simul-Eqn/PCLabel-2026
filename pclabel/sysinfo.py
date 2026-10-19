"""Process memory figures (Windows), for import reports and benchmarks."""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def memory_gb() -> dict:
    """Current and peak working set, and peak commit (private bytes), of this process in GB."""
    try:
        k32 = ctypes.WinDLL("kernel32")
    except (OSError, AttributeError):
        return {}
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(pmc)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
    if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
        return {}
    g = 1024**3
    return {
        "working_set_gb": pmc.WorkingSetSize / g,
        "peak_working_set_gb": pmc.PeakWorkingSetSize / g,
        "peak_commit_gb": pmc.PeakPagefileUsage / g,
    }
