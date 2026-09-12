"""Visual design tokens and the application stylesheet.

Kept in one place so every page draws from the same palette, spacing scale and
radius set. Colours are defined as tokens rather than sprinkled through widget
code, which is what keeps the light and dark variants honest.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    surface: str
    surface_alt: str
    surface_hover: str
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    success: str
    warning: str
    danger: str
    shadow: str

    @property
    def dark(self) -> bool:
        return self.name == "dark"


DARK = Palette(
    name="dark",
    bg="#0F1115",
    surface="#171A21",
    surface_alt="#1E222B",
    surface_hover="#242936",
    border="#2A2F3A",
    text="#E6E9EF",
    muted="#98A1B2",
    accent="#6C8CFF",
    accent_text="#FFFFFF",
    success="#34D399",
    warning="#FBBF24",
    danger="#F87171",
    shadow="rgba(0, 0, 0, 110)",
)

LIGHT = Palette(
    name="light",
    bg="#F5F6F8",
    surface="#FFFFFF",
    surface_alt="#F0F2F5",
    surface_hover="#E9ECF1",
    border="#E2E5EA",
    text="#131721",
    muted="#5C6675",
    accent="#4F6BE8",
    accent_text="#FFFFFF",
    success="#0F9D6E",
    warning="#B45309",
    danger="#DC2626",
    shadow="rgba(15, 23, 42, 28)",
)

#: 4px base spacing scale, referenced by name so layouts stay consistent.
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"sm": 6, "md": 10, "lg": 14, "pill": 999}


def detect_palette(app: QApplication) -> Palette:
    """Follow the desktop's light/dark preference."""
    window = app.palette().color(QPalette.ColorRole.Window)
    # Perceived luminance; anything dim enough gets the dark palette.
    luminance = (0.299 * window.red() + 0.587 * window.green() + 0.114 * window.blue()) / 255
    return DARK if luminance < 0.5 else LIGHT


def mix(colour: str, other: str, amount: float) -> str:
    """Blend two hex colours; *amount* is how much of *other* to use."""
    first, second = QColor(colour), QColor(other)
    blended = QColor(
        round(first.red() * (1 - amount) + second.red() * amount),
        round(first.green() * (1 - amount) + second.green() * amount),
        round(first.blue() * (1 - amount) + second.blue() * amount),
    )
    return blended.name()


def stylesheet(p: Palette) -> str:
    """The whole application's QSS, generated from *p*."""
    accent_hover = mix(p.accent, "#FFFFFF" if p.dark else "#000000", 0.12)
    accent_soft = mix(p.surface, p.accent, 0.16)
    return f"""
* {{
    font-family: "Inter", "Cantarell", "Noto Sans", sans-serif;
    font-size: 14px;
    color: {p.text};
}}

QWidget#Root, QMainWindow {{
    background: {p.bg};
}}

/* ---------- sidebar ---------- */

QWidget#Sidebar {{
    background: {p.surface};
    border-right: 1px solid {p.border};
}}

QLabel#BrandName {{
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 0.2px;
}}

QLabel#BrandSub {{
    color: {p.muted};
    font-size: 12px;
}}

QListWidget#Nav {{
    background: transparent;
    border: none;
    outline: none;
    padding: {SPACE['sm']}px;
}}

QListWidget#Nav::item {{
    padding: 10px 12px;
    margin: 2px 0;
    border-radius: {RADIUS['md']}px;
    color: {p.muted};
}}

QListWidget#Nav::item:hover {{
    background: {p.surface_hover};
    color: {p.text};
}}

QListWidget#Nav::item:selected {{
    background: {accent_soft};
    color: {p.accent};
    font-weight: 600;
}}

/* ---------- cards and surfaces ---------- */

QFrame#Card {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS['lg']}px;
}}

QFrame#CardFlat {{
    background: {p.surface_alt};
    border: 1px solid transparent;
    border-radius: {RADIUS['md']}px;
}}

QFrame#Divider {{
    background: {p.border};
    max-height: 1px;
    border: none;
}}

QLabel#Title {{ font-size: 22px; font-weight: 700; }}
QLabel#Subtitle {{ color: {p.muted}; font-size: 13px; }}
QLabel#SectionTitle {{ font-size: 15px; font-weight: 650; }}
QLabel#Muted {{ color: {p.muted}; }}
QLabel#Mono {{ font-family: "JetBrains Mono", "Fira Code", monospace; font-size: 12px; }}

/* ---------- buttons ---------- */

QPushButton {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: {RADIUS['md']}px;
    padding: 8px 14px;
    color: {p.text};
}}

QPushButton:hover {{ background: {p.surface_hover}; }}
QPushButton:pressed {{ background: {mix(p.surface_alt, p.accent, 0.2)}; }}
QPushButton:disabled {{ color: {p.muted}; background: {p.surface}; }}

QPushButton#Primary {{
    background: {p.accent};
    border: 1px solid {p.accent};
    color: {p.accent_text};
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton#Primary:disabled {{ background: {mix(p.surface, p.accent, 0.35)}; border-color: transparent; }}

QPushButton#Danger {{ color: {p.danger}; border-color: {mix(p.border, p.danger, 0.4)}; }}
QPushButton#Danger:hover {{ background: {mix(p.surface, p.danger, 0.12)}; }}

QPushButton#Ghost {{ background: transparent; border: none; color: {p.muted}; padding: 6px 8px; }}
QPushButton#Ghost:hover {{ background: {p.surface_hover}; color: {p.text}; }}

QPushButton#Copy {{
    background: {p.accent};
    color: {p.accent_text};
    border: none;
    border-radius: {RADIUS['md']}px;
    padding: 10px 18px;
    font-weight: 700;
}}
QPushButton#Copy:hover {{ background: {accent_hover}; }}

/* ---------- inputs ---------- */

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: {RADIUS['md']}px;
    padding: 8px 10px;
    selection-background-color: {p.accent};
    selection-color: {p.accent_text};
}}

QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {p.accent};
}}

QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS['md']}px;
    selection-background-color: {accent_soft};
    selection-color: {p.accent};
    padding: 4px;
}}

/* ---------- lists ---------- */

QListWidget, QListView, QScrollArea {{
    background: transparent;
    border: none;
    outline: none;
}}

/* The viewport is a separate child widget and keeps the default palette
   background unless told otherwise, which leaves a pale panel inside a dark
   window. */
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QAbstractScrollArea::viewport {{ background: transparent; }}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {mix(p.surface, p.text, 0.18)};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {mix(p.surface, p.text, 0.3)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {mix(p.surface, p.text, 0.18)};
    border-radius: 5px;
    min-width: 30px;
}}

/* ---------- misc ---------- */

QToolTip {{
    background: {p.surface_alt};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: {RADIUS['sm']}px;
    padding: 6px 8px;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {p.border};
    border-radius: 5px;
    background: {p.surface_alt};
}}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: none;
}}

QProgressBar {{
    background: {p.surface_alt};
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}

QSplitter::handle {{ background: {p.border}; width: 1px; }}
"""
