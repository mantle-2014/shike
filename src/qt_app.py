"""PySide6 Windows UI for TaskTimeDesk. All application data stays local."""
from __future__ import annotations

import ctypes
import faulthandler
import os
import sqlite3
import sys
import traceback
import uuid
import math
from datetime import date, datetime, time, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QDate, QTimer, QAbstractNativeEventFilter, QLockFile, QProcess, QSize, QEvent, QRect, QRectF
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QKeySequence, QPainter, QPen, QFont
from PySide6.QtWidgets import (
    QApplication, QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QScrollArea, QStackedWidget, QComboBox, QDateEdit, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QTextEdit, QCheckBox,
    QSpinBox, QColorDialog, QSlider, QKeySequenceEdit, QMessageBox, QInputDialog,
    QFileDialog, QSystemTrayIcon, QMenu, QDialog, QFormLayout, QGridLayout,
    QAbstractItemView, QAbstractSpinBox, QStyledItemDelegate, QStyleOptionButton, QStyle,
    QCalendarWidget,
)

from store import Store, data_directory, set_data_directory, prepare_data_directory, now, parse, seconds_between
from qt_widgets import (
    pixel_icon, theme_sheet, TimelineWidget, ConfettiDialog, FloatingBall,
    DraggableTitleBar, StyledSizeGrip, BarChartWidget, NoWheelComboBox,
    NoWheelDateEdit, NoWheelSpinBox, NoWheelSlider,
)


def fmt(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def bounds(day):
    start = datetime.combine(day, time.min).astimezone()
    return start.isoformat(timespec="seconds"), (start + timedelta(days=1)).isoformat(timespec="seconds")


def next_month(day):
    return day.replace(year=day.year + 1, month=1, day=1) if day.month == 12 else day.replace(month=day.month + 1, day=1)


def chart_periods(chosen, mode):
    """Return chart buckets and the selected comparison interval as local dates."""
    if mode == "日/周":
        week = chosen - timedelta(days=chosen.weekday())
        buckets = [(week + timedelta(days=i), week + timedelta(days=i + 1), "周" + "一二三四五六日"[i]) for i in range(7)]
        return buckets, chosen.weekday(), (chosen, chosen + timedelta(days=1)), (chosen - timedelta(days=1), chosen)
    if mode == "周/月":
        month = chosen.replace(day=1)
        month_end = next_month(month)
        cursor = month - timedelta(days=month.weekday())
        buckets = []
        while cursor < month_end:
            start = max(cursor, month)
            end = min(cursor + timedelta(days=7), month_end)
            if end > start:
                buckets.append((start, end, f"{start.month}/{start.day}"))
            cursor += timedelta(days=7)
        week = chosen - timedelta(days=chosen.weekday())
        highlight = next((i for i, (start, end, _label) in enumerate(buckets) if start <= chosen < end), -1)
        return buckets, highlight, (week, week + timedelta(days=7)), (week - timedelta(days=7), week)
    year = chosen.replace(month=1, day=1)
    buckets = []
    for month_index in range(12):
        start = year.replace(month=month_index + 1)
        buckets.append((start, next_month(start), f"{month_index + 1}月"))
    current = chosen.replace(day=1)
    previous = (current - timedelta(days=1)).replace(day=1)
    return buckets, chosen.month - 1, (current, next_month(current)), (previous, current)


def make_card(title=None):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 13, 14, 14)
    layout.setSpacing(9)
    if title:
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
    return frame, layout


def mini_label(text):
    label = QLabel(text)
    label.setObjectName("muted")
    return label


def preview_lines(text, metrics, width, limit=3):
    """Wrap a tree caption to at most three visible lines without changing stored text."""
    result = []
    line = ""
    for char in text.replace("\r", ""):
        if char == "\n" or (line and metrics.horizontalAdvance(line + char) > width):
            result.append(line)
            line = ""
            if len(result) == limit:
                break
            if char == "\n":
                continue
        line += char
    else:
        if line or not result:
            result.append(line)
    if len(result) > limit:
        result = result[:limit]
    if len(result) == limit and "\n".join(result).replace("\n", "") != text.replace("\r", "").replace("\n", ""):
        result[-1] = metrics.elidedText(result[-1] + "…", Qt.TextElideMode.ElideRight, width)
    return "\n".join(result)


def association_label(value):
    return str(value or "")[:8]


def configure_association_columns(tree):
    header = tree.header()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    tree.setColumnWidth(1, tree.fontMetrics().horizontalAdvance("测" * 8) + 24)
    tree.headerItem().setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)


def startup_enabled():
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            value, _ = winreg.QueryValueEx(key, "ShikeTaskTimeDesk")
            return value == f'"{Path(sys.executable).resolve()}"'
    except FileNotFoundError:
        return False


def set_startup_enabled(enabled):
    import winreg
    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "ShikeTaskTimeDesk", 0, winreg.REG_SZ, f'"{Path(sys.executable).resolve()}"')
        else:
            try:
                winreg.DeleteValue(key, "ShikeTaskTimeDesk")
            except FileNotFoundError:
                pass


def scroll_page():
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(2, 2, 8, 12)
    layout.setSpacing(12)
    scroll.setWidget(content)
    return scroll, layout


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        if sys.platform != "win32":
            return False, 0
        import ctypes.wintypes
        try:
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == 0x5444:
                self.callback()
                return True, 0
        except Exception:
            pass
        return False, 0


