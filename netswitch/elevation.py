"""Relaunching with administrator rights on Windows."""

from __future__ import annotations

import subprocess
import sys


def is_admin() -> bool:
    if sys.platform != "win32":
        return True  # NetworkManager authorises via polkit; no root needed
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(extra_args: list[str] | None = None) -> bool:
    """Start a new elevated instance. Returns True if the UAC prompt was accepted."""
    if sys.platform != "win32":
        return False
    import ctypes

    if getattr(sys, "frozen", False):
        exe, args = sys.executable, sys.argv[1:]
    else:
        # Prefer pythonw.exe so no console window appears.
        exe = sys.executable.replace("python.exe", "pythonw.exe")
        args = ["-m", "netswitch", *sys.argv[1:]]
    args += extra_args or []
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, subprocess.list2cmdline(args), None, 1)
    return rc > 32
