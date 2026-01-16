#!/usr/bin/env python3
"""
Space Manager v2.0 - Продвинутый менеджер рабочих столов macOS
С показом реальных окон, матрицей 5x5, и горячими клавишами.

Глобальный hotkey: Ctrl+Option+Space

Автор: Клэр для Ларри
"""

import sys
import json
import subprocess
import re
import threading
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QPushButton,
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSystemTrayIcon,
    QMenu, QDialog, QSpinBox, QMessageBox, QFrame, QScrollArea
)
from PyQt6.QtCore import Qt, QTimer, QSize, QMetaObject, Q_ARG, pyqtSignal, QObject
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut, QFont, QAction, QPixmap, QPainter, QColor
from pynput import keyboard
import Quartz
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGWindowListOptionOnScreenOnly,
    kCGWindowListExcludeDesktopElements,
    kCGNullWindowID
)

CONFIG_PATH = Path.home() / "Клэр" / "apps" / "space-manager" / "config.json"


class DragHeader(QFrame):
    """Заголовок для перетаскивания окна"""

    def __init__(self, parent_window):
        super().__init__()
        self.parent_window = parent_window
        self._drag_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFixedHeight(40)
        self.setStyleSheet("background: transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.parent_window.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
            self.parent_window.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)


def get_windows_on_current_space():
    """Получить ПОЛНЫЙ список окон на ТЕКУЩЕМ Space с заголовками"""
    try:
        options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
        windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID)

        skip_apps = {'Window Server', 'Dock', 'Control Center', 'Spotlight',
                     'SystemUIServer', 'NotificationCenter', 'CursorUIViewService',
                     'Notification Center', 'com.apple.WebKit', 'universalAccessAuthWarn'}

        result = []
        for w in windows:
            owner = w.get('kCGWindowOwnerName', '')
            layer = w.get('kCGWindowLayer', 0)
            title = w.get('kCGWindowName', '')

            # Только обычные окна (layer=0), пропускаем системные
            if layer == 0 and owner and owner not in skip_apps:
                # Формируем красивое название
                if title:
                    display = f"{owner}: {title[:25]}"
                else:
                    display = owner

                result.append({
                    "app": owner,
                    "title": title or "(без названия)",
                    "display": display[:40]
                })

        return result
    except Exception as e:
        print(f"Quartz error: {e}")
        return []


