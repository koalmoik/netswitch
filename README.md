# NetSwitch

A small NetSetMan-style tool: save network profiles (IP, subnet, gateway, DNS) per adapter
and switch between them with one click, from the tray, with a keyboard shortcut, or from the
command line. Profiles can be exported to and imported from JSON files.

Windows is the main target; Linux works through NetworkManager (`nmcli`).

## Features

- Profiles with DHCP or static IPv4, gateway, and any number of DNS servers
- **From current…**: create a profile from an adapter's current settings
- Activate by double-click, the **Activate** button, `Ctrl+1` … `Ctrl+9`, or the tray menu
- Tray icon: switch profiles without opening the window; closing the window keeps it in the tray
- Import/export all or single profiles (JSON). When names clash on import you can replace or keep both
- Drag and drop to reorder profiles
- *Show commands* previews exactly what will be executed
- CLI for scripts and desktop shortcuts

## Running from source

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux: source .venv/bin/activate
pip install -e .[dev]
python -m netswitch
```

On Windows, changing network settings needs administrator rights. If NetSwitch isn't elevated,
it offers to restart itself as administrator.

## Building the Windows .exe

Run `build_windows.bat` on Windows with Python 3.10+ installed. It runs the tests and creates
`dist\NetSwitch.exe`, a single file that asks for admin rights (UAC) on start.

## Command line

```
netswitch --list
netswitch --apply "Office"
netswitch --export backup.json
netswitch --import backup.json [--overwrite]
netswitch --minimized            # start in the tray
```

Tip: a desktop shortcut to `NetSwitch.exe --apply "Office"` switches with a single click.
(The windowed .exe prints nothing; run from source with `python -m netswitch` to see output.)

## Where profiles are stored

- Windows: `%APPDATA%\NetSwitch\profiles.json`
- Linux: `~/.config/NetSwitch/profiles.json`

Export files use the same format, so you can also copy that file directly.

## How it works

| | Windows | Linux |
|---|---|---|
| Read adapters/settings | PowerShell (`Get-NetAdapter`, `Get-NetIPAddress`, …) as JSON | `nmcli` |
| Apply | `netsh interface ipv4 set address / set dnsservers` | `nmcli connection modify` + `up` |

On Linux the settings are written to the NetworkManager connection that's active on the device,
so they persist in that connection.
