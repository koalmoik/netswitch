"""Dialog for creating and editing a profile."""

from __future__ import annotations

import copy
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from ..backends import Adapter, Backend
from ..models import Profile, ValidationError, mask_to_prefix
from .worker import run_async


class ProfileDialog(QDialog):
    def __init__(self, profile: Profile, adapters: list[Adapter], backend: Backend, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit profile" if profile.name else "New profile")
        self.setMinimumWidth(460)
        self.backend = backend
        self.profile = copy.deepcopy(profile)

        self.name = QLineEdit(profile.name)

        self.adapter = QComboBox()
        self.adapter.setEditable(True)
        for a in adapters:
            self.adapter.addItem(a.label, a.name)
        idx = self.adapter.findData(profile.adapter)
        if idx >= 0:
            self.adapter.setCurrentIndex(idx)
        else:
            self.adapter.setEditText(profile.adapter)
        self.read_btn = QPushButton("Read current")
        self.read_btn.setToolTip("Fill in the adapter's current settings")
        self.read_btn.clicked.connect(self._read_current)
        adapter_row = QHBoxLayout()
        adapter_row.addWidget(self.adapter, 1)
        adapter_row.addWidget(self.read_btn)

        top = QFormLayout()
        top.addRow("Name:", self.name)
        top.addRow("Adapter:", adapter_row)

        # IP settings
        self.ip_dhcp = QRadioButton("Obtain an IP address automatically (DHCP)")
        self.ip_static = QRadioButton("Use the following IP address:")
        self._ip_group = QButtonGroup(self)
        self._ip_group.addButton(self.ip_dhcp)
        self._ip_group.addButton(self.ip_static)
        self.ip = QLineEdit()
        self.ip.setPlaceholderText("192.168.1.10")
        self.mask = QLineEdit()
        self.mask.setPlaceholderText("255.255.255.0 or 24")
        self.gateway = QLineEdit()
        self.gateway.setPlaceholderText("192.168.1.1 (optional)")
        self._static_ip_box = QWidget()
        f = QFormLayout(self._static_ip_box)
        f.setContentsMargins(20, 0, 0, 0)
        f.addRow("IP address:", self.ip)
        f.addRow("Subnet mask:", self.mask)
        f.addRow("Gateway:", self.gateway)
        ip_box = QGroupBox("IPv4")
        v = QVBoxLayout(ip_box)
        v.addWidget(self.ip_dhcp)
        v.addWidget(self.ip_static)
        v.addWidget(self._static_ip_box)

        # DNS settings
        self.dns_dhcp = QRadioButton("Obtain DNS server addresses automatically")
        self.dns_static = QRadioButton("Use the following DNS servers:")
        self._dns_group = QButtonGroup(self)
        self._dns_group.addButton(self.dns_dhcp)
        self._dns_group.addButton(self.dns_static)
        self.dns = QLineEdit()
        self.dns.setPlaceholderText("1.1.1.1, 8.8.8.8")
        self.dns.setToolTip("Separate multiple servers with commas or spaces")
        dns_box = QGroupBox("DNS")
        v = QVBoxLayout(dns_box)
        v.addWidget(self.dns_dhcp)
        v.addWidget(self.dns_static)
        dns_row = QWidget()
        h = QFormLayout(dns_row)
        h.setContentsMargins(20, 0, 0, 0)
        h.addRow("Servers:", self.dns)
        v.addWidget(dns_row)

        self.notes = QPlainTextEdit(profile.notes)
        self.notes.setPlaceholderText("Notes (optional)")
        self.notes.setMaximumHeight(60)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(ip_box)
        layout.addWidget(dns_box)
        layout.addWidget(self.notes)
        layout.addWidget(buttons)

        self.ip_static.toggled.connect(self._update_enabled)
        self.dns_static.toggled.connect(self._update_enabled)
        self.ip_static.toggled.connect(self._force_static_dns)
        self._load(profile)
        self.name.setFocus()

    # ---- state <-> widgets ----------------------------------------------

    def _load(self, p: Profile) -> None:
        (self.ip_static if p.ip_mode == "static" else self.ip_dhcp).setChecked(True)
        (self.dns_static if p.dns_mode == "static" else self.dns_dhcp).setChecked(True)
        self.ip.setText(p.ip_address)
        self.mask.setText(p.subnet_mask if p.ip_address or p.ip_mode == "static" else "")
        self.gateway.setText(p.gateway)
        self.dns.setText(", ".join(p.dns_servers))
        self._update_enabled()

    def _update_enabled(self) -> None:
        static_ip = self.ip_static.isChecked()
        self._static_ip_box.setEnabled(static_ip)
        # A static IP without DHCP has no source for automatic DNS.
        self.dns_dhcp.setEnabled(not static_ip)
        self.dns.setEnabled(self.dns_static.isChecked())

    def _force_static_dns(self, static_ip: bool) -> None:
        if static_ip:
            self.dns_static.setChecked(True)

    def _adapter_name(self) -> str:
        text = self.adapter.currentText()
        idx = self.adapter.findText(text)
        if idx >= 0 and self.adapter.itemData(idx):
            return self.adapter.itemData(idx)
        return text.strip()

    def result_profile(self) -> Profile:
        p = self.profile
        p.name = self.name.text().strip()
        p.adapter = self._adapter_name()
        p.ip_mode = "static" if self.ip_static.isChecked() else "dhcp"
        p.ip_address = self.ip.text().strip()
        mask = self.mask.text().strip()
        p.prefix_length = mask_to_prefix(mask) if mask else 24
        p.gateway = self.gateway.text().strip()
        p.dns_mode = "static" if self.dns_static.isChecked() else "dhcp"
        p.dns_servers = [s for s in re.split(r"[,;\s]+", self.dns.text()) if s]
        p.notes = self.notes.toPlainText().strip()
        return p

    def accept(self) -> None:
        try:
            self.result_profile().validate()
        except ValidationError as e:
            QMessageBox.warning(self, "Invalid profile", str(e))
            return
        super().accept()

    # ---- read current settings --------------------------------------------

    def _read_current(self) -> None:
        adapter = self._adapter_name()
        if not adapter:
            QMessageBox.information(self, "Read current", "Select an adapter first.")
            return
        self.read_btn.setEnabled(False)
        self.setCursor(Qt.BusyCursor)

        def done(current: Profile):
            self._reset_busy()
            self._load(current)

        def failed(msg: str):
            self._reset_busy()
            QMessageBox.warning(self, "Read current", msg)

        run_async(self.backend.read_config, adapter, on_done=done, on_error=failed)

    def _reset_busy(self):
        self.read_btn.setEnabled(True)
        self.unsetCursor()
