"""Main window and system tray."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QSystemTrayIcon, QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..backends import Adapter, get_backend
from ..elevation import is_admin, relaunch_as_admin
from ..models import Profile
from ..storage import APP_NAME, ProfileStore
from .editor import ProfileDialog
from .worker import run_async

FILE_FILTER = "NetSwitch profiles (*.json);;All files (*)"


def make_icon(color: str = "#2e7dd7") -> QIcon:
    """Draw a simple network icon so no image assets are needed."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(color)))
        p.drawRoundedRect(0, 0, size, size, size * 0.2, size * 0.2)
        p.setBrush(QBrush(Qt.white))
        s = size / 8
        # three nodes connected to a hub
        p.drawRect(int(size / 2 - s * 0.25), int(s * 2.6), int(s * 0.5), int(s * 2.4))
        p.drawRect(int(s * 1.6), int(s * 4.8), int(size - s * 3.2), int(s * 0.5))
        for x in (s * 1.2, size / 2 - s * 0.8, size - s * 2.8):
            p.drawRect(int(x), int(s * 5.4), int(s * 1.6), int(s * 1.4))
        p.drawRect(int(size / 2 - s * 1.2), int(s * 1.0), int(s * 2.4), int(s * 1.8))
        p.end()
        icon.addPixmap(pm)
    return icon