def get_spaces_count():
    """Получить количество Spaces из системных настроек"""
    try:
        # Читаем plist напрямую
        import plistlib
        plist_path = Path.home() / "Library/Preferences/com.apple.spaces.plist"
        if plist_path.exists():
            with open(plist_path, 'rb') as f:
                data = plistlib.load(f)
                # Ищем SpacesDisplayConfiguration -> Space Properties
                for display in data.get("SpacesDisplayConfiguration", {}).get("Management Data", {}).get("Monitors", []):
                    spaces = display.get("Spaces", [])
                    if spaces:
                        return len(spaces)
    except Exception as e:
        print(f"plist error: {e}")

    # Fallback через defaults
    try:
        result = subprocess.run(
            ["defaults", "read", "com.apple.spaces"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            # Считаем uuid = (это маркер каждого Space)
            count = result.stdout.count('"uuid" =')
            if count == 0:
                count = result.stdout.count('uuid =')
            if count > 0:
                return count
    except:
        pass
    return 4  # По умолчанию


def get_optimal_grid(total_spaces: int) -> tuple:
    """Подобрать оптимальный размер сетки для количества Spaces"""
    if total_spaces <= 2:
        return (1, 2)
    elif total_spaces <= 4:
        return (2, 2)
    elif total_spaces <= 6:
        return (2, 3)
    elif total_spaces <= 9:
        return (3, 3)
    elif total_spaces <= 12:
        return (3, 4)
    elif total_spaces <= 16:
        return (4, 4)
    else:
        return (5, 5)


def get_frontmost_app():
    """Получить активное приложение"""
    script = '''
    tell application "System Events"
        return name of first process whose frontmost is true
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=2
        )
        return result.stdout.strip()
    except:
        return ""


def get_space_count():
    """Попытаться определить количество Spaces"""
    # Читаем из com.apple.spaces plist
    try:
        result = subprocess.run(
            ["defaults", "read", "com.apple.spaces", "spans-displays"],
            capture_output=True, text=True
        )
        # Это не даёт прямого числа spaces, но можно использовать как workaround
    except:
        pass
    return 9  # Вернём максимум по умолчанию


class SpaceCard(QFrame):
    """Карточка для одного Space с приложениями"""

    def __init__(self, space_num: int, name: str = "", apps: list = None, is_active: bool = False, exists: bool = True):
        super().__init__()
        self.space_num = space_num
        self.space_name = name
        self.apps = apps or []
        self.is_active = is_active
        self.exists = exists  # Существует ли этот Space

        self.setFixedSize(220, 180)
        if exists:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ForbiddenCursor)
        self.init_ui()
        self.update_style()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Заголовок: номер + название
        header = QHBoxLayout()

        self.num_label = QLabel(str(self.space_num))
        self.num_label.setFont(QFont("SF Pro Display", 16, QFont.Weight.Bold))
        header.addWidget(self.num_label)

        header.addStretch()

        # Количество окон
        self.win_count = QLabel("")
        self.win_count.setFont(QFont("SF Pro Display", 10))
        header.addWidget(self.win_count)

        layout.addLayout(header)

        # Название
        self.name_label = QLabel(self.space_name or f"Desktop {self.space_num}")
        self.name_label.setFont(QFont("SF Pro Display", 11))
        self.name_label.setWordWrap(True)
        layout.addWidget(self.name_label)

        # Приложения (иконки или текст)
        self.apps_label = QLabel("")
        self.apps_label.setFont(QFont("SF Pro Display", 9))
        self.apps_label.setWordWrap(True)
        layout.addWidget(self.apps_label)

        layout.addStretch()

    def update_style(self):
        if not self.exists:
            # Несуществующий Space - серый и неактивный
            self.setStyleSheet("""
                QFrame {
                    background-color: #1A1A1A;
                    border: 1px dashed #333333;
                    border-radius: 12px;
                }
                QLabel {
                    color: #444444;
                    background: transparent;
                }
            """)
        elif self.is_active:
            self.setStyleSheet("""
                QFrame {
                    background-color: #007AFF;
                    border: 2px solid #005CBB;
                    border-radius: 12px;
                }
                QLabel {
                    color: white;
                    background: transparent;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame {
                    background-color: #2D2D2D;
                    border: 2px solid #444444;
                    border-radius: 12px;
                }
                QFrame:hover {
                    background-color: #3D3D3D;
                    border-color: #007AFF;
                }
                QLabel {
                    color: #CCCCCC;
                    background: transparent;
                }
            """)

    def set_active(self, active: bool):
        self.is_active = active
        self.update_style()

    def set_name(self, name: str):
        self.space_name = name
        self.name_label.setText(name or f"Desktop {self.space_num}")

    def set_apps(self, windows: list):
        """Установить список окон для отображения"""
        self.apps = windows
        if windows:
            # Показать каждое окно с новой строки (до 5)
            lines = []
            for w in windows[:5]:
                if isinstance(w, dict):
                    # Новый формат с полным заголовком
                    display = w.get("display", w.get("name", "?"))
                    lines.append(f"▸ {display[:28]}")
                else:
                    lines.append(f"▸ {str(w)[:28]}")

            self.apps_label.setText("\n".join(lines))
            self.win_count.setText(f"🪟 {len(windows)}")
        else:
            self.apps_label.setText("📭 пусто")
            self.win_count.setText("")

    def mousePressEvent(self, event):
        if not self.exists:
            return  # Игнорируем клики на несуществующих
        if event.button() == Qt.MouseButton.LeftButton:
            # Single click - переключение
            self.parent().parent().parent().switch_to_space(self.space_num)

    def mouseDoubleClickEvent(self, event):
        if not self.exists:
            return  # Игнорируем клики на несуществующих
        if event.button() == Qt.MouseButton.LeftButton:
            # Double click - переименование
            self.parent().parent().parent().rename_space(self.space_num)


class SettingsDialog(QDialog):
    """Диалог настроек"""

    def __init__(self, parent, rows: int, cols: int, total_spaces: int):
        super().__init__(parent)
        self.setWindowTitle("Настройки Space Manager")
        self.setModal(True)
        self.setStyleSheet("""
            QDialog { background-color: #1E1E1E; }
            QLabel { color: white; }
            QSpinBox {
                background-color: #2D2D2D;
                color: white;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px;
            }
            QPushButton {
                background-color: #444444;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover { background-color: #555555; }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Grid размер
        grid_group = QVBoxLayout()
        grid_group.addWidget(QLabel("Размер сетки:"))

        grid_layout = QHBoxLayout()
        grid_layout.addWidget(QLabel("Строк:"))
        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, 5)
        self.rows_spin.setValue(rows)
        grid_layout.addWidget(self.rows_spin)

        grid_layout.addWidget(QLabel("Столбцов:"))
        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(1, 5)
        self.cols_spin.setValue(cols)
        grid_layout.addWidget(self.cols_spin)
        grid_group.addLayout(grid_layout)
        layout.addLayout(grid_group)

        # Всего Spaces
        spaces_layout = QHBoxLayout()
        spaces_layout.addWidget(QLabel("Всего Spaces:"))
        self.spaces_spin = QSpinBox()
        self.spaces_spin.setRange(1, 25)  # До 5x5
        self.spaces_spin.setValue(total_spaces)
        spaces_layout.addWidget(self.spaces_spin)
        layout.addLayout(spaces_layout)

        # Подсказка
        hint = QLabel(
            "Совет: Создай нужное количество рабочих столов\n"
            "в Mission Control (F3) перед использованием.\n\n"
            "Не забудь включить хоткеи Ctrl+1-9 в:\n"
            "System Settings → Keyboard → Shortcuts → Mission Control"
        )
        hint.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(hint)

        # Кнопки
        buttons_layout = QHBoxLayout()
        save_btn = QPushButton("Сохранить")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(cancel_btn)
        buttons_layout.addWidget(save_btn)
        layout.addLayout(buttons_layout)


class RenameDialog(QDialog):
    """Диалог переименования Space"""

    def __init__(self, parent, space_num: int, current_name: str):
        super().__init__(parent)
        self.setWindowTitle(f"Переименовать Space {space_num}")
        self.setModal(True)
        self.setStyleSheet("""
            QDialog { background-color: #1E1E1E; }
            QLabel { color: white; }
            QLineEdit {
                background-color: #2D2D2D;
                color: white;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 8px;
                font-size: 14px;
            }
            QPushButton {
                background-color: #444444;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover { background-color: #555555; }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        layout.addWidget(QLabel(f"Название для Desktop {space_num}:"))

        self.name_edit = QLineEdit()
        self.name_edit.setText(current_name)
        self.name_edit.setPlaceholderText("Например: 🚀 API, 🎨 Frontend, 📚 Research...")
        self.name_edit.selectAll()
        layout.addWidget(self.name_edit)

        # Быстрые варианты
        quick_layout = QHBoxLayout()
        for emoji_name in ["🚀 Dev", "🎨 Design", "📚 Docs", "🧪 Test"]:
            btn = QPushButton(emoji_name)
            btn.clicked.connect(lambda checked, n=emoji_name: self.name_edit.setText(n))
            quick_layout.addWidget(btn)
        layout.addLayout(quick_layout)

        buttons_layout = QHBoxLayout()
        save_btn = QPushButton("Сохранить")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(cancel_btn)
        buttons_layout.addWidget(save_btn)
        layout.addLayout(buttons_layout)

        self.name_edit.returnPressed.connect(self.accept)


class SpaceManager(QMainWindow):
    """Главное окно Space Manager v2"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Space Manager")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Автоопределение количества Spaces
        detected_spaces = get_spaces_count()
        rows, cols = get_optimal_grid(detected_spaces)
        print(f"Обнаружено Spaces: {detected_spaces}, сетка: {rows}x{cols}")

        # Конфигурация по умолчанию
        self.config = {
            "rows": rows,
            "cols": cols,
            "total_spaces": detected_spaces,
            "space_names": {},
            "active_space": 1,
            "show_apps": True
        }
        self.load_config()

        # Обновить если изменилось количество Spaces
        if self.config["total_spaces"] != detected_spaces:
            self.config["total_spaces"] = detected_spaces
            self.config["rows"], self.config["cols"] = get_optimal_grid(detected_spaces)
            self.save_config()

        self.space_cards = {}
        self.init_ui()
        self.setup_shortcuts()
        self.setup_tray()

        # Таймер отключён - AppleScript тормозит
        # self.update_timer = QTimer()
        # self.update_timer.timeout.connect(self.update_apps_info)
        # self.update_timer.start(3000)

    def load_config(self):
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, 'r') as f:
                    saved = json.load(f)
                    self.config.update(saved)
            except:
                pass

    def save_config(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    def init_ui(self):
        # Главный контейнер с закруглёнными углами
        container = QFrame()
        container.setStyleSheet("""
            QFrame {
                background-color: #1E1E1E;
                border-radius: 16px;
                border: 1px solid #333333;
            }
        """)
        self.setCentralWidget(container)

        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # Заголовок с drag area (перетаскивание за заголовок)
        drag_header = DragHeader(self)
        header_layout = QHBoxLayout(drag_header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("🖥️ Space Manager")
        title.setFont(QFont("SF Pro Display", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: white; background: transparent;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Кнопка закрытия
        close_btn = QPushButton("×")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF5F57;
                color: white;
                border: none;
                border-radius: 12px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #FF3B30; }
        """)
        close_btn.clicked.connect(self.hide)
        header_layout.addWidget(close_btn)

        main_layout.addWidget(drag_header)

        # Подсказка
        hint = QLabel("Клик = переключить  •  Двойной клик = переименовать  •  Esc = скрыть")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #666666; font-size: 10px; background: transparent;")
        main_layout.addWidget(hint)

        # Grid с карточками Spaces
        self.grid_widget = QWidget()
        self.grid_widget.setStyleSheet("background: transparent;")
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(10)
        main_layout.addWidget(self.grid_widget)

        self.rebuild_grid()

        # Кнопки управления
        controls = QHBoxLayout()

        btn_style = """
            QPushButton {
                background-color: #333333;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 12px;
            }
            QPushButton:hover { background-color: #444444; }
        """

        refresh_btn = QPushButton("🔄 Обновить")
        refresh_btn.clicked.connect(self.refresh_apps)
        refresh_btn.setStyleSheet(btn_style)
        controls.addWidget(refresh_btn)

        scan_btn = QPushButton("🔍 Сканировать все")
        scan_btn.clicked.connect(self.scan_all_spaces)
        scan_btn.setStyleSheet(btn_style)
        scan_btn.setToolTip("Пройтись по всем Spaces и собрать окна (будет мигать)")
        controls.addWidget(scan_btn)

        settings_btn = QPushButton("⚙️ Настройки")
        settings_btn.clicked.connect(self.show_settings)
        settings_btn.setStyleSheet("""
            QPushButton {
                background-color: #333333;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 12px;
            }
            QPushButton:hover { background-color: #444444; }
        """)
        controls.addWidget(settings_btn)

        controls.addStretch()

        main_layout.addLayout(controls)

        self.adjustSize()
        self.center_on_screen()

    def rebuild_grid(self):
        # Удалить старые карточки
        for card in self.space_cards.values():
            card.deleteLater()
        self.space_cards.clear()

        rows = self.config["rows"]
        cols = self.config["cols"]
        total = self.config["total_spaces"]
        active = self.config["active_space"]
        names = self.config["space_names"]

        space_num = 1
        grid_size = rows * cols  # Всего ячеек в сетке

        for row in range(rows):
            for col in range(cols):
                exists = space_num <= total  # Существует ли этот Space
                name = names.get(str(space_num), "")
                is_active = (space_num == active) if exists else False

                # Загрузить сохранённые окна из конфига
                saved_windows = self.config.get("space_windows", {}).get(str(space_num), []) if exists else []

                card = SpaceCard(space_num, name, [], is_active, exists=exists)
                if exists:
                    card.set_apps(saved_windows)  # Показать сохранённые окна сразу
                else:
                    card.name_label.setText("—")
                    card.apps_label.setText("")
                    card.win_count.setText("")

                self.grid_layout.addWidget(card, row, col)
                self.space_cards[space_num] = card
                space_num += 1

        self.adjustSize()

    def center_on_screen(self):
        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width() - self.width()) // 2,
            (screen.height() - self.height()) // 2
        )

    def setup_shortcuts(self):
        # Escape для скрытия
        esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        esc_shortcut.activated.connect(self.hide)

        # Ctrl+Q для выхода
        quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        quit_shortcut.activated.connect(QApplication.quit)

        # Цифры 1-9 для быстрого переключения (когда окно активно)
        for i in range(1, 10):
            shortcut = QShortcut(QKeySequence(str(i)), self)
            shortcut.activated.connect(lambda n=i: self.switch_to_space(n))

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setToolTip("Space Manager - Ctrl+Opt+Space для открытия")

        tray_menu = QMenu()

        show_action = QAction("🖥️ Показать", self)
        show_action.triggered.connect(self.show_and_raise)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        # Быстрое переключение на Spaces из трея
        for i in range(1, min(10, self.config["total_spaces"] + 1)):
            name = self.config["space_names"].get(str(i), f"Desktop {i}")
            action = QAction(f"{i}: {name}", self)
            action.triggered.connect(lambda checked, n=i: self.switch_to_space(n))
            tray_menu.addAction(action)

        tray_menu.addSeparator()

        quit_action = QAction("Выход", self)
        quit_action.triggered.connect(QApplication.quit)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.tray_activated)
        self.tray_icon.show()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_and_raise()

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.center_on_screen()
        # Обновить приложения синхронно (Quartz быстрый)
        self.refresh_apps()

    def refresh_apps(self):
        """Обновить список окон (синхронно)"""
        windows = get_windows_on_current_space()
        self._update_apps_ui(windows)

    def scan_all_spaces(self):
        """Пройтись по всем Spaces и собрать окна"""
        import time

        self.hide()  # Скрыть окно чтобы не мешало

        total = self.config["total_spaces"]
        original_space = self.config.get("active_space", 1)

        # Собрать окна с каждого Space
        for space_num in range(1, min(total + 1, 10)):  # Ctrl+1-9 работает до 9
            # Переключиться на Space
            key_code = 17 + space_num
            script = f'tell application "System Events" to key code {key_code} using control down'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=2)
            time.sleep(0.3)  # Подождать переключения

            # Собрать окна
            windows = get_windows_on_current_space()
            if windows:
                if "space_windows" not in self.config:
                    self.config["space_windows"] = {}
                self.config["space_windows"][str(space_num)] = windows[:10]

                # Обновить карточку
                if space_num in self.space_cards:
                    self.space_cards[space_num].set_apps(windows)

        # Вернуться на исходный Space
        if original_space <= 9:
            key_code = 17 + original_space
            script = f'tell application "System Events" to key code {key_code} using control down'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=2)

        self.config["active_space"] = original_space
        self.save_config()

        # Показать окно снова
        time.sleep(0.3)
        self.show_and_raise()

    def _update_apps_ui(self, windows):
        """Обновить UI с окнами"""
        active = self.config.get("active_space", 1)

        # Сохранить окна для текущего Space
        if "space_windows" not in self.config:
            self.config["space_windows"] = {}
        if windows:
            self.config["space_windows"][str(active)] = windows[:10]  # до 10 окон
            self.save_config()

        # Показать окна на всех карточках (из сохранённых данных)
        for num, card in self.space_cards.items():
            saved_windows = self.config.get("space_windows", {}).get(str(num), [])
            if num == active and windows:
                card.set_apps(windows)  # Актуальные для текущего
            elif saved_windows:
                card.set_apps(saved_windows)  # Сохранённые для других
            else:
                card.set_apps([])

    def switch_to_space(self, space_num: int):
        if space_num > self.config["total_spaces"]:
            return

        # Обновить UI сразу
        old_active = self.config["active_space"]
        self.config["active_space"] = space_num

        if old_active in self.space_cards:
            self.space_cards[old_active].set_active(False)
        if space_num in self.space_cards:
            self.space_cards[space_num].set_active(True)

        self.save_config()

        # Скрыть окно сразу
        self.hide()

        # Асинхронно переключить Space (не блокируя UI)
        # key codes: 18=1, 19=2, 20=3, 21=4, 22=5, 23=6, 24=7, 25=8, 26=9
        key_code = 17 + space_num
        script = f'tell application "System Events" to key code {key_code} using control down'

        # Используем Popen чтобы не блокировать
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    def rename_space(self, space_num: int):
        current_name = self.config["space_names"].get(str(space_num), "")

        dialog = RenameDialog(self, space_num, current_name)
        if dialog.exec():
            new_name = dialog.name_edit.text().strip()
            self.config["space_names"][str(space_num)] = new_name
            if space_num in self.space_cards:
                self.space_cards[space_num].set_name(new_name)
            self.save_config()
            self.setup_tray()  # Обновить меню трея

    def show_settings(self):
        dialog = SettingsDialog(
            self,
            self.config["rows"],
            self.config["cols"],
            self.config["total_spaces"]
        )
        if dialog.exec():
            self.config["rows"] = dialog.rows_spin.value()
            self.config["cols"] = dialog.cols_spin.value()
            self.config["total_spaces"] = dialog.spaces_spin.value()
            self.save_config()
            self.rebuild_grid()
            self.setup_tray()

    def closeEvent(self, event):
        event.ignore()
        self.hide()


class HotkeySignal(QObject):
    """Сигнал для безопасного вызова из другого потока"""
    toggle = pyqtSignal()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Space Manager")

    window = SpaceManager()
    window.show_and_raise()  # Показать и загрузить приложения

    # Сигнал для toggle из hotkey потока
    hotkey_signal = HotkeySignal()
    hotkey_signal.toggle.connect(window.show_and_raise)

    # Глобальный hotkey: Ctrl+Option+Space
    current_keys = set()

    def on_press(key):
        current_keys.add(key)
        # Ctrl + Option + Space
        if (keyboard.Key.ctrl in current_keys and
            keyboard.Key.alt in current_keys and
            keyboard.Key.space in current_keys):
            hotkey_signal.toggle.emit()

    def on_release(key):
        current_keys.discard(key)

    # Запуск listener в отдельном потоке
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()

    print("Space Manager запущен!")
    print("Hotkey: Ctrl+Option+Space")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
