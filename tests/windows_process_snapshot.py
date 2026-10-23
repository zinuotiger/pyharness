"""Read-only native identities for test-owned Windows descendant checks."""
import ctypes
import os
import time
from ctypes import wintypes as t


def descendant_identities(rows,pid):
    """Bind every ancestry edge to creation time, including a reused root PID."""
    root=next((row for row in rows if row['ProcessId']==pid),None)
    if root is None:raise RuntimeError('Owned root absent from process snapshot')
    parents={pid:int(root['CreationDate'])};selected=[]
    while True:
        added=[row for row in rows if row['ParentProcessId'] in parents
            and row['ProcessId'] not in parents
            and int(row['CreationDate'])>=parents[row['ParentProcessId']]]
        if not added:return selected
        parents.update((row['ProcessId'],int(row['CreationDate'])) for row in added)
        selected.extend((row['ProcessId'],row['CreationDate']) for row in added
            if row['Name'].lower() in {'python.exe','node.exe','chrome.exe','chrome_crashpad_handler.exe','msedge.exe','msedge_crashpad_handler.exe'})


def process_rows():
    if os.name != 'nt':
        raise RuntimeError('Windows process identity evidence requires Windows')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)

    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', t.DWORD), ('cntUsage', t.DWORD),
                    ('th32ProcessID', t.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', t.DWORD), ('cntThreads', t.DWORD),
                    ('th32ParentProcessID', t.DWORD), ('pcPriClassBase', t.LONG),
                    ('dwFlags', t.DWORD), ('szExeFile', t.WCHAR * 260)]

    kernel.CreateToolhelp32Snapshot.argtypes = [t.DWORD, t.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = t.HANDLE
    kernel.Process32FirstW.argtypes = [t.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32FirstW.restype = t.BOOL
    kernel.Process32NextW.argtypes = [t.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.restype = t.BOOL
    kernel.OpenProcess.argtypes = [t.DWORD, t.BOOL, t.DWORD]
    kernel.OpenProcess.restype = t.HANDLE
    kernel.GetProcessTimes.argtypes = [t.HANDLE, *([ctypes.POINTER(t.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = t.BOOL
    kernel.CloseHandle.argtypes = [t.HANDLE]
    kernel.CloseHandle.restype = t.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    rows = []
    deadline = time.monotonic() + 20  # Keep the original evidence query bound.
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            if time.monotonic() > deadline:
                raise TimeoutError('Native process identity query exceeded 20 seconds')
            # Query-only handles never grant terminate, write or injection rights.
            handle = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
            if handle:
                try:
                    created, exited, cpu_kernel, cpu_user = (t.FILETIME() for _ in range(4))
                    if kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                              ctypes.byref(cpu_kernel), ctypes.byref(cpu_user)):
                        birth = (created.dwHighDateTime << 32) | created.dwLowDateTime
                        rows.append(dict(ProcessId=int(entry.th32ProcessID),
                                         ParentProcessId=int(entry.th32ParentProcessID),
                                         CreationDate=str(birth), Name=entry.szExeFile))
                finally:
                    if not kernel.CloseHandle(handle):
                        raise ctypes.WinError(ctypes.get_last_error())
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES only.
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if not kernel.CloseHandle(snapshot):
            raise ctypes.WinError(ctypes.get_last_error())
    return rows
