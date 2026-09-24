"""Owned process trees. Payloads start only after ownership is established.

Windows uses a kill-on-close Job Object, never taskkill on a retained PID.
POSIX children start in a dedicated session. This is lifecycle containment,
not a filesystem/network security sandbox.
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
import time
from ctypes import wintypes


class WindowsJob:
    def __init__(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel = kernel
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t),
                        ('max_ws', ctypes.c_size_t), ('active_limit', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_ulonglong * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.WinError(ctypes.get_last_error())
            kernel.CloseHandle(self.handle)
            self.handle = None
            raise error

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        handle = self.handle
        if handle:
            if not self.kernel.TerminateJobObject(handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


_GATED_COMMAND = (
    "import os,subprocess,sys; "
    "gate=os.read(0,1); "
    "sys.exit(125) if gate!=b'G' else None; "
    "p=subprocess.Popen(sys.argv[1:],stdin=sys.stdin,stdout=sys.stdout,stderr=sys.stderr,creationflags=0x08000000 if os.name=='nt' else 0); "
    "sys.exit(p.wait())"
)


class ProcessOwner:
    def __init__(self, argv, *, cwd, env, text=False, merge_stderr=False, input_open=False):
        self._mutex = threading.RLock()
        self._closed = False
        self._tree_stopped = False
        self._reaped = False
        if os.name != 'nt' and not all(hasattr(os, key) for key in ('waitid', 'WNOWAIT', 'P_PID')):
            raise RuntimeError('owned execution requires non-reaping waitid; this platform is not enabled')
        self.job = WindowsJob() if os.name == 'nt' else None
        self.proc = None
        self._assigned = False
        options = {'creationflags': 0x08000000} if os.name == 'nt' else {'start_new_session': True}
        if text:
            options.update(text=True, encoding='utf-8', errors='replace')
        try:
            command = [sys.executable, '-I', '-B', '-c', _GATED_COMMAND, *argv] if self.job else argv
            self.proc = subprocess.Popen(command, cwd=str(cwd), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE, **options)
            if self.job:
                self.job.assign(self.proc)
                self._assigned = True
                self.proc.stdin.write('G' if text else b'G')
                self.proc.stdin.flush()
            if not input_open:
                self.proc.stdin.close()
        except BaseException as primary:
            try:
                self.terminate()
            except BaseException as secondary:
                primary.add_note(f'process launch cleanup failed: {type(secondary).__name__}')
            if self.proc is not None:
                for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except BaseException as secondary:
                            primary.add_note(f'pipe cleanup failed: {type(secondary).__name__}')
            raise

    def poll(self):
        """Observe without releasing the POSIX identity anchor before group cleanup."""
        with self._mutex:
            if self.proc is None:
                return None
            if self.job is not None or self._reaped:
                return self.proc.poll()
            result = os.waitid(os.P_PID, self.proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if result is None:
                return None
            return result.si_status if result.si_code == os.CLD_EXITED else -result.si_status

    def wait_exit(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            rc = self.poll()
            if rc is not None:
                return rc
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.proc.args, timeout)
            time.sleep(.01)

    def terminate(self):
        with self._mutex:
            if self._closed:
                return
            errors = []
            if not self._tree_stopped:
                try:
                    if self.job is not None:
                        self.job.close()
                    elif self.proc is not None and not self._reaped:
                        try:
                            os.killpg(self.proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    self._tree_stopped = True
                except BaseException as exc:
                    errors.append(exc)
            if self.proc is not None and self.job is not None and not self._assigned:
                try:
                    if self.proc.poll() is None:
                        self.proc.kill()
                except BaseException as exc:
                    errors.append(exc)
            # POSIX must retain its unreaped leader until group termination succeeds.
            if self.proc is not None and (self.job is not None or self._tree_stopped):
                try:
                    self.proc.wait(timeout=5)
                    self._reaped = True
                except BaseException as exc:
                    errors.append(exc)
            self._closed = self._tree_stopped and (self.proc is None or self._reaped)
            if errors:
                for extra in errors[1:]: errors[0].add_note(f'cleanup: {type(extra).__name__}')
                raise errors[0]

    def collect(self, timeout):
        chunks = [bytearray(), bytearray()]
        errors = []
        def read(index, stream):
            try:
                while block := stream.read(8192):
                    room = max(0, 200_000-len(chunks[index]))
                    chunks[index].extend(block[:room])
            except BaseException as exc:
                errors.append(exc)
            finally:
                try: stream.close()
                except BaseException as exc: errors.append(exc)
        readers = [threading.Thread(target=read, args=(i,s), name='owned-process-output', daemon=True)
                   for i,s in enumerate((self.proc.stdout,self.proc.stderr)) if s is not None]
        started = []
        timeout_hit = False
        primary = None
        try:
            for reader in readers:
                reader.start()
                started.append(reader)
            try:
                self.wait_exit(timeout=max(.01,float(timeout)))
            except subprocess.TimeoutExpired:
                timeout_hit = True
        except BaseException as exc:
            primary = exc
        finally:
            try: self.terminate()
            except BaseException as exc: errors.append(exc)
            for reader in started:
                try:
                    reader.join(timeout=5)
                    if reader.is_alive(): raise RuntimeError('owned output reader did not stop')
                except BaseException as exc: errors.append(exc)
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                if stream is not None and not stream.closed:
                    try: stream.close()
                    except BaseException as exc: errors.append(exc)
        if primary is not None:
            for extra in errors: primary.add_note(f'cleanup: {type(extra).__name__}')
            raise primary
        if errors:
            raise errors[0]
        out, err = (bytes(c).decode('utf-8',errors='replace') for c in chunks)
        rc = self.proc.returncode
        summary = '\n'.join(x for x in (out.strip(),err.strip(),f'exit={rc}') if x)
        if timeout_hit: summary += '; timeout; owned process tree terminated'
        return {'ok':rc == 0 and not timeout_hit,'exit_code':rc,'stdout':out,'stderr':err,
                'timeout':timeout_hit,'cancelled':False,'summary':summary}