class MainWindow(QMainWindow):
    def __init__(self, store: ProfileStore):
        super().__init__()
        self.store = store
        self.backend = get_backend()
        self.adapters: list[Adapter] = []
        self._busy = False
        self._really_quit = False
        self._tray_hint_shown = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_icon())
        self.resize(820, 520)

        # ---- profile list ---------------------------------------------------
        self.list = QListWidget()
        self.list.setIconSize(QSize(20, 20))
        self.list.setDragDropMode(QListWidget.InternalMove)
        self.list.model().rowsMoved.connect(self._on_rows_moved)
        self.list.itemDoubleClicked.connect(lambda _: self.apply_selected())
        self.list.currentItemChanged.connect(lambda *_: self._update_details())
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)

        self.apply_btn = QPushButton("Activate")
        self.apply_btn.setDefault(True)
        f = self.apply_btn.font()
        f.setBold(True)
        self.apply_btn.setFont(f)
        self.apply_btn.setMinimumHeight(34)
        self.apply_btn.clicked.connect(self.apply_selected)

        buttons = QHBoxLayout()
        for text, slot in (
            ("New", self.new_profile),
            ("From current…", self.new_from_current),
            ("Edit", self.edit_selected),
            ("Duplicate", self.duplicate_selected),
            ("Delete", self.delete_selected),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Profiles  (double-click or Ctrl+1…9 to activate)"))
        lv.addWidget(self.list, 1)
        lv.addLayout(buttons)

        # ---- details + log ----------------------------------------------------
        self.details = QLabel()
        self.details.setTextFormat(Qt.RichText)
        self.details.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas" if sys.platform == "win32" else "Monospace", 9))
        self.log.setPlaceholderText("Activity log")

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self.details, 1)
        rv.addWidget(self.apply_btn)
        rv.addWidget(self.log, 1)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([420, 400])
        central = QWidget()
        QVBoxLayout(central).addWidget(splitter)
        self.setCentralWidget(central)

        self._build_menu()
        self._build_shortcuts()
        self._build_tray()
        self._update_status()

        self.refresh_list()
        self.refresh_adapters()

    # ---- construction helpers --------------------------------------------------

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        self._action(m, "&Import profiles…", self.import_profiles, QKeySequence("Ctrl+I"))
        self._action(m, "&Export all profiles…", self.export_all, QKeySequence("Ctrl+E"))
        self._action(m, "Export &selected profile…", self.export_selected)
        m.addSeparator()
        self._action(m, "Open profiles &folder", self.open_profiles_folder)
        m.addSeparator()
        self._action(m, "&Quit", self.quit, QKeySequence.Quit)

        m = self.menuBar().addMenu("&Profile")
        self._action(m, "&New", self.new_profile, QKeySequence.New)
        self._action(m, "New from &current settings…", self.new_from_current)
        self._action(m, "&Edit", self.edit_selected, QKeySequence("F2"))
        self._action(m, "&Duplicate", self.duplicate_selected, QKeySequence("Ctrl+D"))
        self._action(m, "De&lete", self.delete_selected, QKeySequence.Delete)
        m.addSeparator()
        self._action(m, "&Activate", self.apply_selected, QKeySequence("Ctrl+Return"))
        self._action(m, "Show &commands (preview)", self.preview_selected)
        m.addSeparator()
        self._action(m, "&Refresh adapters", self.refresh_adapters, QKeySequence.Refresh)

        m = self.menuBar().addMenu("&Help")
        self._action(m, "&About", self.about)

    def _action(self, menu, text, slot, shortcut=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(shortcut)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _build_shortcuts(self):
        for n in range(1, 10):
            a = QAction(self)
            a.setShortcut(QKeySequence(f"Ctrl+{n}"))
            a.triggered.connect(lambda _=False, i=n - 1: self._apply_index(i))
            self.addAction(a)

    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip(APP_NAME)
        self.tray_menu = QMenu()
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _rebuild_tray_menu(self):
        if not self.tray:
            return
        m = self.tray_menu
        m.clear()
        header = m.addAction("Switch profile")
        header.setEnabled(False)
        for p in self.store.profiles:
            a = m.addAction(p.name)
            a.setCheckable(True)
            a.setChecked(p.id == self.store.last_applied)
            a.setEnabled(not self._busy)
            a.triggered.connect(lambda _=False, pid=p.id: self.apply_profile(self.store.get(pid)))
        if not self.store.profiles:
            m.addAction("(no profiles)").setEnabled(False)
        m.addSeparator()
        m.addAction("Show window", self.show_window)
        m.addAction("Quit", self.quit)
        active = self.store.get(self.store.last_applied or "")
        self.tray.setToolTip(f"{APP_NAME} – {active.name}" if active else APP_NAME)

    def _update_status(self):
        if sys.platform == "win32" and not is_admin():
            btn = QPushButton("Restart as administrator")
            btn.clicked.connect(self.restart_elevated)
            self.statusBar().addPermanentWidget(btn)
            self.statusBar().showMessage("Not running as administrator – activating profiles needs admin rights.")
        else:
            self.statusBar().showMessage("Ready")

    # ---- list ------------------------------------------------------------------

    def refresh_list(self, select_id: str | None = None):
        current = select_id or self._selected_id()
        self.list.blockSignals(True)
        self.list.clear()
        active_icon = make_icon("#2ea043")
        idle_icon = make_icon("#8b949e")
        for i, p in enumerate(self.store.profiles):
            prefix = f"{i + 1}. " if i < 9 else "    "
            item = QListWidgetItem(f"{prefix}{p.name}\n      {p.summary()}")
            item.setData(Qt.UserRole, p.id)
            item.setIcon(active_icon if p.id == self.store.last_applied else idle_icon)
            item.setToolTip(p.notes or p.summary())
            self.list.addItem(item)
            if p.id == current:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        self._update_details()
        self._rebuild_tray_menu()

    def _on_rows_moved(self, *_):
        order = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        by_id = {p.id: p for p in self.store.profiles}
        self.store.profiles = [by_id[i] for i in order]
        self.store.save()
        # Re-render to update the 1..9 numbering after the drop completes.
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self.refresh_list)

    def _selected_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _selected(self) -> Profile | None:
        pid = self._selected_id()
        return self.store.get(pid) if pid else None

    def _update_details(self):
        p = self._selected()
        self.apply_btn.setEnabled(p is not None and not self._busy)
        if not p:
            self.details.setText("<i>No profile selected. Create one with <b>New</b> "
                                 "or <b>From current…</b>.</i>")
            return
        rows = [("Adapter", p.adapter or "<i>not set</i>")]
        if p.ip_mode == "dhcp":
            rows.append(("IP", "Automatic (DHCP)"))
        else:
            rows += [("IP address", p.ip_address), ("Subnet mask", f"{p.subnet_mask} (/{p.prefix_length})"),
                     ("Gateway", p.gateway or "<i>none</i>")]
        rows.append(("DNS", "Automatic" if p.dns_mode == "dhcp" else ("<br>".join(p.dns_servers) or "<i>none</i>")))
        if p.notes:
            rows.append(("Notes", p.notes.replace("\n", "<br>")))
        body = "".join(f"<tr><td style='padding-right:12px;color:gray'>{k}</td><td>{v}</td></tr>" for k, v in rows)
        active = " <span style='color:#2ea043'>● active</span>" if p.id == self.store.last_applied else ""
        self.details.setText(f"<h3>{p.name}{active}</h3><table>{body}</table>")

    def _context_menu(self, pos):
        if not self.list.itemAt(pos):
            return
        m = QMenu(self)
        m.addAction("Activate", self.apply_selected)
        m.addAction("Edit", self.edit_selected)
        m.addAction("Duplicate", self.duplicate_selected)
        m.addAction("Export…", self.export_selected)
        m.addAction("Show commands", self.preview_selected)
        m.addSeparator()
        m.addAction("Delete", self.delete_selected)
        m.exec(self.list.viewport().mapToGlobal(pos))

    # ---- adapters ----------------------------------------------------------------

    def refresh_adapters(self):
        def done(adapters):
            self.adapters = adapters
            self.log_line(f"Found {len(adapters)} adapter(s): " + ", ".join(a.name for a in adapters))

        run_async(self.backend.list_adapters, on_done=done,
                  on_error=lambda msg: self.log_line(f"Could not list adapters: {msg}"))

    # ---- profile CRUD ---------------------------------------------------------------

    def _edit(self, profile: Profile) -> Profile | None:
        dlg = ProfileDialog(profile, self.adapters, self.backend, self)
        if dlg.exec():
            return dlg.result_profile()
        return None

    def new_profile(self):
        default_adapter = self.adapters[0].name if len(self.adapters) == 1 else ""
        p = self._edit(Profile(name="", adapter=default_adapter))
        if p:
            self.store.upsert(p)
            self.refresh_list(p.id)

    def new_from_current(self):
        if not self.adapters:
            QMessageBox.information(self, APP_NAME, "No network adapters found yet. Try Profile → Refresh adapters.")
            return
        from PySide6.QtWidgets import QInputDialog

        labels = [a.label for a in self.adapters]
        label, ok = QInputDialog.getItem(self, "New from current settings", "Adapter:", labels, 0, False)
        if not ok:
            return
        adapter = self.adapters[labels.index(label)].name
        self.setCursor(Qt.BusyCursor)

        def done(current: Profile):
            self.unsetCursor()
            current.name = ""
            p = self._edit(current)
            if p:
                self.store.upsert(p)
                self.refresh_list(p.id)

        def failed(msg):
            self.unsetCursor()
            QMessageBox.warning(self, APP_NAME, msg)

        run_async(self.backend.read_config, adapter, on_done=done, on_error=failed)

    def edit_selected(self):
        p = self._selected()
        if p and (edited := self._edit(p)):
            self.store.upsert(edited)
            self.refresh_list(edited.id)

    def duplicate_selected(self):
        p = self._selected()
        if not p:
            return
        dup = copy.deepcopy(p)
        dup.id = Profile(name="x").id
        dup.name = self.store._unique_name(p.name)
        self.store.profiles.insert(self.store.profiles.index(p) + 1, dup)
        self.store.save()
        self.refresh_list(dup.id)

    def delete_selected(self):
        p = self._selected()
        if p and QMessageBox.question(self, "Delete profile", f"Delete profile '{p.name}'?") == QMessageBox.Yes:
            self.store.remove(p.id)
            self.refresh_list()

    # ---- applying ------------------------------------------------------------------

    def _apply_index(self, i: int):
        if i < len(self.store.profiles):
            self.apply_profile(self.store.profiles[i])

    def apply_selected(self):
        self.apply_profile(self._selected())

    def apply_profile(self, p: Profile | None):
        if not p or self._busy:
            return
        if not p.adapter:
            QMessageBox.warning(self, APP_NAME, f"Profile '{p.name}' has no adapter set. Edit it first.")
            return
        if self.backend.needs_elevation():
            if QMessageBox.question(
                self, APP_NAME,
                "Changing network settings requires administrator rights.\n\nRestart NetSwitch as administrator now?",
            ) == QMessageBox.Yes:
                self.restart_elevated()
            return
        self._set_busy(True)
        self.log_line(f"Activating '{p.name}' on {p.adapter}…")

        def done(log):
            for line in log:
                self.log_line(line)
            self.store.last_applied = p.id
            self.store.save()
            self._set_busy(False)
            self.refresh_list()
            self.log_line(f"✔ '{p.name}' is active.")
            self.statusBar().showMessage(f"Activated '{p.name}'", 5000)
            if self.tray and not self.isVisible():
                self.tray.showMessage(APP_NAME, f"Activated '{p.name}'", QSystemTrayIcon.Information, 3000)

        def failed(msg):
            self._set_busy(False)
            self.log_line(f"✘ Failed: {msg}")
            if self.tray and not self.isVisible():
                self.tray.showMessage(APP_NAME, f"Failed to activate '{p.name}'", QSystemTrayIcon.Critical, 5000)
            else:
                QMessageBox.critical(self, APP_NAME, f"Could not activate '{p.name}':\n\n{msg}")

        run_async(self.backend.apply, copy.deepcopy(p), on_done=done, on_error=failed)

    def preview_selected(self):
        p = self._selected()
        if not p:
            return
        try:
            cmds = self.backend.build_commands(p, p.adapter or "<adapter>")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, str(e))
            return
        self.log_line(f"Commands for '{p.name}':\n" + "\n".join("  " + c.display() for c in cmds))

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.apply_btn.setText("Activating…" if busy else "Activate")
        self.setCursor(Qt.BusyCursor) if busy else self.unsetCursor()
        self._update_details()
        self._rebuild_tray_menu()

    def log_line(self, text: str):
        self.log.appendPlainText(text)

    # ---- import / export --------------------------------------------------------------

    def import_profiles(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import profiles", str(Path.home()), FILE_FILTER)
        if not path:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Import profiles")
        box.setText("How should profiles with the same name be handled?")
        replace = box.addButton("Replace existing", QMessageBox.AcceptRole)
        box.addButton("Keep both", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() == box.button(QMessageBox.Cancel):
            return
        try:
            added, replaced = self.store.import_from(Path(path), overwrite=box.clickedButton() == replace)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Import failed", f"Could not import {path}:\n\n{e}")
            return
        self.refresh_list()
        msg = f"Imported {added} new profile(s)" + (f", replaced {replaced}" if replaced else "") + "."
        self.log_line(msg)
        QMessageBox.information(self, "Import profiles", msg)

    def _export(self, ids: list[str] | None, default_name: str):
        path, _ = QFileDialog.getSaveFileName(self, "Export profiles", str(Path.home() / default_name), FILE_FILTER)
        if not path:
            return
        if not Path(path).suffix:
            path += ".json"
        try:
            n = self.store.export_to(Path(path), ids)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(e))
            return
        self.log_line(f"Exported {n} profile(s) to {path}")
        self.statusBar().showMessage(f"Exported {n} profile(s)", 5000)

    def export_all(self):
        self._export(None, "netswitch-profiles.json")

    def export_selected(self):
        p = self._selected()
        if p:
            safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in p.name)
            self._export([p.id], f"{safe}.json")

    def open_profiles_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.path.parent)))

    # ---- window / tray behaviour -----------------------------------------------------

    def restart_elevated(self):
        if relaunch_as_admin():
            self.quit()
        else:
            self.log_line("Elevation was cancelled.")

    def about(self):
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br>Quickly switch between saved network profiles.<br><br>"
            f"Profiles file:<br><code>{self.store.path}</code>",
        )

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.show_window()

    def closeEvent(self, event):
        if self.tray and not self._really_quit:
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self.tray.showMessage(APP_NAME, "Still running in the tray. Right-click the icon to switch profiles.",
                                      QSystemTrayIcon.Information, 3000)
                self._tray_hint_shown = True
        else:
            event.accept()

    def quit(self):
        self._really_quit = True
        if self.tray:
            self.tray.hide()
        QApplication.quit()


def run_gui(store: ProfileStore, start_minimized: bool = False) -> int:
    if sys.platform == "win32":
        import ctypes

        # Own taskbar identity so Windows shows our icon instead of Python's.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NetSwitch.App")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(make_icon())
    win = MainWindow(store)
    if not (start_minimized and win.tray):
        win.show()
    if not win.tray:
        app.setQuitOnLastWindowClosed(True)
    return app.exec()
