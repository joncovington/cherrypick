"""Run `python -m <module> [args...]` with Windows power throttling off for this process.

    python unthrottled.py -m cherrypick.calendars.cli policies

The console's server is Node, which cannot make the Win32 call that opts a process out of power
throttling (EcoQoS), and it runs as a windowless child of the supervisor's service, so every module
CLI it spawned was a throttled background process: CPU-bound work ran 6.8x slower throttled on this
host (2026-11-02). The bridges in `server/src/services/` therefore start their `-m` reads through this
file on Windows. It opts itself out, then runs the module exactly as `python -m` would: the same
`sys.argv`, the working directory first on `sys.path`, and the module's own exit status. Off Windows,
or if the call is refused, the module runs as before.
"""

from __future__ import annotations

import os
import runpy
import sys


def never_throttle_self() -> bool:
    """Set this process's execution-speed throttling control bit with its state bit clear."""
    if os.name != "nt":
        return False
    import ctypes
    import ctypes.wintypes as wt

    class _State(ctypes.Structure):
        _fields_ = [("Version", wt.ULONG), ("ControlMask", wt.ULONG), ("StateMask", wt.ULONG)]

    state = _State(1, 0x1, 0)  # version 1; PROCESS_POWER_THROTTLING_EXECUTION_SPEED controlled, off
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wt.HANDLE
        k32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        ok = k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(state), ctypes.sizeof(state))
        return bool(ok)
    except (OSError, AttributeError):
        return False


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "-m":
        sys.stderr.write("usage: unthrottled.py -m <module> [args...]\n")
        sys.exit(2)
    never_throttle_self()
    module = sys.argv[2]
    # As `python -m`: argv[0] becomes the module's file (run_module sets it), the rest pass through,
    # and the current directory -- not this file's -- leads sys.path.
    sys.argv = [module, *sys.argv[3:]]
    sys.path[0] = os.getcwd()
    runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
