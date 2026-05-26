"""Programmatic app icon — camera-frame brackets with a center dot."""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap


def _draw_icon(size: int) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Background: dark rounded square
    margin = size * 0.06
    p.setBrush(QBrush(QColor(32, 34, 40)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(
        QRectF(margin, margin, size - 2 * margin, size - 2 * margin),
        size * 0.17, size * 0.17,
    )

    # Four camera-frame brackets
    bracket_len = size * 0.18
    inset = size * 0.22
    pen = QPen(QColor(240, 240, 240))
    pen.setWidthF(size * 0.045)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    # top-left
    p.drawLine(int(inset), int(inset), int(inset + bracket_len), int(inset))
    p.drawLine(int(inset), int(inset), int(inset), int(inset + bracket_len))
    # top-right
    p.drawLine(int(size - inset), int(inset), int(size - inset - bracket_len), int(inset))
    p.drawLine(int(size - inset), int(inset), int(size - inset), int(inset + bracket_len))
    # bottom-left
    p.drawLine(int(inset), int(size - inset), int(inset + bracket_len), int(size - inset))
    p.drawLine(int(inset), int(size - inset), int(inset), int(size - inset - bracket_len))
    # bottom-right
    p.drawLine(int(size - inset), int(size - inset), int(size - inset - bracket_len), int(size - inset))
    p.drawLine(int(size - inset), int(size - inset), int(size - inset), int(size - inset - bracket_len))

    # Accent dot at center
    p.setBrush(QBrush(QColor(255, 90, 90)))
    p.setPen(Qt.PenStyle.NoPen)
    r = size * 0.07
    p.drawEllipse(QRectF(size / 2 - r, size / 2 - r, r * 2, r * 2))

    p.end()
    return pix


def make_app_icon() -> QIcon:
    """Multi-resolution QIcon — macOS picks the best size for Dock / app switcher."""
    icon = QIcon()
    for s in (16, 32, 64, 128, 256, 512, 1024):
        icon.addPixmap(_draw_icon(s))
    return icon
