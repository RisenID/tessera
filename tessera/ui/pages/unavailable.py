"""A page for a feature this platform cannot do."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..theme import SPACE, Palette
from ..widgets import EmptyState, heading


class UnavailablePage(QWidget):
    """Says what is missing and why, and does nothing else."""

    def __init__(self, name: str, reason: str, palette: Palette,
                 parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        layout.setSpacing(SPACE["lg"])
        layout.addWidget(heading(name, "Not available on this system"))
        layout.addWidget(
            EmptyState("", f"{name} is not available here", reason), 1
        )
