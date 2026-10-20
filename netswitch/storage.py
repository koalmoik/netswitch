"""Persisting profiles and import/export."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from .models import SCHEMA_VERSION, Profile, ValidationError

APP_NAME = "NetSwitch"


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def _dump(profiles: list[Profile]) -> dict:
    return {
        "app": APP_NAME,
        "schema": SCHEMA_VERSION,
        "profiles": [p.to_dict() for p in profiles],
    }


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def parse_profiles(data) -> list[Profile]:
    """Accept either the full export document or a bare list of profiles."""
    if isinstance(data, dict):
        items = data.get("profiles")
        if items is None and "name" in data:  # single profile object
            items = [data]
    else:
        items = data
    if not isinstance(items, list):
        raise ValidationError("File does not contain any profiles")
    return [Profile.from_dict(item) for item in items]


class ProfileStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "profiles.json")
        self.profiles: list[Profile] = []
        self.last_applied: str | None = None
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.profiles = []
            return
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.profiles = parse_profiles(data)
        self.last_applied = data.get("last_applied") if isinstance(data, dict) else None

    def save(self) -> None:
        data = _dump(self.profiles)
        data["last_applied"] = self.last_applied
        _atomic_write(self.path, data)

    def get(self, profile_id: str) -> Profile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    def upsert(self, profile: Profile) -> None:
        for i, p in enumerate(self.profiles):
            if p.id == profile.id:
                self.profiles[i] = profile
                break
        else:
            self.profiles.append(profile)
        self.save()

    def remove(self, profile_id: str) -> None:
        self.profiles = [p for p in self.profiles if p.id != profile_id]
        self.save()

    def move(self, profile_id: str, delta: int) -> None:
        idx = next(i for i, p in enumerate(self.profiles) if p.id == profile_id)
        new = max(0, min(len(self.profiles) - 1, idx + delta))
        self.profiles.insert(new, self.profiles.pop(idx))
        self.save()

    # ---- import / export -----------------------------------------------

    def export_to(self, path: Path, profile_ids: list[str] | None = None) -> int:
        chosen = [p for p in self.profiles if profile_ids is None or p.id in profile_ids]
        _atomic_write(Path(path), _dump(chosen))
        return len(chosen)

    def import_from(self, path: Path, overwrite: bool = False) -> tuple[int, int]:
        """Import profiles from a file.

        Profiles whose name matches an existing one are replaced when
        ``overwrite`` is True, otherwise they are added with a numbered suffix.
        Returns ``(added, replaced)``.
        """
        with open(path, encoding="utf-8") as f:
            incoming = parse_profiles(json.load(f))
        added = replaced = 0
        by_name = {p.name.casefold(): p for p in self.profiles}
        for prof in incoming:
            existing = by_name.get(prof.name.casefold())
            if existing and overwrite:
                prof.id = existing.id
                self.profiles[self.profiles.index(existing)] = prof
                replaced += 1
            else:
                if existing:
                    prof.name = self._unique_name(prof.name)
                # Always give imported profiles a fresh id so they never clash.
                prof.id = Profile(name="x").id
                self.profiles.append(prof)
                by_name[prof.name.casefold()] = prof
                added += 1
        self.save()
        return added, replaced

    def _unique_name(self, name: str) -> str:
        taken = {p.name.casefold() for p in self.profiles}
        n = 2
        while f"{name} ({n})".casefold() in taken:
            n += 1
        return f"{name} ({n})"
