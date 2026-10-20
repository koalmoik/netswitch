"""Launcher used by PyInstaller (it cannot run a package's __main__ with relative imports)."""

import sys

from netswitch.__main__ import main

sys.exit(main())
