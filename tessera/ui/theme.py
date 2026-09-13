"""Colours, spacing and the application stylesheet."""

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
#: Breeze rounds at 4px; matching it is most of looking native.
RADIUS = {"sm": 3, "md": 5, "lg": 8, "pill": 999}


#: Breeze's semantic colours, used when the platform reports none. These read
#: correctly on light and dark alike.
_POSITIVE = "#27AE60"
_NEUTRAL = "#F67400"
_NEGATIVE = "#DA4453"


def _luminance(colour: QColor) -> float:
    return (0.299 * colour.red() + 0.587 * colour.green() + 0.114 * colour.blue()) / 255


def _hex(colour: QColor) -> str:
    return colour.name()


def detect_palette(app: QApplication) -> Palette:
    """Build a palette from the desktop's own colours, accent included."""
    system = app.palette()
    window = system.color(QPalette.ColorRole.Window)
    if not window.isValid():
        return DARK
    dark = _luminance(window) < 0.5
    fallback = DARK if dark else LIGHT

    text = system.color(QPalette.ColorRole.WindowText)

    accent = system.color(QPalette.ColorRole.Highlight)
    disabled = system.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)

    return Palette(
        name="dark" if dark else "light",
        bg=_hex(window),
        # A shade lighter than the window, so cards read as raised.
        surface=mix(_hex(window), _hex(text), 0.05),
        surface_alt=mix(_hex(window), _hex(text), 0.06),
        surface_hover=mix(_hex(window), _hex(text), 0.11),
        border=mix(_hex(window), _hex(text), 0.18),
        text=_hex(text),
        muted=_hex(disabled) if disabled.isValid() else fallback.muted,
        accent=_hex(accent) if accent.isValid() else fallback.accent,
        accent_text=_hex(system.color(QPalette.ColorRole.HighlightedText)),
        success=_POSITIVE,
        warning=_NEUTRAL,
        danger=_NEGATIVE,
        shadow="rgba(0, 0, 0, 90)" if dark else "rgba(0, 0, 0, 28)",
    )


def mix(colour: str, other: str, amount: float) -> str:
    """Blend two hex colours; *amount* is how much of *other* to use."""
    first, second = QColor(colour), QColor(other)
    blended = QColor(
        round(first.red() * (1 - amount) + second.red() * amount),
        round(first.green() * (1 - amount) + second.green() * amount),
        round(first.blue() * (1 - amount) + second.blue() * amount),
    )
    return blended.name()


def tab_stylesheet(p: Palette) -> str:
    """The tab strip's QSS: colours and padding only."""
    return f"""
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent;
    border: none;
    padding: 7px 14px 9px 14px;
    margin-right: 2px;
    color: {p.muted};
}}
QTabBar::tab:hover {{ color: {p.text}; background: {p.surface_hover}; }}
QTabBar::tab:selected {{ color: {p.text}; font-weight: 650; }}
"""


def stylesheet(p: Palette) -> str:
    """The application's QSS: the sidebar, cards and semantic labels only."""
    app = QApplication.instance()
    base = app.font().pointSizeF() if app else 10.0
    if base <= 0:
        base = 10.0
    accent_soft = mix(p.bg, p.accent, 0.18)

    def pt(scale: float) -> str:
        return f"{base * scale:.1f}pt"

    return f"""
QWidget#Root, QMainWindow {{
    background: {p.bg};
}}

/* ---------- sidebar ---------- */

QWidget#Sidebar {{
    background: {p.surface_alt};
    border: none;
    border-right: 1px solid {p.border};
}}

QLabel#BrandName {{ font-size: {pt(1.2)}; font-weight: 700; }}
QLabel#BrandSub {{ color: {p.muted}; font-size: {pt(0.85)}; }}

/* The phone's picture frame at the top of the panel. */
QLabel#PhoneTile {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS['md']}px;
}}

/* The panel's switches: equal squares, lit when on. */
QPushButton#Quick {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS['md']}px;
}}
QPushButton#Quick:hover {{ background: {p.surface_hover}; }}
QPushButton#Quick:checked {{
    background: {p.accent};
    border-color: {p.accent};
    color: {p.accent_text};
}}

/* One notification in the panel feed. */
QFrame#FeedRow {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS['md']}px;
}}
QFrame#FeedRow:hover {{ background: {p.surface_hover}; }}

/* The grab handle for the sidebar's width. Given a colour of its own so it
   can be found; the platform draws it as empty space. */
QSplitter#Split::handle {{ background: {p.border}; }}
QSplitter#Split::handle:hover {{ background: {p.accent}; }}

/* ---------- tab strip ---------- */

QWidget#TabStrip {{
    background: {p.bg};
    border-bottom: 1px solid {p.border};
}}

QToolButton#Strip {{
    background: transparent;
    border: none;
    border-radius: {RADIUS['sm']}px;
    padding: 6px 8px;
    color: {p.muted};
}}
QToolButton#Strip:hover {{ background: {p.surface_hover}; color: {p.text}; }}
QToolButton#Strip::menu-indicator {{ width: 0; }}

/* ---------- cards ---------- */

QFrame#Card {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS['md']}px;
}}

QFrame#CardFlat {{
    background: {p.surface_alt};
    border: 1px solid transparent;
    border-radius: {RADIUS['sm']}px;
}}

QFrame#Divider {{
    background: {p.border};
    max-height: 1px;
    border: none;
}}

/* ---------- semantic labels ---------- */

QLabel#Title {{ font-size: {pt(1.55)}; font-weight: 700; }}
QLabel#Subtitle {{ color: {p.muted}; font-size: {pt(0.95)}; }}
QLabel#SectionTitle {{ font-size: {pt(1.1)}; font-weight: 650; }}
QLabel#Muted {{ color: {p.muted}; }}
QLabel#Mono {{ font-family: monospace; }}

/* ---------- the three buttons that carry meaning ---------- */

QPushButton#Primary {{
    padding: 6px 14px;
    border-radius: {RADIUS['sm']}px;
    background: {p.accent};
    border: 1px solid {p.accent};
    color: {p.accent_text};
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: {mix(p.accent, p.text, 0.15)};
    border-color: {mix(p.accent, p.text, 0.15)};
}}
QPushButton#Primary:disabled {{
    background: {mix(p.bg, p.accent, 0.3)};
    border-color: transparent;
    color: {p.muted};
}}

QPushButton#Danger {{ color: {p.danger}; }}
QPushButton#Danger:hover {{ background: {mix(p.surface, p.danger, 0.15)}; }}

QPushButton#Ghost {{ background: transparent; border: none; color: {p.muted}; }}
QPushButton#Ghost:hover {{ background: {p.surface_hover}; color: {p.text}; }}

QPushButton#Copy {{
    background: {p.accent};
    border: 1px solid {p.accent};
    color: {p.accent_text};
    font-weight: 700;
    padding: 7px 16px;
}}
QPushButton#Copy:hover {{
    background: {mix(p.accent, p.text, 0.15)};
    border-color: {mix(p.accent, p.text, 0.15)};
}}

/* ---------- scroll areas ---------- */

/* Cards must sit on the window colour, and a scroll area's viewport is a
   separate child that keeps its own background unless told otherwise. */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QAbstractScrollArea::viewport {{ background: transparent; }}

QComboBox QAbstractItemView {{
    selection-background-color: {p.accent};
    selection-color: {p.accent_text};
}}
"""
