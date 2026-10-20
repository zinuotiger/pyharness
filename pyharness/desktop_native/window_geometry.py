"""Fit the native frame inside its current screen, using actual Qt decoration."""
from PySide6.QtCore import QPoint
from shiboken6 import isValid


def fit_available_screen(window):
    if not isValid(window) or not window.isVisible() or window.isMaximized() or window.isMinimized():
        return
    screen = window.screen()
    if screen is None:
        return
    available = screen.availableGeometry().adjusted(8, 8, -8, -8)
    frame = window.frameGeometry()
    decoration_width = max(0, frame.width() - window.width())
    decoration_height = max(0, frame.height() - window.height())
    width = min(window.width(), max(1, available.width() - decoration_width))
    height = min(window.height(), max(1, available.height() - decoration_height))
    window.resize(width, height)
    frame = window.frameGeometry()
    x = max(available.left(), min(frame.left(), available.right() - frame.width() + 1))
    y = max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1))
    window.move(window.pos() + QPoint(x - frame.left(), y - frame.top()))
