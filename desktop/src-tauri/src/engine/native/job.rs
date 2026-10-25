//! Windows Job Objects: a stop ends a service with all of its children.
//!
//! Each service runs in its own job. The job ends its processes when it is
//! closed, so the engine also stops when the app ends or crashes.

use std::ffi::c_void;
use std::io;
use std::mem::size_of;
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::{
    OpenThread, ResumeThread, CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, CREATE_SUSPENDED,
    THREAD_SUSPEND_RESUME,
};

/// Creation flags for an engine process: no console window, and suspended
/// until it is in its job (see `Job::adopt`).
pub const SPAWN_FLAGS: u32 = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED;

/// Creation flags for a short helper program (for example `pg_isready`).
pub const HELPER_FLAGS: u32 = CREATE_NO_WINDOW;

/// The size of a Win32 struct, which is always far below `u32::MAX`.
#[allow(clippy::cast_possible_truncation)]
const fn struct_size<T>() -> u32 {
    size_of::<T>() as u32
}

pub struct Job(HANDLE);

// The handle is only used with thread-safe Win32 calls.
unsafe impl Send for Job {}
unsafe impl Sync for Job {}

impl Job {
    fn new() -> io::Result<Self> {
        // SAFETY: no security attributes and no name.
        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err(io::Error::last_os_error());
        }
        let job = Self(handle);
        // SAFETY: an all-zero JOBOBJECT_EXTENDED_LIMIT_INFORMATION is valid.
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: `info` lives for the call and the size matches its type.
        let ok = unsafe {
            SetInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                std::ptr::from_ref(&info).cast::<c_void>(),
                struct_size::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>(),
            )
        };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(job)
    }

    /// Put the suspended process `process` (id `pid`) in a new job, and then
    /// let it run. The process runs even when the job fails, because a
    /// suspended engine process would never end.
    pub fn adopt(process: HANDLE, pid: u32) -> io::Result<Self> {
        let job = Self::new().and_then(|job| {
            // SAFETY: both handles are open.
            if unsafe { AssignProcessToJobObject(job.0, process) } == 0 {
                Err(io::Error::last_os_error())
            } else {
                Ok(job)
            }
        });
        resume_process(pid);
        job
    }

    /// End every process in the job.
    pub fn terminate(&self) {
        // SAFETY: the handle is open. A job with no processes is fine.
        unsafe { TerminateJobObject(self.0, 1) };
    }
}

impl Drop for Job {
    fn drop(&mut self) {
        // SAFETY: the handle is open and closed only here.
        unsafe { CloseHandle(self.0) };
    }
}

/// Resume the threads of a process started with `CREATE_SUSPENDED`.
fn resume_process(pid: u32) {
    // SAFETY: a thread snapshot of the whole system.
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return;
    }
    // SAFETY: an all-zero THREADENTRY32 with its size set is valid.
    let mut entry: THREADENTRY32 = unsafe { std::mem::zeroed() };
    entry.dwSize = struct_size::<THREADENTRY32>();
    // SAFETY: `snapshot` is open and `entry` has its size set.
    let mut more = unsafe { Thread32First(snapshot, &raw mut entry) } != 0;
    while more {
        if entry.th32OwnerProcessID == pid {
            // SAFETY: an open thread handle is closed right after its use.
            unsafe {
                let thread = OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID);
                if !thread.is_null() {
                    ResumeThread(thread);
                    CloseHandle(thread);
                }
            }
        }
        // SAFETY: as above.
        more = unsafe { Thread32Next(snapshot, &raw mut entry) } != 0;
    }
    // SAFETY: the snapshot handle is open.
    unsafe { CloseHandle(snapshot) };
}
