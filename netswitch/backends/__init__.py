import sys

from .base import Adapter, Backend, BackendError, Command


def get_backend() -> Backend:
    if sys.platform == "win32":
        from .windows import WindowsBackend

        return WindowsBackend()
    from .linux import LinuxBackend

    return LinuxBackend()


__all__ = ["Adapter", "Backend", "BackendError", "Command", "get_backend"]
