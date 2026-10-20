"""Entry point: GUI by default, plus a small CLI for scripts and shortcuts.

    netswitch                      open the GUI
    netswitch --list               list saved profiles
    netswitch --apply "Office"     apply a profile without opening the GUI
    netswitch --apply "Office" --adapter "Ethernet 2"
                                   ... on another adapter than the one saved in the profile
    netswitch --export out.json    export all profiles
    netswitch --import in.json     import profiles
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
    # A windowed (no-console) build has no stdout/stderr; keep print() from crashing.
    if sys.stdout is None or sys.stderr is None:
        import os

        sys.stdout = sys.stderr = open(os.devnull, "w")
    parser = argparse.ArgumentParser(prog="netswitch", description="Network profile switcher")
    parser.add_argument("--list", action="store_true", help="list saved profiles")
    parser.add_argument("--apply", metavar="NAME", help="apply the profile with this name")
    parser.add_argument("--adapter", metavar="NAME", help="with --apply: use this adapter instead of the profile's")
    parser.add_argument("--export", metavar="FILE", type=Path, help="export all profiles to FILE")
    parser.add_argument("--import", dest="import_", metavar="FILE", type=Path,
                        help="import profiles from FILE (NetSwitch .json or NetSetMan .xml export)")
    parser.add_argument("--overwrite", action="store_true", help="with --import: replace profiles with the same name")
    parser.add_argument("--profiles", metavar="FILE", type=Path, help="use an alternative profiles file")
    parser.add_argument("--minimized", action="store_true", help="start the GUI hidden in the tray")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    if args.adapter and not args.apply:
        parser.error("--adapter can only be used with --apply")

    from .storage import ProfileStore

    if args.list or args.apply or args.export or args.import_:
        store = ProfileStore(args.profiles)
        try:
            return run_cli(store, args)
        except Exception as e:  # noqa: BLE001 - report any failure to the user
            print(f"error: {e}", file=sys.stderr)
            return 1

    from .gui.app import run_gui

    return run_gui(ProfileStore(args.profiles), start_minimized=args.minimized)


def run_cli(store, args) -> int:
    if args.import_:
        added, replaced, skipped = store.import_from(args.import_, overwrite=args.overwrite)
        print(f"Imported {added} new, replaced {replaced} profile(s).")
        for reason in skipped:
            print(f"Skipped {reason}")
    if args.export:
        n = store.export_to(args.export)
        print(f"Exported {n} profile(s) to {args.export}")
    if args.list:
        for p in store.profiles:
            print(f"{p.name:<30} {p.adapter or '-':<20} {p.summary()}")
    if args.apply:
        from .backends import get_backend

        match = [p for p in store.profiles if p.name.casefold() == args.apply.casefold()]
        if not match:
            print(f"No profile named '{args.apply}'", file=sys.stderr)
            return 2
        if not (args.adapter or match[0].adapter):
            print(f"Profile '{match[0].name}' has no adapter set; pass --adapter NAME", file=sys.stderr)
            return 2
        for line in get_backend().apply(match[0], args.adapter):
            print(line)
        store.last_applied = match[0].id
        store.save()
        print(f"Applied '{match[0].name}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
