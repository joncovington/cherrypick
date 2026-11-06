"""Running CPU-bound child processes at full speed: the opt-out from Windows power throttling, and how
many to run at once.

The supervisor runs as a service with nobody logged on, so every process under it is a windowless
background process, which Windows throttles (EcoQoS, Task Manager's "efficiency mode"). On this
suite's hybrid-CPU host the same CPU-bound loop took 0.36 s normally and 2.45 s with EcoQoS forced on
(6.8x, #807). The opt-out is NOT inherited: each process has to be opted out itself, at its spawn,
whoever spawns it. Until 2026-11-05 only the supervisor's own children were, so a research-queue task
and the replay subprocesses a backtest starts ran throttled.
"""

from __future__ import annotations

import os
import subprocess

#: Windows `creationflags` for research work: below-normal priority, so with throttling off it takes
#: only CPU the live and paper loops leave. A child created without a priority flag of its own takes
#: this class from its parent (CreateProcess), so a task's replays inherit it. 0 elsewhere.
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000 if os.name == "nt" else 0


def never_throttle(handle: int | None = None) -> bool:
    """Opt a process out of Windows power throttling: `handle` is a child's process handle
    (`Popen._handle`), None this process. Setting the control bit with the state bit clear tells
    Windows never to throttle it. No admin needed for one's own process or child. Best effort: False
    off Windows, or where the call is refused, and the process runs as before."""
    if os.name != "nt":
        return False
    import ctypes
    import ctypes.wintypes as wt

    class _State(ctypes.Structure):
        _fields_ = [("Version", wt.ULONG), ("ControlMask", wt.ULONG), ("StateMask", wt.ULONG)]

    execution_speed = 0x1  # PROCESS_POWER_THROTTLING_EXECUTION_SPEED
    process_power_throttling = 4  # PROCESS_INFORMATION_CLASS.ProcessPowerThrottling
    state = _State(1, execution_speed, 0)  # version 1; controlled, not throttled
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wt.HANDLE
        k32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        target = k32.GetCurrentProcess() if handle is None else handle
        ok = k32.SetProcessInformation(
            target, process_power_throttling, ctypes.byref(state), ctypes.sizeof(state)
        )
        return bool(ok)
    except (OSError, AttributeError):
        return False


def never_throttle_child(proc) -> bool:
    """`never_throttle` for a `subprocess.Popen` (or a test double without a handle: False)."""
    handle = getattr(proc, "_handle", None)
    return never_throttle(int(handle)) if handle is not None else False


def run_unthrottled(args, *, timeout: float | None = None, **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run` with the child opted out of power throttling as soon as it exists. The same
    arguments and result; on a timeout the child is killed and `TimeoutExpired` raised, as `run` does."""
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    with subprocess.Popen(args, **kwargs) as proc:
        never_throttle_child(proc)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


#: Logical cores kept free when sizing a pool: one for the process that runs the pool and the
#: supervisor, one for whatever else is running.
RESERVED_CORES = 2


def default_workers(reserve: int = RESERVED_CORES) -> int:
    """How many CPU-bound children to run at once by default: the machine's logical cores less
    `reserve`, at least one. Every study script's `--workers` defaults to this, never to a number
    of its own (a fixed 8 left half of a 16-thread host idle)."""
    return max(1, (os.cpu_count() or 1) - reserve)
