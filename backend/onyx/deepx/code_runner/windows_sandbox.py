"""Windows sandbox for code runner jobs: an AppContainer inside a job object.

Each job gets its own AppContainer with no capabilities. So a job has no
network, and it can open only files whose ACL lets in every AppContainer (the
Windows system files, and the job Python, which the runner grants once) or
its own AppContainer (the job's folders, which the runner grants per job). It
cannot read the files of the user.

Run as a script, this module is the launcher: it starts one command in an
AppContainer, in a job object, and waits for it. The job object ends all of
the job's processes when the launcher ends, also when the runner kills it.

This module imports only the standard library, so the launcher starts fast.
"""

import ctypes
import subprocess
import sys

if sys.platform != "win32":
    raise ImportError("windows_sandbox works only on Windows")

from ctypes import wintypes  # noqa: E402

# Every AppContainer ("ALL APPLICATION PACKAGES").
ALL_APP_PACKAGES_SID = "S-1-15-2-1"

_CREATE_NO_WINDOW = 0x08000000
_CREATE_SUSPENDED = 0x00000004
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_STARTF_USESTDHANDLES = 0x00000100
_HANDLE_FLAG_INHERIT = 0x00000001
_INFINITE = 0xFFFFFFFF
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_ALREADY_EXISTS = -2147024713  # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)

_JOB_OBJECT_BASIC_UI_RESTRICTIONS = 4
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_LIMIT_ACTIVE_PROCESS = 0x00000008
_LIMIT_JOB_MEMORY = 0x00000200
_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
# Desktop, display settings, exit Windows, global atoms, handles of other
# processes, clipboard read and write, and system settings.
_UI_LIMIT_ALL = 0x000000FF


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.POINTER(_SidAndAttributes)),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _dlls() -> tuple[ctypes.WinDLL, ctypes.WinDLL, ctypes.WinDLL]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    for function in (
        kernel32.CreateJobObjectW,
        kernel32.GetStdHandle,
    ):
        function.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.SetHandleInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_StartupInfoEx),
        ctypes.POINTER(_ProcessInformation),
    ]
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.FreeSid.argtypes = [ctypes.c_void_p]
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    return kernel32, advapi32, userenv


def _check(ok: object, what: str) -> None:
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), f"{what} failed")


def create_profile(name: str) -> str:
    """Create the AppContainer `name` (or reuse it) and return its SID."""
    _, advapi32, userenv = _dlls()
    sid = ctypes.c_void_p()
    result = userenv.CreateAppContainerProfile(
        name, name, "Deep X code runner job", None, 0, ctypes.byref(sid)
    )
    if result == _ALREADY_EXISTS:
        result = userenv.DeriveAppContainerSidFromAppContainerName(
            name, ctypes.byref(sid)
        )
    if result != 0:
        raise OSError(
            f"Could not create AppContainer {name}: HRESULT {result & 0xFFFFFFFF:#x}"
        )
    text = wintypes.LPWSTR()
    try:
        _check(
            advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)),
            "ConvertSidToStringSid",
        )
        return text.value or ""
    finally:
        advapi32.FreeSid(sid)
        ctypes.windll.kernel32.LocalFree(text)


def delete_profile(name: str) -> None:
    _, _, userenv = _dlls()
    userenv.DeleteAppContainerProfile(name)


def grant(path: str, sid: str, rights: str) -> None:
    """Let `sid` use `path` and everything under it. `rights` is an icacls
    right, for example "RX" (read) or "M" (read and write)."""
    subprocess.run(
        ["icacls", path, "/grant", f"*{sid}:(OI)(CI)({rights})", "/Q"],
        capture_output=True,
        check=True,
        timeout=600,
        creationflags=_CREATE_NO_WINDOW,
    )


