"""Small custom Qt widgets for the offline TaskTimeDesk interface."""
from __future__ import annotations

import random
import math
from datetime import datetime

from PySide6.QtCore import Qt, QPoint, QRect, QRectF, QTimer, QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QBrush, QPen, QFont, QCursor
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QWidget, QSizeGrip, QComboBox, QDateEdit, QSpinBox, QSlider

from store import parse


PIXELS = {
    "clock": ["..####..", ".#....#.", "#..#...#", "#..#...#", "#..###.#", "#......#", ".#....#.", "..####.."],
    "note": [".######.", ".#....#.", ".#.##.#.", ".#....#.", ".#.##.#.", ".#....#.", ".######.", "........"],
    "check": ["........", "......##", ".....##.", "#...##..", "##.##...", ".###....", "..#.....", "........"],
    "folder": ["........", ".###....", ".#..###.", ".######.", ".######.", ".######.", ".######.", "........"],
    "gear": [".#.##.#.", "########", "##.##.##", "###..###", "###..###", "##.##.##", "########", ".#.##.#."],
    "play": ["........", ".##.....", ".####...", ".######.", ".######.", ".####...", ".##.....", "........"],
    "stop": ["........", ".######.", ".######.", ".######.", ".######.", ".######.", ".######.", "........"],
    "tomato": ["...##...", ".#.##.#.", "..####..", ".######.", "########", "########", ".######.", "..####.."],
    "spark": ["...##...", "...##...", "#..##..#", "########", "########", "#..##..#", "...##...", "...##..."],
    "pin": ["..####..", "...##...", "..####..", ".######.", "...##...", "...##...", "...##...", "....#..."],
}


