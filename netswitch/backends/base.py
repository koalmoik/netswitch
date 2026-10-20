"""Common backend interface and command runner."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from ..models import Profile


class BackendError(RuntimeError):
    pass


@dataclass
class Adapter:
    name: str  # identifier used when applying a profile
    description: str = ""
    status: str = ""
    mac: str = ""

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.description and self.description != self.name:
            parts.append(f"– {self.description}")
        if self.status:
            parts.append(f"[{self.status}]")
        return " ".join(parts)


@dataclass
class Command:
    """A command to execute. ``args`` is a list; on Windows ``raw`` may hold a pre-quoted string."""

    args: list[str]
    raw: str | None = None
    tolerate_failure: bool = False

    def display(self) -> str:
        return self.raw or subprocess.list2cmdline(self.args)


def run(cmd: list[str] | str, encoding: str = "utf-8", timeout: int = 60) -> subprocess.CompletedProcess:
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            encoding=encoding,
            errors="replace",
            timeout=timeout,
            **kwargs,
        )
    except FileNotFoundError as e:
        raise BackendError(f"Command not found: {e.filename}") from e
    except subprocess.TimeoutExpired as e:
        raise BackendError(f"Command timed out: {e.cmd}") from e


class Backend:
    name = "base"
    output_encoding = "utf-8"

    def list_adapters(self) -> list[Adapter]:
        raise NotImplementedError

    def read_config(self, adapter: str) -> Profile:
        """Return the adapter's current settings as an (unsaved) profile."""
        raise NotImplementedError

    def build_commands(self, profile: Profile, adapter: str) -> list[Command]:
        raise NotImplementedError

    def needs_elevation(self) -> bool:
        return False

    def apply(self, profile: Profile, adapter: str | None = None) -> list[str]:
        """Apply ``profile``. Returns a log of executed commands and their output."""
        profile.validate()
        adapter = adapter or profile.adapter
        if not adapter:
            raise BackendError(f"Profile '{profile.name}' has no network adapter selected")
        log = []
        for cmd in self.build_commands(profile, adapter):
            res = run(cmd.raw if cmd.raw is not None else cmd.args, encoding=self.output_encoding)
            out = (res.stdout + res.stderr).strip()
            log.append(f"> {cmd.display()}" + (f"\n{out}" if out else ""))
            if res.returncode != 0 and not cmd.tolerate_failure:
                raise BackendError(
                    f"Command failed (exit {res.returncode}):\n{cmd.display()}\n\n{out}"
                )
        return log