def _new_job(kernel32: ctypes.WinDLL, memory_bytes: int, max_processes: int) -> int:
    job = kernel32.CreateJobObjectW(None, None)
    _check(job, "CreateJobObject")
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = (
        _LIMIT_KILL_ON_JOB_CLOSE
        | _LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        | _LIMIT_JOB_MEMORY
        | _LIMIT_ACTIVE_PROCESS
    )
    limits.BasicLimitInformation.ActiveProcessLimit = max_processes
    limits.JobMemoryLimit = memory_bytes
    _check(
        kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ),
        "SetInformationJobObject",
    )
    ui = wintypes.DWORD(_UI_LIMIT_ALL)
    _check(
        kernel32.SetInformationJobObject(
            job, _JOB_OBJECT_BASIC_UI_RESTRICTIONS, ctypes.byref(ui), ctypes.sizeof(ui)
        ),
        "SetInformationJobObject",
    )
    return job


def launch(sid: str, memory_bytes: int, max_processes: int, argv: list[str]) -> int:
    """Run `argv` in the AppContainer `sid` with this process's standard
    handles, environment and folder. Returns its exit code."""
    kernel32, advapi32, _ = _dlls()
    job = _new_job(kernel32, memory_bytes, max_processes)

    container_sid = ctypes.c_void_p()
    _check(
        advapi32.ConvertStringSidToSidW(sid, ctypes.byref(container_sid)),
        "ConvertStringSidToSid",
    )
    capabilities = _SecurityCapabilities(container_sid, None, 0, 0)

    handles: list[int] = []
    for number in (-10, -11, -12):
        handle = kernel32.GetStdHandle(number)
        if handle and handle not in handles:
            kernel32.SetHandleInformation(
                handle, _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT
            )
            handles.append(handle)
    handle_list = (wintypes.HANDLE * len(handles))(*handles)

    size = ctypes.c_size_t()
    kernel32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
    attributes = ctypes.create_string_buffer(size.value)
    _check(
        kernel32.InitializeProcThreadAttributeList(
            attributes, 2, 0, ctypes.byref(size)
        ),
        "InitializeProcThreadAttributeList",
    )
    try:
        _check(
            kernel32.UpdateProcThreadAttribute(
                attributes,
                0,
                _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(capabilities),
                ctypes.sizeof(capabilities),
                None,
                None,
            ),
            "UpdateProcThreadAttribute",
        )
        _check(
            kernel32.UpdateProcThreadAttribute(
                attributes,
                0,
                _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                handle_list,
                ctypes.sizeof(handle_list),
                None,
                None,
            ),
            "UpdateProcThreadAttribute",
        )
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = kernel32.GetStdHandle(-10)
        startup.StartupInfo.hStdOutput = kernel32.GetStdHandle(-11)
        startup.StartupInfo.hStdError = kernel32.GetStdHandle(-12)
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
        info = _ProcessInformation()
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        _check(
            kernel32.CreateProcessW(
                None,
                command_line,
                None,
                None,
                True,
                _EXTENDED_STARTUPINFO_PRESENT | _CREATE_SUSPENDED | _CREATE_NO_WINDOW,
                None,
                None,
                ctypes.byref(startup),
                ctypes.byref(info),
            ),
            "CreateProcess",
        )
    finally:
        kernel32.DeleteProcThreadAttributeList(attributes)
        advapi32.FreeSid(container_sid)

    try:
        if not kernel32.AssignProcessToJobObject(job, info.hProcess):
            error = ctypes.get_last_error()
            kernel32.TerminateProcess(info.hProcess, 1)
            raise ctypes.WinError(error, "AssignProcessToJobObject failed")
        kernel32.ResumeThread(info.hThread)
        kernel32.WaitForSingleObject(info.hProcess, _INFINITE)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(info.hThread)
        kernel32.CloseHandle(info.hProcess)
        # Closing the job ends any process that the job left running.
        kernel32.CloseHandle(job)


def main(args: list[str]) -> int:
    """`launcher <sid> <memory bytes> <max processes> <command...>`"""
    sid, memory_bytes, max_processes, *argv = args
    try:
        return launch(sid, int(memory_bytes), int(max_processes), argv)
    except OSError as e:
        sys.stderr.write(f"The sandbox could not start the job: {e}\n")
        return 125


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