def pixel_icon(name: str, color: str = "#f27791", scale: int = 3) -> QIcon:
    pattern = PIXELS.get(name, PIXELS["clock"])
    pixmap = QPixmap(8 * scale, 8 * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    for y, line in enumerate(pattern):
        for x, char in enumerate(line):
            if char == "#":
                painter.drawRect(x * scale, y * scale, scale, scale)
    painter.end()
    return QIcon(pixmap)


def theme_sheet(dark: bool, accent: QColor, alpha: int) -> str:
    background = "#101116" if dark else "#f3f4f7"
    panel = "#1b1d25" if dark else "#ffffff"
    elevated = "#252833" if dark else "#eaedf3"
    text = "#f4f4f7" if dark else "#191b25"
    muted = "#9a9da9" if dark else "#667080"
    border = "#343742" if dark else "#dbe0e8"
    accent_rgba = f"rgba({accent.red()},{accent.green()},{accent.blue()},{alpha})"
    accent_rgb = accent.name()
    return f"""
        QWidget {{ background: {background}; color: {text}; font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-size: 13px; }}
        QLabel {{ background: transparent; border: 0; }}
        QCheckBox {{ background: transparent; border: 0; }}
        QFrame#card, QFrame#floatingCard {{ background: {panel}; border: 1px solid {border}; border-radius: 12px; }}
        QLabel#muted {{ color: {muted}; background: transparent; }}
        QLabel#bigTimer {{ font-size: 31px; font-weight: 700; letter-spacing: 1px; background: transparent; }}
        QLabel#sectionTitle {{ font-size: 15px; font-weight: 700; background: transparent; }}
        QPushButton {{ background: {elevated}; border: 1px solid {border}; border-radius: 8px; padding: 7px 10px; min-height: 23px; }}
        QPushButton:hover {{ border-color: {accent_rgb}; }}
        QPushButton:disabled {{ color: {muted}; }}
        QPushButton#primary {{ background: {accent_rgba}; border: 1px solid {accent_rgb}; color: {'#ffffff' if dark else '#191b25'}; font-weight: 700; }}
        QPushButton#navActive {{ background: {accent_rgba}; border: 1px solid {accent_rgb}; font-weight: 700; }}
        QPushButton#nav, QPushButton#navActive {{ padding: 3px 2px; min-height: 0; }}
        QPushButton#nav {{ background: transparent; border: 0; color: {muted}; }}
        QPushButton#windowControl {{ background: transparent; border: 0; border-radius: 5px; padding: 0; min-height: 0; font-size: 15px; }}
        QPushButton#windowControl:hover {{ background: {elevated}; }}
        QPushButton#titleQuick {{ background: {elevated}; border: 1px solid {border}; border-radius: 7px; padding: 2px 5px; min-height: 0; }}
        QPushButton#spinStep {{ background: {elevated}; border: 1px solid {border}; border-radius: 4px; padding: 0; min-height: 0; font-size: 9px; }}
        QWidget#spinControls, QWidget#spinButtons {{ background: transparent; border: 0; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDateEdit, QSpinBox, QKeySequenceEdit {{ background: {elevated}; color: {text}; border: 1px solid {border}; border-radius: 7px; padding: 5px; selection-background-color: {accent_rgb}; }}
        QComboBox QAbstractItemView {{ background: {panel}; color: {text}; selection-background-color: {accent_rgb}; }}
        QComboBox::drop-down {{ width: 15px; subcontrol-origin: padding; subcontrol-position: center right; border: 0; margin-right: 5px; }}
        QComboBox::down-arrow {{ width: 7px; height: 5px; }}
        QFormLayout QLabel {{ background: transparent; border: 0; }}
        QTreeWidget, QTableWidget, QListWidget {{ background: {panel}; alternate-background-color: {elevated}; color: {text}; border: 1px solid {border}; border-radius: 8px; outline: 0; selection-background-color: {accent_rgba}; }}
        QHeaderView::section {{ background: {elevated}; color: {muted}; border: 0; padding: 5px; }}
        QScrollArea {{ border: 0; }}
        QScrollBar:vertical {{ background: {background}; width: 9px; }}
        QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 28px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QToolTip {{ background: {elevated}; color: {text}; border: 1px solid {accent_rgb}; padding: 5px; }}
    """


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelDateEdit(QDateEdit):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelSlider(QSlider):
    def wheelEvent(self, event):
        event.ignore()


class BarChartWidget(QWidget):
    """Compact duration bars with hover details for a chosen calendar range."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(205)
        self.setMouseTracking(True)
        self.labels = []
        self.values = []
        self.highlight = -1
        self.accent = QColor("#f27791")
        self.dark = True
        self.hit_rects = []

    def set_data(self, labels, values, highlight, accent, dark):
        self.labels = list(labels)
        self.values = list(values)
        self.highlight = highlight
        self.accent = QColor(accent)
        self.dark = dark
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ink = QColor("#aeb2c0" if self.dark else "#657080")
        grid = QColor("#414550" if self.dark else "#d6dce6")
        left, right = 35, self.width() - 12
        top, bottom = 17, self.height() - 32
        plot_width = max(1, right - left)
        plot_height = max(1, bottom - top)
        maximum = max(self.values, default=0)
        maximum = max(60, maximum)
        painter.setPen(QPen(grid, 1))
        painter.drawLine(left, bottom, right, bottom)
        painter.setPen(ink)
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(QRect(0, top - 5, left - 4, 15), Qt.AlignmentFlag.AlignRight, f"{maximum / 3600:g}h")
        painter.drawText(QRect(0, bottom - 13, left - 4, 15), Qt.AlignmentFlag.AlignRight, "0")
        self.hit_rects = []
        if not self.labels:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无计时记录")
            return
        slot = plot_width / len(self.labels)
        width = max(8, min(34, slot * 0.58))
        for index, (label, seconds) in enumerate(zip(self.labels, self.values)):
            x = left + slot * (index + 0.5)
            bar_height = max(2, plot_height * seconds / maximum) if seconds else 0
            color = QColor(self.accent)
            color.setAlpha(245 if index == self.highlight else 145)
            if bar_height:
                rect = QRectF(x - width / 2, bottom - bar_height, width, bar_height)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(ink)
            painter.drawText(QRectF(x - slot / 2, bottom + 6, slot, 20), Qt.AlignmentFlag.AlignHCenter, label)
            self.hit_rects.append((QRectF(x - slot / 2, top, slot, bottom - top + 25), label, seconds))
        painter.end()

    def mouseMoveEvent(self, event):
        tooltip = ""
        for rect, label, seconds in self.hit_rects:
            if rect.contains(event.position()):
                hours, remainder = divmod(int(seconds), 3600)
                minutes = remainder // 60
                tooltip = f"{label} · {hours:02d}:{minutes:02d}"
                break
        self.setToolTip(tooltip)
        super().mouseMoveEvent(event)


class TimelineWidget(QWidget):
    """One-row, 24-hour task strip with hover details and check-in marks."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(104)
        self.setMouseTracking(True)
        self.day = None
        self.entries = []
        self.checkin = None
        self.dark = True
        self.accent = QColor("#f27791")
        self.hit_rects = []

    def set_data(self, day, entries, checkin, dark, accent):
        self.day, self.entries, self.checkin = day, entries, checkin
        self.dark, self.accent = dark, QColor(accent)
        self.update()

    def paintEvent(self, _event):
        if not self.day:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        left, right = 18, self.width() - 18
        width = max(1, right - left)
        base = QColor("#343742" if self.dark else "#dbe0e8")
        ink = QColor("#9a9da9" if self.dark else "#667080")
        p.fillRect(QRect(left, 47, width, 18), base)
        p.setPen(ink)
        font = QFont("Segoe UI", 8)
        p.setFont(font)
        for hour in (0, 6, 12, 18, 24):
            x = left + round(width * hour / 24)
            p.drawText(x - 12, 22, f"{hour:02d}")
            p.drawLine(x, 39, x, 69)
        day_start = datetime.fromisoformat(self.day).astimezone()
        day_end = day_start.replace(hour=0, minute=0, second=0) + __import__("datetime").timedelta(days=1)
        self.hit_rects = []
        for entry in self.entries:
            start = max(parse(entry["start_at"]), day_start)
            end = min(parse(entry["end_at"]), day_end)
            if end <= start:
                continue
            x1 = left + round(width * (start - day_start).total_seconds() / 86400)
            x2 = left + round(width * (end - day_start).total_seconds() / 86400)
            rect = QRect(x1, 47, max(2, x2 - x1), 18)
            p.fillRect(rect, QColor(entry["color"]))
            self.hit_rects.append((rect, f"{entry['task_name']}  {start:%H:%M}–{end:%H:%M}"))
        for shift in self.checkin or []:
            for key, label, color in (("start_at", "开工", QColor("#4ecba6")), ("end_at", "收工", QColor("#f0bc69"))):
                value = shift[key]
                if value:
                    stamp = parse(value)
                    if day_start <= stamp < day_end:
                        x = left + round(width * (stamp - day_start).total_seconds() / 86400)
                        p.setBrush(color)
                        p.setPen(Qt.PenStyle.NoPen)
                        p.drawPolygon([QPoint(x, 72), QPoint(x - 5, 80), QPoint(x + 5, 80)])
                        p.setPen(color)
                        p.drawText(max(left, min(right - 84, x - 24)), 96, f"{label} {stamp:%H:%M}")
                        self.hit_rects.append((QRect(x - 7, 69, 14, 14), f"{label} {stamp:%H:%M}"))
        p.end()

    def mouseMoveEvent(self, event):
        tooltip = ""
        for rect, label in reversed(self.hit_rects):
            if rect.contains(event.position().toPoint()):
                tooltip = label
                break
        self.setToolTip(tooltip)
        super().mouseMoveEvent(event)


class ConfettiDialog(QDialog):
    def __init__(self, parent, accent, title="子待办全部完成！", subtitle="目标完成仍由你手动勾选"):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(310, 160)
        self.accent = QColor(accent)
        self.title, self.subtitle = title, subtitle
        self.pieces = [(random.randrange(0, 300), random.randrange(0, 160), random.choice(["#f27791", "#f0bc69", "#4ecba6", "#9a8df0"])) for _ in range(42)]
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(70)
        QTimer.singleShot(2800, self.close)

    def animate(self):
        self.pieces = [(x, (y + 4) % 160, color) for x, y, color in self.pieces]
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1b1d25"))
        for x, y, color in self.pieces:
            p.fillRect(x, y, 6, 6, QColor(color))
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Microsoft YaHei UI", 14, QFont.Weight.Bold))
        p.drawText(QRect(15, 47, 280, 35), Qt.AlignmentFlag.AlignCenter, self.title)
        p.setPen(QColor("#bcc0cc"))
        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.drawText(QRect(15, 87, 280, 35), Qt.AlignmentFlag.AlignCenter, self.subtitle)
        p.end()


class DraggableTitleBar(QWidget):
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        window = self.window()
        window.showNormal() if window.isMaximized() else window.showMaximized()
        super().mouseDoubleClickEvent(event)


class StyledSizeGrip(QSizeGrip):
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(16, 16)

    def paintEvent(self, _event):
        painter = QPainter(self)
        pen = QPen(QColor("#858997"))
        pen.setWidthF(1.5)
        painter.setPen(pen)
        for offset in (5, 9, 13):
            painter.drawLine(self.width() - offset, self.height() - 2, self.width() - 2, self.height() - offset)
        painter.end()


class FloatingBall(QWidget):
    def __init__(self, host):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.host = host
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(64, 64)
        self.drag_origin = None
        self.dragged = False
        self.popup = QFrame(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.popup.setObjectName("floatingCard")
        self.popup_layout = QVBoxLayout(self.popup)
        self.popup_layout.setContentsMargins(10, 10, 10, 10)
        self.popup_layout.setSpacing(4)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._hide_if_outside)
        self.popup.enterEvent = lambda event: self.hide_timer.stop()
        self.popup.leaveEvent = lambda event: self.hide_timer.start(450)
        self.setToolTip("时刻 · 悬停选择任务，点击打开主窗口")

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent = QColor(self.host.accent)
        accent.setAlpha(int(255 * self.host.float_opacity / 100))
        state = self.host.store.pomo()
        if state:
            duration = {"focus": state["focus_minutes"], "break": state["break_minutes"], "long_break": state["long_minutes"]}[state["phase"]] * 60
            elapsed = min(duration, max(0, int((datetime.now().astimezone() - parse(state["phase_start"])).total_seconds())))
            remaining = max(0, duration - elapsed)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#25262b"))
            p.drawEllipse(QRect(2, 2, 60, 60))
            ticks = 36
            for index in range(ticks):
                angle = 2 * math.pi * index / ticks - math.pi / 2
                outer, inner = 27, 23
                x1, y1 = 32 + outer * math.cos(angle), 32 + outer * math.sin(angle)
                x2, y2 = 32 + inner * math.cos(angle), 32 + inner * math.sin(angle)
                color = accent if index < math.ceil(ticks * elapsed / duration) else QColor("#41434a")
                p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPoint(round(x1), round(y1)), QPoint(round(x2), round(y2)))
            p.setPen(QColor("#ffffff"))
            p.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            p.drawText(QRect(7, 23, 50, 18), Qt.AlignmentFlag.AlignCenter, f"{remaining // 60:02d}:{remaining % 60:02d}")
            p.end()
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        p.drawEllipse(QRect(2, 2, 60, 60))
        icon = pixel_icon("clock", "#ffffff", 3)
        icon.paint(p, QRect(20, 17, 24, 24))
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        label = "RUN" if self.host.store.active() else "READY"
        p.drawText(QRect(5, 42, 54, 14), Qt.AlignmentFlag.AlignCenter, label)
        p.end()

    def enterEvent(self, event):
        self.hide_timer.stop()
        self.show_tasks()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hide_timer.start(450)
        super().leaveEvent(event)

    def _hide_if_outside(self):
        point = QCursor.pos()
        if not self.geometry().contains(point) and not self.popup.geometry().contains(point):
            self.popup.hide()
            self.host.dock_ball()

    def show_tasks(self):
        self.host.undock_ball()
        while self.popup_layout.count():
            item = self.popup_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        title = QLabel("快速开始")
        title.setObjectName("sectionTitle")
        self.popup_layout.addWidget(title)
        if self.host.store.pomo():
            pomo = QPushButton("停止番茄钟")
            pomo.setIcon(pixel_icon("stop", self.host.accent, 2))
            pomo.clicked.connect(self.host.toggle_pomodoro)
            self.popup_layout.addWidget(pomo)
        else:
            pomo = QPushButton("开始番茄钟")
            pomo.setIcon(pixel_icon("tomato", self.host.accent, 2))
            pomo.clicked.connect(self.host.toggle_pomodoro)
            self.popup_layout.addWidget(pomo)
        tasks = [t for t in self.host.store.tasks() if t["pinned"] and not t["system"]][:8]
        for task in tasks:
            button = QPushButton(task["name"])
            button.setIcon(pixel_icon("stop" if self.host.store.active() and self.host.store.active()["task_id"] == task["id"] else "play", task["color"], 2))
            button.clicked.connect(lambda _checked=False, tid=task["id"]: self.host.toggle_task(tid))
            self.popup_layout.addWidget(button)
        if not tasks:
            self.popup_layout.addWidget(QLabel("在任务页设置悬浮任务"))
        note = QPushButton("快速便签")
        note.setIcon(pixel_icon("note", self.host.accent, 2))
        note.clicked.connect(self.host.quick_note)
        self.popup_layout.addWidget(note)
        self.popup.adjustSize()
        self.popup.setMinimumWidth(190)
        screen = self.screen().availableGeometry()
        x = self.x() + 70 if self.x() < screen.center().x() else self.x() - self.popup.width() - 8
        y = min(self.y(), screen.bottom() - self.popup.height())
        self.popup.move(x, y)
        self.popup.show()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_origin = event.globalPosition().toPoint() - self.pos()
            self.dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.dragged = True
            self.move(event.globalPosition().toPoint() - self.drag_origin)
            self.popup.hide()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.dragged:
                self.host.save_ball_position()
                self.host.dock_ball()
            else:
                self.host.show_main()
            self.drag_origin = None
        super().mouseReleaseEvent(event)

    def hideEvent(self, event):
        self.popup.hide()
        super().hideEvent(event)