class QuickNoteDialog(QDialog):
    def __init__(self, host, note_id=None):
        super().__init__(host)
        self.host = host
        self.note_id = note_id
        self.item_rows = []
        self.setWindowTitle("编辑便签" if note_id else "快速便签")
        self.setWindowIcon(pixel_icon("note", host.accent))
        self.resize(370, 470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        title_row = QHBoxLayout()
        title = QLabel("编辑便签" if note_id else "随手记录")
        title.setObjectName("sectionTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.pin_button = QPushButton()
        self.pin_button.setIcon(pixel_icon("pin", host.accent, 2))
        self.pin_button.setToolTip("仅将此便签固定在最前")
        self.pin_button.setCheckable(True)
        self.pin_button.setFixedSize(30, 30)
        self.pin_button.toggled.connect(self.set_pinned)
        title_row.addWidget(self.pin_button)
        layout.addLayout(title_row)
        self.body = QTextEdit()
        self.body.setPlaceholderText("写下想法、线索或备注……")
        self.body.setMinimumHeight(95)
        layout.addWidget(self.body)
        layout.addWidget(mini_label("候选条目 · 每条都有独立勾选框"))
        self.items_area = QScrollArea()
        self.items_area.setWidgetResizable(True)
        self.items_host = QWidget()
        self.items_layout = QVBoxLayout(self.items_host)
        self.items_layout.setContentsMargins(2, 2, 2, 2)
        self.items_layout.addStretch()
        self.items_area.setWidget(self.items_host)
        self.items_area.setMinimumHeight(100)
        layout.addWidget(self.items_area)
        row = QHBoxLayout()
        self.new_item = QLineEdit()
        self.new_item.setPlaceholderText("新增可勾选条目")
        self.new_item.returnPressed.connect(self.add_item)
        row.addWidget(self.new_item)
        add = QPushButton("添加")
        add.clicked.connect(self.add_item)
        row.addWidget(add)
        layout.addLayout(row)
        self.goal = NoWheelComboBox()
        self.goal.addItem("收集箱", None)
        for goal in host.store.goals(date.today().isoformat()):
            self.goal.addItem(goal["title"], goal["id"])
        if note_id is not None:
            note = host.store.one("SELECT * FROM notes WHERE id=?", (note_id,))
            if note:
                self.body.setPlainText(note["body"])
                index = self.goal.findData(note["goal_id"])
                if index < 0 and note["goal_id"] is not None:
                    old_goal = host.store.one("SELECT title,day FROM goals WHERE id=?", (note["goal_id"],))
                    if old_goal:
                        self.goal.addItem(f"{old_goal['title']} · {old_goal['day']}", note["goal_id"])
                        index = self.goal.count() - 1
                if index >= 0:
                    self.goal.setCurrentIndex(index)
                for item in host.store.note_items(note_id):
                    self._append_item(item["title"], bool(item["done"]), item["id"])
        save = QPushButton("保存便签")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(self.goal, 1)
        bottom.addWidget(save, 1)
        layout.addLayout(bottom)
        self.body.setFocus()

    def set_pinned(self, pinned):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pinned)
        self.show()
        self.raise_()
        self.activateWindow()

    def add_item(self):
        value = self.new_item.text().strip()
        if not value:
            return
        self._append_item(value)
        self.new_item.clear()

    def _append_item(self, value, done=False, item_id=None):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        check = QCheckBox()
        check.setChecked(done)
        row.addWidget(check)
        title = QLineEdit(value)
        row.addWidget(title, 1)
        remove = QPushButton("×")
        remove.setFixedWidth(26)
        remove.setToolTip("删除此条目")
        row.addWidget(remove)
        record = {"widget": holder, "check": check, "title": title, "id": item_id}
        self.item_rows.append(record)
        remove.clicked.connect(lambda: self._remove_item(record))
        self.items_layout.insertWidget(self.items_layout.count() - 1, holder)

    def _remove_item(self, record):
        self.item_rows.remove(record)
        record["widget"].deleteLater()

    def save(self):
        body = self.body.toPlainText().strip()
        pending = self.new_item.text().strip()
        if pending:
            choice = QMessageBox.question(self, "候选条目", "是否添加条目？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Yes:
                self._append_item(pending)
                self.new_item.clear()
        items = [{"id": row["id"], "title": row["title"].text().strip(), "done": row["check"].isChecked()}
                 for row in self.item_rows if row["title"].text().strip()]
        if not body and not items:
            QMessageBox.information(self, "便签", "请填写内容或添加条目。")
            return
        if self.note_id is None:
            note_id = self.host.store.add_note_with_items(body or "待办便签", [row["title"] for row in items], self.goal.currentData())
            for item, saved in zip(items, self.host.store.note_items(note_id)):
                if item["done"]:
                    self.host.store.set_note_item_done(saved["id"], True)
        else:
            self.host.store.update_note(self.note_id, body or "待办便签", self.goal.currentData(), items)
        self.accept()
        self.host.refresh_notes()


class TaskFolderTree(QTreeWidget):
    def __init__(self, on_move):
        super().__init__()
        self.on_move = on_move
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def dropEvent(self, event):
        source = self.currentItem()
        target = self.itemAt(event.position().toPoint())
        source_data = source.data(0, Qt.ItemDataRole.UserRole) if source else None
        target_data = target.data(0, Qt.ItemDataRole.UserRole) if target else None
        if not source_data or source_data[0] != "task" or not target_data:
            event.ignore()
            return
        if target_data[0] == "task":
            target = target.parent()
            target_data = target.data(0, Qt.ItemDataRole.UserRole) if target else None
        if target_data and target_data[0] == "folder":
            self.on_move(source_data[1], target_data[1])
            event.acceptProposedAction()
        else:
            event.ignore()


class RichTreeDelegate(QStyledItemDelegate):
    def __init__(self, tree, notes=False):
        super().__init__(tree)
        self.tree = tree
        self.notes = notes

    def _separator(self, index):
        if not self.notes:
            return False
        item = self.tree.itemFromIndex(index)
        return bool(item and ((item.parent() and item.parent().child(item.parent().childCount() - 1) is item) or (not item.parent() and item.childCount() == 0)))

    def paint(self, painter, option, index):
        painter.save()
        dark = self.tree.palette().color(self.tree.backgroundRole()).lightness() < 130
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect, QColor("#3b3442" if dark else "#eadce5"))
        if index.column() == 1 and self.notes and not index.parent().isValid():
            label = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            goal_id = index.data(Qt.ItemDataRole.UserRole)
            colors = ("#677ed8", "#43a997", "#cc8a56", "#a078c6", "#c16f88")
            color = QColor(colors[int(goal_id or 0) % len(colors)])
            color.setAlpha(90 if dark else 70)
            fm = option.fontMetrics
            badge_width = min(option.rect.width() - 9, fm.horizontalAdvance(label) + 15)
            badge = QRectF(option.rect.center().x() - badge_width / 2, option.rect.top() + 4, badge_width, min(23, option.rect.height() - 8))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(badge, 8, 8)
            painter.setPen(QColor("#f7f7fa" if dark else "#252633"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, fm.elidedText(label, Qt.TextElideMode.ElideRight, badge_width - 10))
        elif index.column() == 0:
            state = index.data(Qt.ItemDataRole.CheckStateRole)
            text_x = option.rect.left() + 4
            if state is not None:
                check = QStyleOptionButton()
                check.rect = QRect(option.rect.left() + 3, option.rect.top() + 6, 16, 16)
                check.state = QStyle.StateFlag.State_Enabled | (QStyle.StateFlag.State_On if state == Qt.CheckState.Checked else QStyle.StateFlag.State_Off)
                self.tree.style().drawControl(QStyle.ControlElement.CE_CheckBox, check, painter, self.tree)
                text_x += 22
            painter.setPen(QColor("#f4f4f7" if dark else "#191b25"))
            text_rect = QRect(text_x, option.rect.top() + 4, max(20, option.rect.right() - text_x - 3), option.rect.height() - 8)
            painter.drawText(text_rect, Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignVCenter, str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
        else:
            painter.setPen(QColor("#aeb2c0" if dark else "#596274"))
            painter.drawText(option.rect.adjusted(3, 2, -4, -2), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
                             option.fontMetrics.elidedText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""), Qt.TextElideMode.ElideRight, option.rect.width() - 9))
        if self._separator(index):
            painter.setPen(QPen(QColor("#3b3e49" if dark else "#d6dce6"), 1))
            painter.drawLine(option.rect.left() + 5, option.rect.bottom() - 1, option.rect.right() - 5, option.rect.bottom() - 1)
        painter.restore()


class RichTreeWidget(QTreeWidget):
    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if item and event.button() == Qt.MouseButton.LeftButton:
            rect = self.visualRect(self.indexFromItem(item, 0))
            point = event.position().toPoint()
            if rect.left() + 3 <= point.x() <= rect.left() + 22 and rect.top() + 5 <= point.y() <= rect.top() + 25:
                self.setCurrentItem(item)
                item.setCheckState(0, Qt.CheckState.Unchecked if item.checkState(0) == Qt.CheckState.Checked else Qt.CheckState.Checked)
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and self.currentItem():
            item = self.currentItem()
            item.setCheckState(0, Qt.CheckState.Unchecked if item.checkState(0) == Qt.CheckState.Checked else Qt.CheckState.Checked)
            event.accept()
            return
        super().keyPressEvent(event)


class StartupSplash(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedSize(180, 154)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)
        self.timer.start(45)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())

    def advance(self):
        self.angle = (self.angle + 12) % 360
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#45404c"), 1))
        painter.setBrush(QColor("#1b1d25"))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 14, 14)
        pixel_icon("clock", "#f27791", 8).paint(painter, QRect(58, 22, 64, 64))
        angle = math.radians(self.angle - 90)
        painter.setPen(QPen(QColor("#ffffff"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(90, 54, 90 + round(21 * math.cos(angle)), 54 + round(21 * math.sin(angle)))
        painter.setPen(QColor("#f4f4f7"))
        painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
        painter.drawText(QRect(10, 108, 160, 24), Qt.AlignmentFlag.AlignCenter, "时刻正在启动…")
        painter.end()


class MainWindow(QWidget):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.store = Store()
        self.dark = self.store.preference("theme", "dark") == "dark"
        self.accent = self.store.preference("accent", "#f27791")
        self.accent_alpha = int(self.store.preference("accent_alpha", "220"))
        self.float_opacity = int(self.store.preference("float_opacity", "92"))
        self.ball_enabled = self.store.preference("ball_enabled", "1") == "1"
        self.always_on_top = self.store.preference("always_on_top", "0") == "1"
        self.view_day = date.today()
        self.stats_anchor = date.today()
        self.last_calendar_day = self.view_day
        self.nav_buttons = []
        self.goal_loading = False
        self.notes_loading = False
        self.note_expanded = set()
        self.goal_expanded = set()
        self.ball_docked = False
        self._quitting = False
        self._hotkey_string = self.store.preference("note_hotkey", "Ctrl+Alt+N")
        self._hotkey_registered = False
        self._hotkey_filter = HotkeyFilter(lambda: self.quick_note(True))
        self.app.installNativeEventFilter(self._hotkey_filter)
        self.app.installEventFilter(self)
        self.setWindowTitle("时刻 · TaskTimeDesk")
        self.setWindowIcon(pixel_icon("clock", self.accent))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        if self.always_on_top:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumSize(420, 610)
        self.resize(510, 770)
        self.setMouseTracking(True)
        self._resize_margin = 9
        self._build_ui()
        self._create_tray()
        self.ball = FloatingBall(self)
        self._apply_theme()
        bx = int(self.store.preference("ball_x", "65"))
        by = int(self.store.preference("ball_y", "200"))
        self.ball.move(bx, by)
        if self.ball_enabled:
            self.ball.show()
        self._register_hotkey(self._hotkey_string, initial=True)
        self.refresh_all()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 17)
        outer.setSpacing(8)
        self.title_bar = DraggableTitleBar()
        header = QHBoxLayout(self.title_bar)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(5)
        logo = QLabel()
        logo.setPixmap(pixel_icon("clock", self.accent, 4).pixmap(32, 32))
        logo.setObjectName("appLogo")
        self.logo = logo
        header.addWidget(logo)
        heading = QVBoxLayout()
        name = QLabel("时刻")
        name.setStyleSheet("font-size: 20px; font-weight: 800;")
        heading.addWidget(name)
        header.addLayout(heading)
        header.addStretch()
        quick = QPushButton("快速便签")
        quick.setObjectName("titleQuick")
        quick.setIcon(pixel_icon("note", self.accent, 2))
        quick.setToolTip("快速便签")
        quick.setFixedSize(94, 28)
        quick.clicked.connect(self.quick_note)
        header.addWidget(quick)
        header.addSpacing(12)
        self.pin_button = QPushButton()
        self.pin_button.setObjectName("windowControl")
        self.pin_button.setIcon(pixel_icon("pin", self.accent if self.always_on_top else "#8b91a3", 2))
        self.pin_button.setFixedSize(25, 27)
        self.pin_button.setToolTip("取消固定在最前" if self.always_on_top else "固定在最前")
        self.pin_button.clicked.connect(self.toggle_always_on_top)
        header.addWidget(self.pin_button)
        for text, callback, hint in (("−", self.showMinimized, "最小化"), ("□", self.toggle_maximized, "最大化或还原"), ("×", self.close, "关闭到通知区域")):
            button = QPushButton(text)
            button.setObjectName("windowControl")
            button.setFixedSize(25, 27)
            button.setToolTip(hint)
            button.clicked.connect(callback)
            header.addWidget(button)
        outer.addWidget(self.title_bar)

        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        self._today_page()
        self._goals_page()
        self._tasks_page()
        self._notes_page()
        self._settings_page()

        nav = QHBoxLayout()
        nav.setSpacing(2)
        for index, (name, icon_name) in enumerate((("今日", "clock"), ("目标", "check"), ("任务", "folder"), ("便签", "note"), ("设置", "gear"))):
            button = QPushButton(name)
            button.setIcon(pixel_icon(icon_name, self.accent, 2))
            button.setObjectName("navActive" if index == 0 else "nav")
            button.setFixedHeight(34)
            button.clicked.connect(lambda _checked=False, i=index: self.switch_page(i))
            button.setMinimumWidth(0)
            nav.addWidget(button, 1)
            self.nav_buttons.append(button)
        outer.addLayout(nav)
        self.resize_grip = StyledSizeGrip(self)
        self.resize_grip.setToolTip("拖动调整窗口大小")
        self.resize_grip.raise_()

    def resizeEvent(self, event):
        if hasattr(self, "resize_grip"):
            self.resize_grip.move(self.width() - 16, self.height() - 16)
            self.resize_grip.raise_()
        super().resizeEvent(event)

    def _resize_edges(self, position):
        x, y = position.x(), position.y()
        margin = self._resize_margin
        edges = Qt.Edge(0)
        if x < margin:
            edges |= Qt.Edge.LeftEdge
        elif x >= self.width() - margin:
            edges |= Qt.Edge.RightEdge
        if y < margin:
            edges |= Qt.Edge.TopEdge
        elif y >= self.height() - margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self, event):
        self._update_resize_cursor(event.position().toPoint())
        super().mouseMoveEvent(event)

    def _update_resize_cursor(self, position):
        edges = self._resize_edges(position)
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if horizontal and vertical:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge) else Qt.CursorShape.SizeBDiagCursor)
        elif horizontal:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif vertical:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseMove and isinstance(watched, QWidget) and (watched is self or self.isAncestorOf(watched)):
            self._update_resize_cursor(self.mapFromGlobal(event.globalPosition().toPoint()))
        return super().eventFilter(watched, event)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.windowHandle():
            edges = self._resize_edges(event.position().toPoint())
            if edges:
                self.windowHandle().startSystemResize(edges)
                event.accept()
                return
        super().mousePressEvent(event)

    def _today_page(self):
        page, layout = scroll_page()
        self.pages.addWidget(page)
        day_row = QHBoxLayout()
        label = QLabel("今日概览")
        label.setObjectName("sectionTitle")
        day_row.addWidget(label)
        day_row.addStretch()
        layout.addLayout(day_row)

        check_card, check_layout = make_card("工作日 · 时间轴")
        check_row = QHBoxLayout()
        self.start_check = QPushButton("开工")
        self.start_check.clicked.connect(lambda: self.checkin("start_at"))
        check_row.addWidget(self.start_check)
        self.end_check = QPushButton("收工")
        self.end_check.clicked.connect(lambda: self.checkin("end_at"))
        check_row.addWidget(self.end_check)
        self.check_status = mini_label("尚未打卡")
        check_row.addWidget(self.check_status, 1)
        check_layout.addLayout(check_row)
        self.timeline = TimelineWidget()
        check_layout.addWidget(self.timeline)
        self.legend_host = QWidget()
        self.legend_layout = QHBoxLayout(self.legend_host)
        self.legend_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_layout.setSpacing(8)
        check_layout.addWidget(self.legend_host)
        layout.addWidget(check_card)

        timer_card, timer_layout = make_card("任务计时")
        self.task_time = QLabel("00:00:00")
        self.task_time.setObjectName("bigTimer")
        timer_layout.addWidget(self.task_time)
        self.current_task_label = mini_label("尚未开始")
        timer_layout.addWidget(self.current_task_label)
        task_row = QHBoxLayout()
        self.task_choice = NoWheelComboBox()
        task_row.addWidget(self.task_choice, 1)
        self.task_toggle = QPushButton("开始")
        self.task_toggle.setObjectName("primary")
        self.task_toggle.setIcon(pixel_icon("play", "#ffffff", 2))
        self.task_toggle.clicked.connect(self.start_or_stop_task)
        task_row.addWidget(self.task_toggle)
        timer_layout.addLayout(task_row)
        layout.addWidget(timer_card)

        pomo_card, pomo_layout = make_card("独立番茄钟")
        pomo_top = QHBoxLayout()
        self.pomo_phase = QLabel("准备专注")
        self.pomo_phase.setObjectName("sectionTitle")
        pomo_top.addWidget(self.pomo_phase)
        pomo_top.addStretch()
        self.pomo_time = QLabel("25:00")
        self.pomo_time.setObjectName("bigTimer")
        pomo_top.addWidget(self.pomo_time)
        pomo_layout.addLayout(pomo_top)
        self.pomo_hint = mini_label("未选择任务时，番茄钟时间记录为“其他”。休息也计入任务时长。")
        self.pomo_hint.setWordWrap(True)
        pomo_layout.addWidget(self.pomo_hint)
        self.pomo_toggle = QPushButton("开始番茄钟")
        self.pomo_toggle.clicked.connect(self.toggle_pomodoro)
        pomo_layout.addWidget(self.pomo_toggle)
        layout.addWidget(pomo_card)

        total_card, total_layout = make_card("当天任务统计")
        self.today_total = QLabel("合计 00:00:00")
        self.today_total.setObjectName("sectionTitle")
        total_layout.addWidget(self.today_total)
        self.today_stats = QVBoxLayout()
        self.today_stats.setSpacing(5)
        total_layout.addLayout(self.today_stats)
        layout.addWidget(total_card)
        layout.addStretch()

    def _goals_page(self):
        page, layout = scroll_page()
        self.pages.addWidget(page)
        header = QHBoxLayout()
        title = QLabel("目标与待办")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        add_goal = QPushButton("+ 目标")
        add_goal.clicked.connect(self.add_goal)
        header.addWidget(add_goal)
        add_todo = QPushButton("+ 子待办")
        add_todo.clicked.connect(self.add_todo)
        header.addWidget(add_todo)
        delete_goal = QPushButton("删除")
        delete_goal.clicked.connect(self.delete_goal)
        header.addWidget(delete_goal)
        layout.addLayout(header)
        layout.addWidget(mini_label("添加目标以及子待办"))
        edit_goal = QPushButton("编辑")
        edit_goal.clicked.connect(self.edit_goal_item)
        header.insertWidget(header.count() - 1, edit_goal)
        self.goal_tree = RichTreeWidget()
        self.goal_tree.setHeaderLabels(["今天的目标 / 子待办", "关联任务"])
        self.goal_tree.setRootIsDecorated(True)
        self.goal_tree.setMinimumHeight(420)
        configure_association_columns(self.goal_tree)
        self.goal_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.goal_tree.setWordWrap(True)
        self.goal_tree.setExpandsOnDoubleClick(False)
        self.goal_tree.setItemDelegate(RichTreeDelegate(self.goal_tree))
        self.goal_resize_timer = QTimer(self)
        self.goal_resize_timer.setSingleShot(True)
        self.goal_resize_timer.timeout.connect(self.refresh_goals)
        self.goal_tree.header().sectionResized.connect(lambda *_: self.goal_resize_timer.start(80))
        self.goal_tree.itemChanged.connect(self.goal_changed)
        self.goal_tree.itemClicked.connect(self.goal_clicked)
        self.goal_tree.itemDoubleClicked.connect(lambda *_: self.edit_goal_item())
        self.goal_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.goal_tree.customContextMenuRequested.connect(self.goal_context_menu)
        layout.addWidget(self.goal_tree)
        layout.addStretch()

    def _tasks_page(self):
        page, layout = scroll_page()
        self.pages.addWidget(page)
        top = QHBoxLayout()
        title = QLabel("任务与统计")
        title.setObjectName("sectionTitle")
        top.addWidget(title)
        top.addStretch()
        folder_button = QPushButton("+ 文件夹")
        folder_button.clicked.connect(self.add_folder)
        top.addWidget(folder_button)
        task_button = QPushButton("+ 任务")
        task_button.clicked.connect(self.add_task)
        top.addWidget(task_button)
        layout.addLayout(top)
        self.folder_tree = TaskFolderTree(self.move_task_by_drag)
        self.folder_tree.setHeaderLabels(["文件夹 / 任务", "累计"])
        self.folder_tree.setMinimumHeight(190)
        self.folder_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.folder_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.folder_tree.setColumnWidth(1, 85)
        self.folder_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.folder_tree.itemSelectionChanged.connect(self.refresh_task_detail)
        self.folder_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_tree.customContextMenuRequested.connect(self.task_context_menu)
        layout.addWidget(self.folder_tree)
        action_row = QHBoxLayout()
        actions = (("移动到文件夹", self.move_task), ("悬浮显示", self.toggle_pin), ("任务颜色", self.change_task_color), ("归档", self.archive_task), ("删除", self.delete_selected_task_or_folder))
        for index, (text, handler) in enumerate(actions):
            if index == 3:
                layout.addLayout(action_row)
                action_row = QHBoxLayout()
            button = QPushButton(text)
            button.clicked.connect(handler)
            action_row.addWidget(button)
        layout.addLayout(action_row)
        stats_card, stats_layout = make_card("汇总与多天对比")
        selector = QHBoxLayout()
        selector.setSpacing(4)
        self.stats_previous = QPushButton("‹")
        self.stats_previous.setFixedWidth(29)
        self.stats_previous.setToolTip("上一个统计时段")
        self.stats_previous.clicked.connect(lambda: self.shift_stats_range(-1))
        selector.addWidget(self.stats_previous)
        self.stats_range_button = QPushButton()
        self.stats_range_button.setMinimumWidth(150)
        self.stats_range_button.setStyleSheet("font-size: 11px;")
        self.stats_range_button.clicked.connect(self.pick_stats_range)
        selector.addWidget(self.stats_range_button, 1)
        self.stats_next = QPushButton("›")
        self.stats_next.setFixedWidth(29)
        self.stats_next.setToolTip("下一个统计时段")
        self.stats_next.clicked.connect(lambda: self.shift_stats_range(1))
        selector.addWidget(self.stats_next)
        self.period_choice = NoWheelComboBox()
        self.period_choice.addItems(["日/周", "周/月", "月/年"])
        self.period_choice.setFixedWidth(78)
        self.period_choice.currentIndexChanged.connect(self.stats_period_changed)
        selector.addWidget(self.period_choice)
        stats_layout.addLayout(selector)
        self.update_stats_range_label()
        self.stats_label = QLabel("选择任务或文件夹查看统计")
        self.stats_label.setObjectName("sectionTitle")
        self.stats_label.setWordWrap(True)
        stats_layout.addWidget(self.stats_label)
        self.compare_label = mini_label("")
        self.compare_label.setWordWrap(True)
        stats_layout.addWidget(self.compare_label)
        self.stats_chart = BarChartWidget()
        stats_layout.addWidget(self.stats_chart)
        layout.addWidget(stats_card)
        record_card, record_layout = make_card("单次时间记录")
        self.entries_table = QTableWidget(0, 3)
        self.entries_table.setHorizontalHeaderLabels(["开始", "结束", "时长"])
        self.entries_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.entries_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.entries_table.setMinimumHeight(170)
        record_layout.addWidget(self.entries_table)
        layout.addWidget(record_card)
        archive_button = QPushButton("查看归档任务")
        archive_button.clicked.connect(self.show_archived_tasks)
        layout.addWidget(archive_button)
        layout.addStretch()

    def _notes_page(self):
        page, layout = scroll_page()
        self.pages.addWidget(page)
        top = QHBoxLayout()
        title = QLabel("便签")
        title.setObjectName("sectionTitle")
        top.addWidget(title)
        top.addStretch()
        new_button = QPushButton("+ 便签")
        new_button.clicked.connect(self.quick_note)
        top.addWidget(new_button)
        layout.addLayout(top)
        layout.addWidget(mini_label("点击便签文字可再次编辑内容与条目"))
        self.notes_tree = RichTreeWidget()
        self.notes_tree.setHeaderLabels(["便签 / 候选条目", "归属"])
        self.notes_tree.setMinimumHeight(230)
        configure_association_columns(self.notes_tree)
        self.notes_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.notes_tree.setWordWrap(True)
        self.notes_tree.setExpandsOnDoubleClick(False)
        self.notes_tree.setItemDelegate(RichTreeDelegate(self.notes_tree, notes=True))
        self.notes_resize_timer = QTimer(self)
        self.notes_resize_timer.setSingleShot(True)
        self.notes_resize_timer.timeout.connect(self.refresh_notes)
        self.notes_tree.header().sectionResized.connect(lambda *_: self.notes_resize_timer.start(80))
        self.notes_tree.itemChanged.connect(self.note_changed)
        self.notes_tree.itemClicked.connect(self.note_clicked)
        self.notes_tree.itemDoubleClicked.connect(self.edit_note)
        self.notes_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.notes_tree.customContextMenuRequested.connect(self.note_context_menu)
        layout.addWidget(self.notes_tree, 1)
        row = QHBoxLayout()
        for text, handler in (("编辑", self.edit_note), ("归入目标", self.assign_note), ("归档", self.archive_note), ("删除", self.delete_note)):
            button = QPushButton(text)
            button.clicked.connect(handler)
            row.addWidget(button)
        layout.addLayout(row)
        archive_button = QPushButton("查看归档便签")
        archive_button.clicked.connect(self.show_archived_notes)
        layout.addWidget(archive_button)
        layout.addStretch()

    def _settings_page(self):
        page, layout = scroll_page()
        self.pages.addWidget(page)
        title = QLabel("设置")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        appearance, appearance_layout = make_card("外观")
        form = QFormLayout()
        self.theme_choice = NoWheelComboBox()
        self.theme_choice.addItems(["黑暗模式", "白天模式"])
        self.theme_choice.setCurrentIndex(0 if self.dark else 1)
        self.theme_choice.currentIndexChanged.connect(self.change_theme)
        form.addRow("主题", self.theme_choice)
        color_button = QPushButton("选择主题色")
        color_button.clicked.connect(self.choose_accent)
        form.addRow("强调色", color_button)
        self.accent_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.accent_slider.setRange(70, 255)
        self.accent_slider.setValue(self.accent_alpha)
        self.accent_slider.valueChanged.connect(self.change_accent_alpha)
        form.addRow("主题色透明度", self.accent_slider)
        appearance_layout.addLayout(form)
        layout.addWidget(appearance)

        desktop, desktop_layout = make_card("桌面入口")
        self.ball_checkbox = QCheckBox("启用桌面悬浮窗")
        self.ball_checkbox.setChecked(self.ball_enabled)
        self.ball_checkbox.toggled.connect(self.toggle_ball)
        desktop_layout.addWidget(self.ball_checkbox)
        self.startup_checkbox = QCheckBox("开机时自动启动时刻")
        self.startup_checkbox.setChecked(startup_enabled())
        self.startup_checkbox.toggled.connect(self.toggle_startup)
        desktop_layout.addWidget(self.startup_checkbox)
        self.opacity_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(45, 100)
        self.opacity_slider.setValue(self.float_opacity)
        self.opacity_slider.valueChanged.connect(self.change_float_opacity)
        desktop_layout.addWidget(mini_label("悬浮窗透明度"))
        desktop_layout.addWidget(self.opacity_slider)
        desktop_hint = mini_label("关闭主窗口后，程序保留在系统托盘。托盘菜单也能恢复悬浮窗。")
        desktop_hint.setWordWrap(True)
        desktop_layout.addWidget(desktop_hint)
        layout.addWidget(desktop)

        shortcut, shortcut_layout = make_card("快速便签快捷键")
        self.hotkey_edit = QKeySequenceEdit(QKeySequence(self._hotkey_string))
        shortcut_layout.addWidget(self.hotkey_edit)
        apply_key = QPushButton("应用全局快捷键")
        apply_key.clicked.connect(self.apply_hotkey)
        shortcut_layout.addWidget(apply_key)
        self.hotkey_status = mini_label("默认 Ctrl+Alt+N；若被其他程序占用，会保留原快捷键。")
        self.hotkey_status.setWordWrap(True)
        shortcut_layout.addWidget(self.hotkey_status)
        layout.addWidget(shortcut)

        pomo_card, pomo_layout = make_card("番茄钟间隔")
        row = QGridLayout()
        self.focus_spin = NoWheelSpinBox()
        self.focus_spin.setRange(1, 180)
        self.focus_spin.setValue(int(self.store.preference("focus_minutes", "25")))
        self.rest_spin = NoWheelSpinBox()
        self.rest_spin.setRange(1, 60)
        self.rest_spin.setValue(int(self.store.preference("break_minutes", "5")))
        self.long_spin = NoWheelSpinBox()
        self.long_spin.setRange(1, 90)
        self.long_spin.setValue(int(self.store.preference("long_minutes", "15")))
        self.round_spin = NoWheelSpinBox()
        self.round_spin.setRange(1, 20)
        self.round_spin.setValue(int(self.store.preference("long_every", "4")))
        for index, (label, widget, key) in enumerate((("专注", self.focus_spin, "focus_minutes"), ("短休", self.rest_spin, "break_minutes"), ("长休", self.long_spin, "long_minutes"), ("长休间隔（轮）", self.round_spin, "long_every"))):
            row.addWidget(mini_label(label), index, 0)
            controls = QWidget()
            controls.setObjectName("spinControls")
            controls_layout = QHBoxLayout(controls)
            controls_layout.setContentsMargins(0, 0, 0, 0)
            controls_layout.setSpacing(3)
            controls_layout.addStretch(1)
            widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            widget.setFixedWidth(92)
            controls_layout.addWidget(widget)
            step_buttons = QWidget()
            step_buttons.setObjectName("spinButtons")
            step_layout = QVBoxLayout(step_buttons)
            step_layout.setContentsMargins(0, 0, 0, 0)
            step_layout.setSpacing(2)
            up = QPushButton("▲")
            down = QPushButton("▼")
            for button in (up, down):
                button.setObjectName("spinStep")
                button.setFixedSize(25, 17)
                step_layout.addWidget(button)
            up.setToolTip(f"增加{label}时长")
            down.setToolTip(f"减少{label}时长")
            up.clicked.connect(widget.stepUp)
            down.clicked.connect(widget.stepDown)
            controls_layout.addWidget(step_buttons)
            row.addWidget(controls, index, 1)
            if index == 0:
                self.focus_up_button, self.focus_down_button = up, down
            elif index == 1:
                self.rest_up_button, self.rest_down_button = up, down
            else:
                self.long_up_button, self.long_down_button = up, down
        row.setColumnStretch(1, 1)
        pomo_layout.addLayout(row)
        self.sound_check = QCheckBox("阶段结束时播放提示音")
        self.sound_check.setChecked(self.store.preference("pomo_sound", "1") == "1")
        pomo_layout.addWidget(self.sound_check)
        save_pomo = QPushButton("保存番茄钟设置")
        save_pomo.setObjectName("primary")
        save_pomo.clicked.connect(self.save_pomodoro_settings)
        pomo_layout.addWidget(save_pomo)
        pomo_layout.addWidget(mini_label("保存后从下一次启动番茄钟起生效"))
        layout.addWidget(pomo_card)

        data_card, data_layout = make_card("本地数据")
        self.data_path_label = mini_label(str(self.store.path).replace("\\", "\\\u200b"))
        self.data_path_label.setWordWrap(True)
        data_layout.addWidget(self.data_path_label)
        change_path = QPushButton("更改数据存储位置")
        change_path.clicked.connect(self.change_data_directory)
        data_layout.addWidget(change_path)
        path_hint = mini_label("更改时会复制当前数据；原目录保留，程序随后重启。")
        path_hint.setWordWrap(True)
        data_layout.addWidget(path_hint)
        backup = QPushButton("备份完整数据")
        backup.clicked.connect(self.backup_data)
        data_layout.addWidget(backup)
        export = QPushButton("导出时间记录 CSV")
        export.clicked.connect(self.export_data)
        data_layout.addWidget(export)
        layout.addWidget(data_card)
        layout.addStretch()

    def _create_tray(self):
        self.app.setQuitOnLastWindowClosed(False)
        self.tray = QSystemTrayIcon(pixel_icon("clock", self.accent), self)
        menu = QMenu()
        self.tray_open = menu.addAction("打开时刻")
        self.tray_open.triggered.connect(self.show_main)
        self.tray_ball = menu.addAction("关闭悬浮窗" if self.ball_enabled else "开启悬浮窗")
        self.tray_ball.triggered.connect(lambda: self.toggle_ball(not self.ball_enabled))
        menu.addAction("快速便签", self.quick_note)
        menu.addSeparator()
        menu.addAction("退出程序", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_main() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _apply_theme(self):
        color = QColor(self.accent)
        self.app.setStyleSheet(theme_sheet(self.dark, color, self.accent_alpha))
        self.setWindowIcon(pixel_icon("clock", self.accent))
        self.logo.setPixmap(pixel_icon("clock", self.accent, 4).pixmap(32, 32))
        self.tray.setIcon(pixel_icon("clock", self.accent))
        self.pin_button.setIcon(pixel_icon("pin", self.accent if self.always_on_top else "#8b91a3", 2))
        self.ball.update()
        self.timeline.update()
        for index, (button, icon_name) in enumerate(zip(self.nav_buttons, ("clock", "check", "folder", "note", "gear"))):
            button.setIcon(pixel_icon(icon_name, self.accent, 2))

    def switch_page(self, index):
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setObjectName("navActive" if i == index else "nav")
            button.style().unpolish(button)
            button.style().polish(button)
        if index == 2:
            self.refresh_task_detail()

    def toggle_always_on_top(self):
        self.always_on_top = not self.always_on_top
        self.store.set_preference("always_on_top", int(self.always_on_top))
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.always_on_top)
        self.pin_button.setIcon(pixel_icon("pin", self.accent if self.always_on_top else "#8b91a3", 2))
        self.pin_button.setToolTip("取消固定在最前" if self.always_on_top else "固定在最前")
        self.show()

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def change_theme(self, index):
        self.dark = index == 0
        self.store.set_preference("theme", "dark" if self.dark else "light")
        self._apply_theme()
        self.refresh_today()

    def choose_accent(self):
        color = QColorDialog.getColor(QColor(self.accent), self, "选择主题色")
        if color.isValid():
            self.accent = color.name()
            self.store.set_preference("accent", self.accent)
            self._apply_theme()
            self.refresh_all()

    def change_accent_alpha(self, value):
        self.accent_alpha = value
        self.store.set_preference("accent_alpha", value)
        self._apply_theme()

    def change_float_opacity(self, value):
        self.float_opacity = value
        self.store.set_preference("float_opacity", value)
        self.ball.update()

    def toggle_ball(self, enabled):
        self.ball_enabled = bool(enabled)
        self.store.set_preference("ball_enabled", int(self.ball_enabled))
        self.ball_checkbox.blockSignals(True)
        self.ball_checkbox.setChecked(self.ball_enabled)
        self.ball_checkbox.blockSignals(False)
        self.tray_ball.setText("关闭悬浮窗" if self.ball_enabled else "开启悬浮窗")
        if self.ball_enabled:
            self.ball.show()
        else:
            self.ball.hide()

    def save_ball_position(self):
        self.store.set_preference("ball_x", self.ball.x())
        self.store.set_preference("ball_y", self.ball.y())

    def dock_ball(self):
        if not self.ball.isVisible() or self.ball_docked:
            return
        screen = self.ball.screen().availableGeometry()
        x, y = self.ball.x(), self.ball.y()
        if x <= screen.left() + 18:
            self.ball_docked = True
            self._ball_full_x = screen.left()
            self.ball.move(screen.left() - 30, y)
        elif x >= screen.right() - 82:
            self.ball_docked = True
            self._ball_full_x = screen.right() - 62
            self.ball.move(screen.right() - 31, y)

    def undock_ball(self):
        if self.ball_docked:
            self.ball.move(self._ball_full_x, self.ball.y())
            self.ball_docked = False

    def show_main(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.refresh_all()

    def closeEvent(self, event):
        if self._quitting:
            event.accept()
            return
        if self.tray.isSystemTrayAvailable():
            self.hide()
            event.ignore()
        else:
            event.accept()

    def quit_app(self):
        self._quitting = True
        if self._hotkey_registered and sys.platform == "win32":
            ctypes.windll.user32.UnregisterHotKey(int(self.winId()), 0x5444)
        if self.store.active():
            self.store.heartbeat()
        self.ball.hide()
        self.tray.hide()
        self.store.close()
        self.app.quit()

    def _decode_hotkey(self, shortcut):
        parts = [part.strip().upper() for part in shortcut.split("+")]
        mods = 0
        for part in parts[:-1]:
            mods |= {"ALT": 0x0001, "CTRL": 0x0002, "SHIFT": 0x0004, "META": 0x0008, "WIN": 0x0008}.get(part, 0)
        key = parts[-1] if parts else ""
        if not mods or len(key) == 0:
            return None
        if len(key) == 1 and ("A" <= key <= "Z" or "0" <= key <= "9"):
            vk = ord(key)
        elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
            vk = 0x70 + int(key[1:]) - 1
        else:
            return None
        return mods | 0x4000, vk

    def _register_hotkey(self, shortcut, initial=False):
        if sys.platform != "win32":
            return False
        decoded = self._decode_hotkey(shortcut)
        if not decoded:
            if not initial:
                self.hotkey_status.setText("请使用 Ctrl/Alt/Shift/Win + 字母、数字或 F1–F12。")
            return False
        old = self._hotkey_string
        if self._hotkey_registered:
            ctypes.windll.user32.UnregisterHotKey(int(self.winId()), 0x5444)
            self._hotkey_registered = False
        success = bool(ctypes.windll.user32.RegisterHotKey(int(self.winId()), 0x5444, *decoded))
        if success:
            self._hotkey_registered = True
            self._hotkey_string = shortcut
            self.store.set_preference("note_hotkey", shortcut)
            self.hotkey_status.setText(f"全局便签快捷键：{shortcut}")
        else:
            previous = self._decode_hotkey(old)
            if previous:
                self._hotkey_registered = bool(ctypes.windll.user32.RegisterHotKey(int(self.winId()), 0x5444, *previous))
            self.hotkey_status.setText("快捷键被占用或无法注册；已保留原设置。")
        return success

    def apply_hotkey(self):
        shortcut = self.hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        if not self._register_hotkey(shortcut):
            self.hotkey_edit.setKeySequence(QKeySequence(self._hotkey_string))

    def nativeEvent(self, event_type, message):
        if sys.platform == "win32":
            import ctypes.wintypes
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == 0x0312 and msg.wParam == 0x5444:
                    self.quick_note(True)
                    return True, 0
            except Exception:
                pass
        return super().nativeEvent(event_type, message)

    def quick_note(self, topmost=False):
        dialog = QuickNoteDialog(self)
        if topmost:
            dialog.pin_button.blockSignals(True)
            dialog.pin_button.setChecked(True)
            dialog.pin_button.blockSignals(False)
            dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            QTimer.singleShot(0, dialog.activateWindow)
        dialog.exec()

    def _recover_timer(self):
        active = self.store.active()
        if not active:
            return
        box = QMessageBox(self)
        box.setWindowTitle("恢复计时")
        box.setText(f"上次退出时正在计时：{active['task_name']}。")
        box.setInformativeText(f"最后运行于 {parse(active['heartbeat_at']):%Y-%m-%d %H:%M}。请选择如何处理这段时间。")
        end = box.addButton("在上次运行时间结束", QMessageBox.ButtonRole.AcceptRole)
        cont = box.addButton("继续计时", QMessageBox.ButtonRole.ActionRole)
        discard = box.addButton("放弃未结束部分", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        if box.clickedButton() is end:
            self.store.stop(active["heartbeat_at"])
            self.store.ensure_pomo_other()
        elif box.clickedButton() is discard:
            self.store.execute("DELETE FROM active_timer WHERE singleton=1")
            self.store.ensure_pomo_other()

    def _notify(self, title, message):
        if self.tray.isSystemTrayAvailable():
            self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)
        else:
            QMessageBox.information(self, title, message)

    def tick(self):
        current_day = date.today()
        if current_day != self.last_calendar_day:
            self.view_day = current_day
            self.last_calendar_day = current_day
            self.refresh_all()
        state = self.store.pomo()
        if state:
            limit = {"focus": state["focus_minutes"], "break": state["break_minutes"], "long_break": state["long_minutes"]}[state["phase"]] * 60
            if seconds_between(state["phase_start"], now()) >= limit:
                message = self.store.advance_pomodoro()
                if self.store.preference("pomo_sound", "1") == "1":
                    QApplication.beep()
                self._notify("番茄钟", message)
        if datetime.now().second < 2 and self.store.active():
            self.store.heartbeat()
        self.refresh_clock()
        if self.view_day == current_day and datetime.now().second % 5 == 0:
            self.refresh_today()

    def refresh_clock(self):
        active = self.store.active()
        if active:
            self.task_time.setText(fmt(seconds_between(active["start_at"], now())))
            self.current_task_label.setText(f"正在计时 · {active['task_name']}")
            selected_id = self.task_choice.currentData()
            self.task_toggle.setText("停止" if selected_id == active["task_id"] else "切换")
            self.task_toggle.setIcon(pixel_icon("stop" if selected_id == active["task_id"] else "play", "#ffffff", 2))
        else:
            self.task_time.setText("00:00:00")
            self.current_task_label.setText("尚未开始")
            self.task_toggle.setText("开始")
            self.task_toggle.setIcon(pixel_icon("play", "#ffffff", 2))
        state = self.store.pomo()
        if state:
            label = {"focus": "专注中", "break": "短休息", "long_break": "长休息"}[state["phase"]]
            total = {"focus": state["focus_minutes"], "break": state["break_minutes"], "long_break": state["long_minutes"]}[state["phase"]] * 60
            remaining = max(0, total - seconds_between(state["phase_start"], now()))
            self.pomo_phase.setText(f"{label} · 第 {state['rounds'] + (state['phase'] == 'focus')} 轮")
            self.pomo_time.setText(f"{remaining // 60:02d}:{remaining % 60:02d}")
            self.pomo_toggle.setText("结束番茄钟")
        else:
            self.pomo_phase.setText("准备专注")
            self.pomo_time.setText(f"{int(self.store.preference('focus_minutes', '25')):02d}:00")
            self.pomo_toggle.setText("开始番茄钟")
        self.ball.update()

    def refresh_all(self):
        self.refresh_task_choice()
        self.refresh_today()
        self.refresh_goals()
        self.refresh_folder_tree()
        self.refresh_notes()
        self.refresh_clock()

    def refresh_task_choice(self):
        selected = self.task_choice.currentData()
        self.task_choice.blockSignals(True)
        self.task_choice.clear()
        for task in self.store.tasks():
            if not task["system"]:
                self.task_choice.addItem(task["name"], task["id"])
        if selected is not None:
            index = self.task_choice.findData(selected)
            if index >= 0:
                self.task_choice.setCurrentIndex(index)
        self.task_choice.blockSignals(False)

    def change_day(self, qdate):
        self.view_day = qdate.toPython()
        self.refresh_today()

    def checkin(self, kind):
        if self.view_day != date.today():
            return
        self.store.checkin_once(self.view_day.isoformat(), kind)
        self.refresh_today()

    def refresh_today(self):
        a, b = bounds(self.view_day)
        shifts = self.store.checkins_on(self.view_day.isoformat())
        row = self.store.one("SELECT * FROM checkins WHERE day=?", (self.view_day.isoformat(),))
        entries = [dict(x) for x in self.store.entries_between(a, b)]
        active = self.store.active()
        if active and self.view_day == date.today():
            task = self.store.one("SELECT color FROM tasks WHERE id=?", (active["task_id"],))
            entries.append({"task_name": active["task_name"], "color": task["color"] if task else self.accent, "start_at": active["start_at"], "end_at": now()})
        self.timeline.set_data(self.view_day.isoformat(), entries, shifts, self.dark, self.accent)
        is_today = self.view_day == date.today()
        open_shift = self.store.one("SELECT * FROM checkins WHERE start_at IS NOT NULL AND end_at IS NULL ORDER BY start_at DESC LIMIT 1")
        self.start_check.setEnabled(is_today and not open_shift and not (row and row["start_at"]))
        self.end_check.setEnabled(is_today and bool(open_shift or (row and row["start_at"] and not row["end_at"])))
        day_start, day_end = parse(a), parse(b)
        segments = []
        for shift in shifts:
            if shift["start_at"]:
                segment_start = max(parse(shift["start_at"]), day_start)
                segment_end = min(parse(shift["end_at"]) if shift["end_at"] else parse(now()), day_end)
                if segment_end >= segment_start:
                    end_text = "24:00" if segment_end == day_end else segment_end.strftime("%H:%M")
                    segments.append(f"{segment_start:%H:%M} → {end_text}")
        self.check_status.setText("  /  ".join(segments) if segments else "尚未打卡")
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        seen = {}
        for entry in entries:
            seen[entry["task_name"]] = entry["color"]
        for name, color in list(seen.items())[:7]:
            label = QLabel(f"■ {name}")
            label.setStyleSheet(f"color: {color}; background: transparent; font-size: 12px;")
            self.legend_layout.addWidget(label)
        self.legend_layout.addStretch()
        while self.today_stats.count():
            item = self.today_stats.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        totals = self.store.totals(a, b)
        self.today_total.setText(f"合计 {fmt(sum(totals.values()))}")
        for (_task_id, name), seconds in sorted(totals.items(), key=lambda pair: -pair[1]):
            line = QLabel(f"{name}     {fmt(seconds)}")
            self.today_stats.addWidget(line)
        if not totals:
            self.today_stats.addWidget(mini_label("这一天还没有任务计时"))

    def start_or_stop_task(self):
        task_id = self.task_choice.currentData()
        if task_id is None:
            QMessageBox.information(self, "任务", "请先在任务页创建任务。")
            return
        active = self.store.active()
        if active and active["task_id"] == task_id:
            self.store.stop()
            self.store.ensure_pomo_other()
        else:
            self.store.start(task_id)
        self.refresh_all()

    def toggle_task(self, task_id):
        active = self.store.active()
        if active and active["task_id"] == task_id:
            self.store.stop()
            self.store.ensure_pomo_other()
        else:
            self.store.start(task_id)
        self.ball.popup.hide()
        self.refresh_all()

    def toggle_pomodoro(self):
        if self.store.pomo():
            self.store.stop_pomodoro()
        else:
            self.store.start_pomodoro(int(self.store.preference("focus_minutes", "25")),
                                      int(self.store.preference("break_minutes", "5")),
                                      int(self.store.preference("long_minutes", "15")),
                                      int(self.store.preference("long_every", "4")))
        self.refresh_all()

    def save_pomodoro_settings(self):
        for key, widget in (("focus_minutes", self.focus_spin), ("break_minutes", self.rest_spin),
                            ("long_minutes", self.long_spin), ("long_every", self.round_spin)):
            self.store.set_preference(key, widget.value())
        self.store.set_preference("pomo_sound", int(self.sound_check.isChecked()))
        self.refresh_clock()
        QMessageBox.information(self, "番茄钟", "设置已保存，从下一次启动番茄钟起生效。")

    def toggle_startup(self, enabled):
        if not getattr(sys, "frozen", False):
            QMessageBox.information(self, "开机自启动", "请使用打包后的 Shike.exe 设置开机自启动。")
            self.startup_checkbox.blockSignals(True)
            self.startup_checkbox.setChecked(False)
            self.startup_checkbox.blockSignals(False)
            return
        try:
            set_startup_enabled(enabled)
        except OSError as error:
            QMessageBox.warning(self, "开机自启动", str(error))
            self.startup_checkbox.blockSignals(True)
            self.startup_checkbox.setChecked(startup_enabled())
            self.startup_checkbox.blockSignals(False)

    def add_goal(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("新建每日目标")
        form = QFormLayout(dialog)
        title = QLineEdit()
        title.setPlaceholderText("今天想完成什么？")
        form.addRow("目标", title)
        task = NoWheelComboBox()
        task.addItem("未关联任务", None)
        for row in self.store.tasks():
            if not row["system"] and row["name"] != "番茄钟":
                task.addItem(row["name"], row["id"])
        form.addRow("归入任务", task)
        save = QPushButton("创建目标")
        save.setObjectName("primary")
        save.clicked.connect(dialog.accept)
        form.addRow(save)
        if dialog.exec() and title.text().strip():
            self.store.add_goal(date.today().isoformat(), title.text().strip(), task.currentData())
            self.refresh_goals()
            self.refresh_notes()

    def add_todo(self):
        selected = self.goal_tree.currentItem()
        if selected and selected.data(0, Qt.ItemDataRole.UserRole)[0] == "todo":
            selected = selected.parent()
        if not selected or selected.data(0, Qt.ItemDataRole.UserRole)[0] != "goal":
            QMessageBox.information(self, "子待办", "请先选择一个目标。")
            return
        title, ok = QInputDialog.getText(self, "新增子待办", "事项内容")
        if ok and title.strip():
            self.store.add_todo(title.strip(), selected.data(0, Qt.ItemDataRole.UserRole)[1])
            self.refresh_goals()

    def edit_goal_item(self, item=None, _column=0):
        item = item or self.goal_tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data[0] == "todo":
            row = self.store.one("SELECT title FROM todos WHERE id=?", (data[1],))
            if not row:
                return
            text, ok = QInputDialog.getText(self, "编辑子待办", "事项内容", text=row["title"])
            if ok and text.strip():
                self.store.execute("UPDATE todos SET title=? WHERE id=?", (text.strip(), data[1]))
                self.refresh_goals()
            return
        row = self.store.one("SELECT title,task_id FROM goals WHERE id=?", (data[1],))
        if not row:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑目标")
        form = QFormLayout(dialog)
        title = QTextEdit()
        title.setPlainText(row["title"])
        title.setMinimumHeight(85)
        form.addRow("目标", title)
        task = NoWheelComboBox()
        task.addItem("未关联任务", None)
        for candidate in self.store.tasks():
            if not candidate["system"] and candidate["name"] != "番茄钟":
                task.addItem(candidate["name"], candidate["id"])
        index = task.findData(row["task_id"])
        task.setCurrentIndex(max(0, index))
        form.addRow("关联任务", task)
        save = QPushButton("保存")
        save.setObjectName("primary")
        save.clicked.connect(dialog.accept)
        form.addRow(save)
        if dialog.exec() and title.toPlainText().strip():
            self.store.execute("UPDATE goals SET title=?,task_id=? WHERE id=?", (title.toPlainText().strip(), task.currentData(), data[1]))
            self.refresh_goals()
            self.refresh_notes()

    def goal_context_menu(self, point):
        item = self.goal_tree.itemAt(point)
        if not item:
            return
        if item.data(0, Qt.ItemDataRole.UserRole)[0] == "todo":
            item = item.parent()
        self.goal_tree.setCurrentItem(item)
        menu = QMenu(self)
        change = menu.addAction("修改关联任务")
        delete = menu.addAction("删除目标及子待办")
        chosen = menu.exec(self.goal_tree.viewport().mapToGlobal(point))
        if chosen == delete:
            self.delete_goal()
        elif chosen == change:
            goal_id = item.data(0, Qt.ItemDataRole.UserRole)[1]
            tasks = [task for task in self.store.tasks() if not task["system"] and task["name"] != "番茄钟"]
            labels = ["未关联任务"] + [task["name"] for task in tasks]
            current = self.store.one("SELECT task_id FROM goals WHERE id=?", (goal_id,))["task_id"]
            current_index = next((i + 1 for i, task in enumerate(tasks) if task["id"] == current), 0)
            label, ok = QInputDialog.getItem(self, "修改关联任务", "关联任务", labels, current_index, False)
            if ok:
                index = labels.index(label)
                self.store.execute("UPDATE goals SET task_id=? WHERE id=?", (tasks[index - 1]["id"] if index else None, goal_id))
                self.refresh_goals()

    def delete_goal(self):
        item = self.goal_tree.currentItem()
        if not item:
            return
        if item.data(0, Qt.ItemDataRole.UserRole)[0] == "todo":
            item = item.parent()
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] != "goal":
            return
        goal_id = data[1]
        count = len(self.store.todos(goal_id=goal_id))
        text = f"删除目标“{item.text(0)}”及其 {count} 条子待办？关联便签会回到收集箱，计时记录保留。"
        if QMessageBox.question(self, "确认删除目标", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.store.delete_goal(goal_id)
            self.refresh_goals()
            self.refresh_notes()

    def refresh_goals(self):
        self.goal_loading = True
        self.goal_tree.blockSignals(True)
        self.goal_tree.clear()
        metrics = self.goal_tree.fontMetrics()
        width = max(100, self.goal_tree.header().sectionSize(0) - 65)
        for goal in self.store.goals(date.today().isoformat()):
            full_association = goal["task_name"] or "未分类"
            item = QTreeWidgetItem(["", association_label(full_association)])
            item.setToolTip(1, full_association)
            item.setData(0, Qt.ItemDataRole.UserRole, ("goal", goal["id"]))
            self._set_list_text(item, goal["title"], metrics, width, 3, ("goal", goal["id"]) in self.goal_expanded)
            item.setCheckState(0, Qt.CheckState.Checked if goal["done"] else Qt.CheckState.Unchecked)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.goal_tree.addTopLevelItem(item)
            for todo in self.store.todos(goal_id=goal["id"]):
                child = QTreeWidgetItem(["", ""])
                child.setData(0, Qt.ItemDataRole.UserRole, ("todo", todo["id"], goal["id"]))
                self._set_list_text(child, todo["title"], metrics, width - 24, 1, ("todo", todo["id"]) in self.goal_expanded)
                child.setCheckState(0, Qt.CheckState.Checked if todo["done"] else Qt.CheckState.Unchecked)
                child.setFlags(child.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                item.addChild(child)
            item.setExpanded(True)
        self.goal_tree.blockSignals(False)
        self.goal_loading = False

    def _set_list_text(self, item, full, metrics, width, lines, expanded):
        item.setData(0, Qt.ItemDataRole.UserRole + 1, full)
        item.setData(0, Qt.ItemDataRole.UserRole + 2, lines)
        item.setToolTip(0, full)
        display = full if expanded else preview_lines(full, metrics, width, lines)
        item.setText(0, display)
        height = metrics.boundingRect(QRect(0, 0, max(40, width), 20000), Qt.TextFlag.TextWordWrap, display).height() + 11
        item.setSizeHint(0, QSize(0, max(28, height)))

    def _toggle_list_text(self, item, tree, expanded_keys):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        key = (data[0], data[1])
        if key in expanded_keys:
            expanded_keys.remove(key)
        else:
            expanded_keys.add(key)
        width = max(100, tree.header().sectionSize(0) - (89 if item.parent() else 65))
        self._set_list_text(item, item.data(0, Qt.ItemDataRole.UserRole + 1), tree.fontMetrics(), width,
                            item.data(0, Qt.ItemDataRole.UserRole + 2), key in expanded_keys)

    def goal_clicked(self, item, column):
        if column == 0:
            self._toggle_list_text(item, self.goal_tree, self.goal_expanded)

    def goal_changed(self, item, column):
        if self.goal_loading or column != 0:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        done = item.checkState(0) == Qt.CheckState.Checked
        if data[0] == "goal":
            self.store.set_done("goals", data[1], done)
        else:
            goal_id = data[2]
            before = self.store.todos(goal_id=goal_id)
            was_complete = bool(before) and all(x["done"] for x in before)
            self.store.set_done("todos", data[1], done)
            self._check_goal_completion(goal_id, was_complete)
        self.refresh_notes()

    def _check_goal_completion(self, goal_id, was_complete):
        if not goal_id or was_complete:
            return
        after = self.store.todos(goal_id=goal_id)
        goal = self.store.one("SELECT done FROM goals WHERE id=?", (goal_id,))
        if after and all(x["done"] for x in after) and goal and not goal["done"]:
            self.confetti = ConfettiDialog(self, self.accent)
            self.confetti.move(self.geometry().center() - self.confetti.rect().center())
            self.confetti.show()
            self._notify("目标待办完成", "子待办全部完成，请手动勾选目标。")

    def add_folder(self):
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称")
        if ok and name.strip():
            try:
                self.store.add_folder(name.strip())
                self.refresh_folder_tree()
            except sqlite3.IntegrityError:
                QMessageBox.information(self, "文件夹", "这个名称已经存在。")

    def add_task(self):
        name, ok = QInputDialog.getText(self, "新建任务", "任务名称")
        if not ok or not name.strip():
            return
        task_id = self.store.add_task(name.strip())
        selected = self.folder_tree.currentItem()
        if selected:
            data = selected.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == "folder":
                self.store.assign_folder(task_id, data[1])
        self.refresh_all()

    def _selected_task(self):
        item = self.folder_tree.currentItem()
        if not item:
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return data[1] if data and data[0] == "task" else None

    def task_context_menu(self, point):
        item = self.folder_tree.itemAt(point)
        if not item:
            return
        self.folder_tree.setCurrentItem(item)
        data = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        if data[0] == "task":
            task = self.store.one("SELECT system FROM tasks WHERE id=?", (data[1],))
            archive = None if task and task["system"] else menu.addAction("归档任务")
            delete = menu.addAction("清空其他计时记录" if task and task["system"] else "删除任务及历史记录")
        elif data[1] is not None:
            archive = None
            delete = menu.addAction("删除文件夹")
        else:
            return
        chosen = menu.exec(self.folder_tree.viewport().mapToGlobal(point))
        if chosen == archive:
            self.archive_task()
        elif chosen == delete:
            self.delete_selected_task_or_folder()

    def delete_selected_task_or_folder(self):
        item = self.folder_tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data[0] == "task":
            task = self.store.one("SELECT * FROM tasks WHERE id=?", (data[1],))
            if not task:
                return
            count = self.store.one("SELECT COUNT(*) n FROM entries WHERE task_id=?", (data[1],))["n"]
            text = (f"清空“其他”的 {count} 条历史计时记录？任务本身保留。此操作不可撤销。" if task["system"]
                    else f"删除任务“{task['name']}”及其 {count} 条历史计时记录？此操作不可撤销。")
            if QMessageBox.question(self, "确认删除任务", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self.store.delete_task(data[1])
            self.store.ensure_pomo_other()
        elif data[1] is not None:
            folder = self.store.one("SELECT name FROM folders WHERE id=?", (data[1],))
            if not folder:
                return
            text = f"删除文件夹“{folder['name']}”？其中的任务会移到“未分类”。"
            if QMessageBox.question(self, "确认删除文件夹", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self.store.delete_folder(data[1])
        self.refresh_all()

    def move_task(self):
        task_id = self._selected_task()
        if not task_id:
            QMessageBox.information(self, "移动任务", "请先选择一个任务。")
            return
        folders = self.store.folders()
        labels = ["未分类"] + [row["name"] for row in folders]
        label, ok = QInputDialog.getItem(self, "移动任务", "目标文件夹", labels, 0, False)
        if ok:
            folder_id = next((row["id"] for row in folders if row["name"] == label), None)
            self.store.assign_folder(task_id, folder_id)
            self.refresh_folder_tree()

    def move_task_by_drag(self, task_id, folder_id):
        task = self.store.one("SELECT system,folder_id FROM tasks WHERE id=?", (task_id,))
        if not task or task["system"] or task["folder_id"] == folder_id:
            return
        self.store.assign_folder(task_id, folder_id)
        QTimer.singleShot(0, self.refresh_folder_tree)

    def toggle_pin(self):
        task_id = self._selected_task()
        if task_id:
            row = self.store.one("SELECT pinned FROM tasks WHERE id=?", (task_id,))
            self.store.execute("UPDATE tasks SET pinned=? WHERE id=?", (0 if row["pinned"] else 1, task_id))
            self.refresh_folder_tree()

    def change_task_color(self):
        task_id = self._selected_task()
        if not task_id:
            return
        row = self.store.one("SELECT color FROM tasks WHERE id=?", (task_id,))
        color = QColorDialog.getColor(QColor(row["color"]), self, "任务颜色")
        if color.isValid():
            self.store.execute("UPDATE tasks SET color=? WHERE id=?", (color.name(), task_id))
            self.refresh_all()

    def archive_task(self):
        task_id = self._selected_task()
        if not task_id:
            return
        active = self.store.active()
        if active and active["task_id"] == task_id:
            QMessageBox.information(self, "归档任务", "请先结束或切换正在计时的任务。")
            return
        row = self.store.one("SELECT archived,system FROM tasks WHERE id=?", (task_id,))
        if not row or row["system"]:
            return
        self.store.execute("UPDATE tasks SET archived=1 WHERE id=?", (task_id,))
        self.refresh_all()

    def show_archived_tasks(self):
        self._show_archive("任务归档", [(t["id"], t["name"]) for t in self.store.tasks(True) if t["archived"]], "tasks")

    def refresh_folder_tree(self):
        current = self.folder_tree.currentItem()
        selected = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        self.folder_tree.blockSignals(True)
        self.folder_tree.clear()
        roots = {None: QTreeWidgetItem(["未分类", ""])}
        roots[None].setData(0, Qt.ItemDataRole.UserRole, ("folder", None))
        self.folder_tree.addTopLevelItem(roots[None])
        for folder in self.store.folders():
            item = QTreeWidgetItem([folder["name"], ""])
            item.setData(0, Qt.ItemDataRole.UserRole, ("folder", folder["id"]))
            self.folder_tree.addTopLevelItem(item)
            roots[folder["id"]] = item
        all_entries = self.store.rows("SELECT task_id,start_at,end_at FROM entries")
        totals = {}
        for entry in all_entries:
            totals[entry["task_id"]] = totals.get(entry["task_id"], 0) + seconds_between(entry["start_at"], entry["end_at"])
        active = self.store.active()
        if active:
            totals[active["task_id"]] = totals.get(active["task_id"], 0) + seconds_between(active["start_at"], now())
        for task in self.store.tasks():
            text = ("● " if task["pinned"] else "") + task["name"]
            item = QTreeWidgetItem([text, fmt(totals.get(task["id"], 0))])
            item.setData(0, Qt.ItemDataRole.UserRole, ("task", task["id"]))
            item.setForeground(0, QColor(task["color"]))
            roots.get(task["folder_id"], roots[None]).addChild(item)
        for root in roots.values():
            root.setExpanded(True)
        if selected:
            iterator = __import__("PySide6.QtWidgets", fromlist=["QTreeWidgetItemIterator"]).QTreeWidgetItemIterator(self.folder_tree)
            while iterator.value():
                if iterator.value().data(0, Qt.ItemDataRole.UserRole) == selected:
                    self.folder_tree.setCurrentItem(iterator.value())
                    break
                iterator += 1
        self.folder_tree.blockSignals(False)
        self.refresh_task_detail()

    def stats_period_changed(self, *_args):
        self.update_stats_range_label()
        self.refresh_task_detail()

    def update_stats_range_label(self):
        period = self.period_choice.currentText()
        chosen = self.stats_anchor
        if period == "日/周":
            start = chosen - timedelta(days=chosen.weekday())
            end = start + timedelta(days=6)
            label = (f"{start:%Y/%m/%d}–{end:%m/%d}" if start.year == end.year
                     else f"{start:%y/%m/%d}–{end:%y/%m/%d}")
            detail = f"{start:%Y-%m-%d} 至 {end:%Y-%m-%d}"
        elif period == "周/月":
            label = f"{chosen:%Y年%m月}"
            detail = label
        else:
            label = f"{chosen:%Y年}"
            detail = label
        self.stats_range_button.setText(label)
        self.stats_range_button.setToolTip(f"选择统计时段：{detail}")

    def shift_stats_range(self, offset):
        chosen = self.stats_anchor
        period = self.period_choice.currentText()
        if period == "日/周":
            self.stats_anchor = chosen + timedelta(days=7 * offset)
        elif period == "周/月":
            year, month_index = divmod(chosen.year * 12 + chosen.month - 1 + offset, 12)
            month = month_index + 1
            last_day = (next_month(date(year, month, 1)) - timedelta(days=1)).day
            self.stats_anchor = date(year, month, min(chosen.day, last_day))
        else:
            year = min(2200, max(1900, chosen.year + offset))
            self.stats_anchor = date(year, chosen.month, min(chosen.day, (next_month(date(year, chosen.month, 1)) - timedelta(days=1)).day))
        self.update_stats_range_label()
        self.refresh_task_detail()

    def pick_stats_range(self):
        period = self.period_choice.currentText()
        chosen = self.stats_anchor
        if period == "日/周":
            dialog = QDialog(self)
            dialog.setWindowTitle("选择一周")
            layout = QVBoxLayout(dialog)
            calendar = QCalendarWidget()
            calendar.setSelectedDate(QDate(chosen.year, chosen.month, chosen.day))
            calendar.clicked.connect(dialog.accept)
            layout.addWidget(calendar)
            if dialog.exec():
                self.stats_anchor = calendar.selectedDate().toPython()
        elif period == "周/月":
            dialog = QDialog(self)
            dialog.setWindowTitle("选择年份和月份")
            layout = QHBoxLayout(dialog)
            years = NoWheelComboBox()
            years.addItems([str(year) for year in range(1900, 2201)])
            years.setCurrentText(str(chosen.year))
            months = NoWheelComboBox()
            months.addItems([f"{month:02d}月" for month in range(1, 13)])
            months.setCurrentIndex(chosen.month - 1)
            layout.addWidget(years)
            layout.addWidget(months)
            apply_button = QPushButton("确定")
            apply_button.clicked.connect(dialog.accept)
            layout.addWidget(apply_button)
            if dialog.exec():
                year, month = int(years.currentText()), months.currentIndex() + 1
                last_day = (next_month(date(year, month, 1)) - timedelta(days=1)).day
                self.stats_anchor = date(year, month, min(chosen.day, last_day))
        else:
            year, ok = QInputDialog.getInt(self, "选择年份", "年份", chosen.year, 1900, 2200)
            if ok:
                last_day = (next_month(date(year, chosen.month, 1)) - timedelta(days=1)).day
                self.stats_anchor = date(year, chosen.month, min(chosen.day, last_day))
        self.update_stats_range_label()
        self.refresh_task_detail()

    def refresh_task_detail(self, *_args):
        item = self.folder_tree.currentItem()
        selected = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not selected:
            self.stats_label.setText("选择任务或文件夹查看统计")
            self.compare_label.setText("")
            self.stats_chart.set_data([], [], -1, self.accent, self.dark)
            self.entries_table.setRowCount(0)
            return
        if selected[0] == "task":
            task_ids = [selected[1]]
            title = self.store.one("SELECT name FROM tasks WHERE id=?", (selected[1],))["name"]
        else:
            folder_id = selected[1]
            task_ids = [t["id"] for t in self.store.tasks(True) if t["folder_id"] == folder_id]
            title = item.text(0)
        chosen = self.stats_anchor
        period = self.period_choice.currentText()
        task_set = set(task_ids)
        def amount(first, last):
            start = datetime.combine(first, time.min).astimezone().isoformat(timespec="seconds")
            end = datetime.combine(last, time.min).astimezone().isoformat(timespec="seconds")
            return sum(value for (task_id, _name), value in self.store.totals(start, end).items() if task_id in task_set)
        buckets, highlight, current_range, previous_range = chart_periods(chosen, period)
        labels = [label for _start, _end, label in buckets]
        values = [amount(start, end) for start, end, _label in buckets]
        self.stats_chart.set_data(labels, values, highlight, self.accent, self.dark)
        current = amount(*current_range)
        previous = amount(*previous_range)
        unit = {"日/周": "日", "周/月": "周", "月/年": "月"}[period]
        period_label = {"日/周": f"{chosen:%m/%d} 日累计", "周/月": "所选周累计", "月/年": f"{chosen:%Y-%m} 月累计"}[period]
        self.stats_label.setText(f"{title} · {period_label} {fmt(current)}")
        difference = current - previous
        direction = "增加" if difference > 0 else "减少" if difference < 0 else "持平"
        if previous:
            comparison = f"较前一{unit}{direction} {fmt(abs(difference))}（{abs(difference) / previous * 100:.1f}%）"
        elif current:
            comparison = f"较前一{unit}增加 {fmt(current)}（前期为 0，百分比不适用）"
        else:
            comparison = f"较前一{unit}持平 · 00:00:00"
        self.compare_label.setText(comparison)
        rows = [row for row in self.store.rows("SELECT * FROM entries ORDER BY start_at DESC") if row["task_id"] in task_ids][:40]
        self.entries_table.setRowCount(len(rows))
        for r, entry in enumerate(rows):
            for c, value in enumerate((parse(entry["start_at"]).strftime("%m-%d %H:%M"), parse(entry["end_at"]).strftime("%m-%d %H:%M"), fmt(seconds_between(entry["start_at"], entry["end_at"])))):
                self.entries_table.setItem(r, c, QTableWidgetItem(value))

    def refresh_notes(self):
        self.notes_loading = True
        self.notes_tree.blockSignals(True)
        self.notes_tree.clear()
        metrics = self.notes_tree.fontMetrics()
        width = max(100, self.notes_tree.header().sectionSize(0) - 66)
        for note in self.store.notes():
            full_association = note["goal_title"] or "收集箱"
            parent = QTreeWidgetItem(["", association_label(full_association)])
            parent.setToolTip(1, full_association)
            parent.setData(0, Qt.ItemDataRole.UserRole, ("note", note["id"]))
            parent.setData(1, Qt.ItemDataRole.UserRole, note["goal_id"])
            self._set_list_text(parent, note["body"], metrics, width, 3, ("note", note["id"]) in self.note_expanded)
            parent.setCheckState(0, Qt.CheckState.Checked if note["done"] else Qt.CheckState.Unchecked)
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.notes_tree.addTopLevelItem(parent)
            for entry in self.store.note_items(note["id"]):
                child = QTreeWidgetItem(["", "候选条目"])
                child.setData(0, Qt.ItemDataRole.UserRole, ("item", entry["id"], note["id"]))
                self._set_list_text(child, entry["title"], metrics, width - 24, 1, ("item", entry["id"]) in self.note_expanded)
                child.setCheckState(0, Qt.CheckState.Checked if entry["done"] else Qt.CheckState.Unchecked)
                child.setFlags(child.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                parent.addChild(child)
            parent.setExpanded(True)
        self.notes_tree.blockSignals(False)
        self.notes_loading = False

    def note_changed(self, item, column):
        if self.notes_loading or column != 0:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        done = item.checkState(0) == Qt.CheckState.Checked
        note_id = data[1] if data[0] == "note" else data[2]
        note_before = self.store.one("SELECT done FROM notes WHERE id=?", (note_id,))
        note_was_complete = bool(note_before and note_before["done"])
        if data[0] == "note":
            link = self.store.one("SELECT todo_id FROM notes WHERE id=?", (data[1],))
        else:
            link = self.store.one("SELECT todo_id FROM note_items WHERE id=?", (data[1],))
        linked = self.store.one("SELECT goal_id FROM todos WHERE id=?", (link["todo_id"],)) if link and link["todo_id"] else None
        goal_id = linked["goal_id"] if linked else None
        before = self.store.todos(goal_id=goal_id) if goal_id else []
        was_complete = bool(before) and all(x["done"] for x in before)
        if data[0] == "note":
            self.store.set_done("notes", data[1], done)
        else:
            self.store.set_note_item_done(data[1], done)
        if goal_id:
            self._check_goal_completion(goal_id, was_complete)
        if data[0] == "note" and done and not note_was_complete:
            self._show_confetti("便签完成！", "已记录这项完成")
        elif data[0] == "item":
            items = self.store.note_items(note_id)
            if items and all(row["done"] for row in items) and not note_was_complete:
                self._show_confetti("便签条目全部完成！", "可手动勾选整张便签")
        self.refresh_goals()

    def _show_confetti(self, title, subtitle):
        self.confetti = ConfettiDialog(self, self.accent, title, subtitle)
        self.confetti.move(self.geometry().center() - self.confetti.rect().center())
        self.confetti.show()
        self._notify(title, subtitle)

    def note_context_menu(self, point):
        item = self.notes_tree.itemAt(point)
        if not item:
            return
        if item.data(0, Qt.ItemDataRole.UserRole)[0] == "item":
            item = item.parent()
        self.notes_tree.setCurrentItem(item)
        menu = QMenu(self)
        edit = menu.addAction("编辑便签")
        archive = menu.addAction("归档便签")
        delete = menu.addAction("删除便签")
        chosen = menu.exec(self.notes_tree.viewport().mapToGlobal(point))
        if chosen == edit:
            self.edit_note()
        elif chosen == archive:
            self.archive_note()
        elif chosen == delete:
            self.delete_note()

    def note_clicked(self, item, column):
        if column == 0:
            self._toggle_list_text(item, self.notes_tree, self.note_expanded)

    def open_note(self, note_id):
        if self.store.one("SELECT 1 FROM notes WHERE id=?", (note_id,)):
            QuickNoteDialog(self, note_id).exec()

    def edit_note(self, item=None, _column=0):
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else self._selected_note_data()
        if not data:
            return
        note_id = data[1] if data[0] == "note" else data[2]
        self.open_note(note_id)

    def archive_note(self):
        data = self._selected_note_data()
        if data:
            self.store.archive_note(data[1] if data[0] == "note" else data[2])
            self.refresh_notes()

    def delete_note(self):
        data = self._selected_note_data()
        if not data:
            return
        note_id = data[1] if data[0] == "note" else data[2]
        if QMessageBox.question(self, "确认删除便签", "删除这张便签及所有候选条目？此操作不可撤销。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.store.delete_note(note_id)
            self.refresh_notes()

    def show_archived_notes(self):
        self._show_archive("便签归档", [(n["id"], n["body"].replace("\n", " / ")[:100]) for n in self.store.notes(True)], "notes")

    def _show_archive(self, title, rows, kind):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(350, 360)
        layout = QVBoxLayout(dialog)
        listing = QTreeWidget()
        listing.setHeaderLabels([title])
        for item_id, label in rows:
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, item_id)
            listing.addTopLevelItem(item)
        layout.addWidget(listing)
        buttons = QHBoxLayout()
        restore = QPushButton("恢复")
        delete = QPushButton("删除")
        close = QPushButton("关闭")
        for button in (restore, delete, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        def apply_action(remove=False):
            item = listing.currentItem()
            if not item:
                return
            item_id = item.data(0, Qt.ItemDataRole.UserRole)
            if remove:
                warning = "删除任务及全部历史计时记录？" if kind == "tasks" else "删除便签及所有候选条目？"
                if QMessageBox.question(dialog, "确认删除", warning, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return
                if kind == "tasks":
                    self.store.delete_task(item_id)
                else:
                    self.store.delete_note(item_id)
            elif kind == "tasks":
                self.store.execute("UPDATE tasks SET archived=0 WHERE id=?", (item_id,))
            else:
                self.store.archive_note(item_id, False)
            listing.takeTopLevelItem(listing.indexOfTopLevelItem(item))
            self.refresh_all()
        restore.clicked.connect(lambda: apply_action(False))
        delete.clicked.connect(lambda: apply_action(True))
        close.clicked.connect(dialog.accept)
        dialog.exec()

    def _selected_note_data(self):
        item = self.notes_tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def assign_note(self):
        data = self._selected_note_data()
        if not data:
            return
        note_id = data[1] if data[0] == "note" else data[2]
        goals = self.store.goals(date.today().isoformat())
        if not goals:
            QMessageBox.information(self, "归入目标", "请先建立今天的目标。")
            return
        labels = [goal["title"] for goal in goals]
        label, ok = QInputDialog.getItem(self, "归入目标", "选择今天的目标", labels, 0, False)
        if ok:
            goal_id = goals[labels.index(label)]["id"]
            self.store.execute("UPDATE notes SET goal_id=? WHERE id=?", (goal_id, note_id))
            linked = self.store.one("SELECT todo_id FROM notes WHERE id=?", (note_id,))
            if linked and linked["todo_id"]:
                self.store.execute("UPDATE todos SET goal_id=? WHERE id=?", (goal_id, linked["todo_id"]))
            for entry in self.store.note_items(note_id):
                if entry["todo_id"]:
                    self.store.execute("UPDATE todos SET goal_id=? WHERE id=?", (goal_id, entry["todo_id"]))
            self.refresh_notes()
            self.refresh_goals()

    def backup_data(self):
        path, _ = QFileDialog.getSaveFileName(self, "备份完整数据库", "tasktime-backup.db", "SQLite (*.db)")
        if path:
            self.store.backup(path)
            self._notify("备份完成", path)

    def change_data_directory(self):
        if os.environ.get("TASKTIME_DATA_DIR"):
            QMessageBox.information(self, "数据位置", "当前由 TASKTIME_DATA_DIR 环境变量指定目录，请先取消该变量。")
            return
        if self.store.active() or self.store.pomo():
            QMessageBox.information(self, "数据位置", "请先结束任务计时和番茄钟，再更改数据位置。")
            return
        selected = QFileDialog.getExistingDirectory(self, "选择新的本地数据目录", str(self.store.path.parent))
        if not selected:
            return
        target_dir = Path(selected).resolve()
        if target_dir == self.store.path.parent.resolve():
            return
        destination = target_dir / "tasktime.db"
        use_existing = False
        if destination.exists():
            prompt = QMessageBox(self)
            prompt.setWindowTitle("目标目录已有数据")
            prompt.setText("这个目录已有 tasktime.db。请选择要使用的数据。")
            existing_button = prompt.addButton("使用已有数据", QMessageBox.ButtonRole.AcceptRole)
            replace_button = prompt.addButton("复制当前数据并替换", QMessageBox.ButtonRole.DestructiveRole)
            prompt.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            prompt.exec()
            if prompt.clickedButton() not in (existing_button, replace_button):
                return
            use_existing = prompt.clickedButton() is existing_button
        target_lock = None
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_lock = QLockFile(str(target_dir / "tasktime.lock"))
            if not target_lock.tryLock(0):
                raise RuntimeError("目标目录正在被另一份时刻程序使用")
            prepare_data_directory(self.store, target_dir, use_existing)
            set_data_directory(target_dir)
        except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "更改数据位置失败", str(error))
            return
        finally:
            if target_lock and target_lock.isLocked():
                target_lock.unlock()
        QMessageBox.information(self, "数据位置已更改", f"数据目录：\n{target_dir}\n\n程序现在将重启；原目录中的数据仍保留。")
        self.quit_app()
        program = sys.executable
        arguments = [] if getattr(sys, "frozen", False) else [str(Path(__file__).resolve())]
        result = QProcess.startDetached(program, arguments, str(Path(program).parent))
        launched = result[0] if isinstance(result, tuple) else bool(result)
        if not launched:
            ctypes.windll.user32.MessageBoxW(None, "自动重启失败，请重新打开时刻程序。新数据位置已经保存。", "时刻", 0x30)

    def export_data(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出时间记录", "tasktime-entries.csv", "CSV (*.csv)")
        if path:
            self.store.export_csv(path)
            self._notify("导出完成", path)


def backup_before_migration():
    folder = data_directory()
    db_path = folder / "tasktime.db"
    backup_path = folder / "tasktime-pre-qt.db"
    if not db_path.exists() or backup_path.exists():
        return
    original = sqlite3.connect(str(db_path))
    backup = sqlite3.connect(str(backup_path))
    try:
        with backup:
            original.backup(backup)
    finally:
        backup.close()
        original.close()


_error_dialog_open = False
_fault_log = None


def report_exception(kind, value, tb):
    global _error_dialog_open
    error_text = "".join(traceback.format_exception(kind, value, tb))
    try:
        log = data_directory() / "crash.log"
        with log.open("a", encoding="utf-8") as out:
            out.write(f"\n[{datetime.now().isoformat()}]\n{error_text}\n")
        location = str(log)
    except Exception:
        location = "日志写入失败"
    if _error_dialog_open:
        return
    _error_dialog_open = True
    try:
        app = QApplication.instance()
        if app:
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("时刻 · 运行错误")
            box.setText("程序遇到错误。请复制详情，或发送日志以便排查。")
            box.setInformativeText(f"日志位置：{location}")
            box.setDetailedText(error_text)
            copy_button = box.addButton("复制报错", QMessageBox.ButtonRole.ActionRole)
            box.addButton("关闭", QMessageBox.ButtonRole.AcceptRole)
            box.setMinimumWidth(460)
            box.exec()
            if box.clickedButton() is copy_button:
                app.clipboard().setText(error_text)
        elif sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, error_text[-3000:], "时刻 · 运行错误", 0x10)
    finally:
        _error_dialog_open = False


class SafeApplication(QApplication):
    def notify(self, receiver, event):
        try:
            return super().notify(receiver, event)
        except Exception:
            report_exception(*sys.exc_info())
            return False


def enable_fatal_traceback():
    global _fault_log
    try:
        _fault_log = (data_directory() / "fatal-crash.log").open("a", encoding="utf-8")
        _fault_log.write(f"\n[{datetime.now().isoformat()}] Program started\n")
        _fault_log.flush()
        faulthandler.enable(file=_fault_log, all_threads=True)
    except Exception:
        pass


def main():
    sys.excepthook = report_exception
    enable_fatal_traceback()
    app = SafeApplication(sys.argv)
    app.setApplicationName("时刻")
    app.setOrganizationName("TaskTimeDesk")
    lock = QLockFile(str(data_directory() / "tasktime.lock"))
    if not lock.tryLock(0):
        QMessageBox.information(None, "时刻", "程序已经运行。请从 Windows 通知区域图标打开。")
        return 0
    splash = StartupSplash()
    splash.show()
    app.processEvents()
    backup_before_migration()
    window = MainWindow(app)
    def reveal():
        window.show()
        splash.close()
        QTimer.singleShot(0, window._recover_timer)
    QTimer.singleShot(700, reveal)
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        report_exception(type(error), error, error.__traceback__)
        sys.exit(1)
