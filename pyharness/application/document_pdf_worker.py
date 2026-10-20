"""One disposable, memory-limited parser. No document scripts, rendering or OCR."""
import ctypes
import json
import os
import sys
from ctypes import wintypes


def memory_limit():
    if os.name != 'nt':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                    ('active_limit', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                    ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', ctypes.c_ulonglong * 6),
                    ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                    ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
    handle = kernel.CreateJobObjectW(None, None)
    info = Extended(); info.basic.flags = 0x100; info.process_memory = 512 * 1024**2
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        raise RuntimeError('memory containment unavailable')
    return handle  # Keep the job alive until this disposable process exits.


def parse(path):
    from pypdf import PdfReader
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted:
        return {'capability_reason': 'encrypted_pdf', 'pages': []}
    root = reader.root_object
    names = root.get('/Names', {})
    if hasattr(names, 'get_object'):
        names = names.get_object()
    def unsafe_action(value):
        if hasattr(value, 'get_object'):
            value = value.get_object()
        # Internal destinations and URI links are never executed or rendered here.
        return isinstance(value, dict) and value.get('/S') not in {'/GoTo', '/URI'}
    if unsafe_action(root.get('/OpenAction')) or root.get('/AA') or names.get('/JavaScript') or names.get('/EmbeddedFiles'):
        return {'rejected': 'document_active_content'}
    if not 1 <= len(reader.pages) <= 200:
        return {'rejected': 'document_page_limit'}
    pages = []; size = 0; decoded = 0
    for page in reader.pages:
        if page.get('/AA'):
            return {'rejected': 'document_active_content'}
        for annotation in page.get('/Annots', []):
            obj = annotation.get_object()
            if obj.get('/AA') or unsafe_action(obj.get('/A')) or obj.get('/FS'):
                return {'rejected': 'document_active_content'}
        content = page.get_contents()
        # ContentStream is a dictionary and may be false even with decoded data.
        length = len(content.get_data()) if content is not None else 0
        decoded += length
        if length > 2 * 1024**2 or decoded > 8 * 1024**2:
            return {'rejected': 'document_stream_limit'}
        text = page.extract_text() or ''
        size += len(text.encode())
        if size > 98304:
            return {'rejected': 'document_text_limit'}
        pages.append(text)
    return {'capability_reason': 'text' if any(x.strip() for x in pages) else 'no_text_layer', 'pages': pages}


def main():
    try:
        retained_job = memory_limit()
        try:
            result = parse(sys.argv[1])
        except Exception:
            result = {'capability_reason': 'invalid_pdf', 'pages': []}
        # Private source text only travels over the parent's private pipe.
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode())
        sys.stdout.buffer.flush()
    except BaseException:
        sys.exit(2)


if __name__ == '__main__':
    main()
