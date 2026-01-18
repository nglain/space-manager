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
    QMenu, QDialog, QSpinBox, QMessageBox, QFrame, QScrollArea,
    QGraphicsDropShadowEffect, QGraphicsBlurEffect, QGraphicsOpacityEffect
)
from PyQt6.QtCore import (
    Qt, QTimer, QSize, QMetaObject, Q_ARG, pyqtSignal, QObject,
    QPropertyAnimation, QEasingCurve, QSequentialAnimationGroup, QParallelAnimationGroup
)
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut, QFont, QAction, QPixmap, QPainter, QColor, QFontDatabase
from AppKit import NSWorkspace, NSImage, NSBitmapImageRep, NSPNGFileType, NSRunningApplication
from Foundation import NSURL, NSData
import objc
from pynput import keyboard
import Quartz
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGWindowListOptionOnScreenOnly,
    kCGWindowListExcludeDesktopElements,
    kCGNullWindowID
)

CONFIG_PATH = Path.home() / "Клэр" / "apps" / "space-manager" / "config.json"

# Кэш иконок приложений
_app_icon_cache = {}
_running_apps_cache = {}  # Кэш запущенных приложений


def _get_running_apps_map():
    """Получить словарь запущенных приложений: имя -> NSRunningApplication"""
    global _running_apps_cache
    workspace = NSWorkspace.sharedWorkspace()
    apps = workspace.runningApplications()
    result = {}
    for app in apps or []:
        name = app.localizedName()
        if name:
            result[name.lower()] = app
    return result


def get_app_icon(app_name: str, target_size: int = 16) -> QPixmap:
    """Получить иконку приложения"""
    cache_key = f"{app_name}_{target_size}"
    if cache_key in _app_icon_cache:
        return _app_icon_cache[cache_key]

    icon = None
    workspace = NSWorkspace.sharedWorkspace()
    app_name_lower = app_name.lower()

    try:
        # Метод 1: Поиск среди запущенных по точному имени
        running = _get_running_apps_map()
        if app_name_lower in running:
            icon = running[app_name_lower].icon()

        # Метод 2: Поиск по частичному совпадению
        if not icon:
            for name, app in running.items():
                if app_name_lower in name or name in app_name_lower:
                    icon = app.icon()
                    break

        # Метод 3: Через fullPathForApplication
        if not icon:
            app_path = workspace.fullPathForApplication_(app_name)
            if app_path:
                icon = workspace.iconForFile_(app_path)

    except Exception:
        pass

    # Конвертировать NSImage в QPixmap через TIFF->PNG
    pixmap = QPixmap(target_size, target_size)
    pixmap.fill(Qt.GlobalColor.transparent)

    if icon:
        try:
            tiff_data = icon.TIFFRepresentation()
            if tiff_data:
                bitmap = NSBitmapImageRep.imageRepWithData_(tiff_data)
                if bitmap:
                    png_data = bitmap.representationUsingType_properties_(NSPNGFileType, None)
                    if png_data:
                        temp_pixmap = QPixmap()
                        if temp_pixmap.loadFromData(bytes(png_data)):
                            pixmap = temp_pixmap.scaled(
                                target_size, target_size,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation
                            )
        except Exception:
            pass

    _app_icon_cache[cache_key] = pixmap
    return pixmap


def group_windows_by_app(windows: list) -> dict:
    """Группировать окна по приложениям"""
    groups = {}
    for w in windows:
        app = w.get("app", "Unknown") if isinstance(w, dict) else str(w)
        if app not in groups:
            groups[app] = []
        groups[app].append(w)
    return groups


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


def get_windows_on_current_space(include_minimized: bool = False):
    """Получить список окон на ТЕКУЩЕМ Space (опционально со свёрнутыми)"""
    try:
        from Quartz import kCGWindowListOptionAll

        if include_minimized:
            options = kCGWindowListOptionAll | kCGWindowListExcludeDesktopElements
        else:
            options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements

        windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID)

        skip_apps = {'Window Server', 'Dock', 'Control Center', 'Spotlight',
                     'SystemUIServer', 'NotificationCenter', 'CursorUIViewService',
                     'Notification Center', 'com.apple.WebKit', 'universalAccessAuthWarn',
                     'TextInputMenuAgent', 'Пункт управления'}

        result = []
        for w in windows:
            owner = w.get('kCGWindowOwnerName', '')
            layer = w.get('kCGWindowLayer', 0)
            title = w.get('kCGWindowName', '')
            on_screen = w.get('kCGWindowIsOnscreen', True)

            # Только обычные окна (layer=0), пропускаем системные
            if layer == 0 and owner and owner not in skip_apps and title:
                result.append({
                    "app": owner,
                    "title": title,
                    "minimized": not on_screen
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
    """Подобрать оптимальный размер сетки (максимум 4x4)"""
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
    else:
        return (4, 4)  # Максимум 4x4


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


class AppItemWidget(QWidget):
    """Компактный виджет приложения с иконкой и QMenu при клике"""

    def __init__(self, app_name: str, windows: list, is_active_space: bool = False):
        super().__init__()
        self.app_name = app_name
        self.windows = windows
        self.is_active = is_active_space
        self.setFixedHeight(20)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # Иконка
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(14, 14)
        self.icon_pixmap = get_app_icon(app_name, 14)
        if not self.icon_pixmap.isNull():
            self.icon_label.setPixmap(self.icon_pixmap)
        else:
            self.icon_label.setText("●")
            color = '#fff' if is_active_space else '#888'
            self.icon_label.setStyleSheet(f"color: {color}; font-size: 10px;")
            self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        # Название + количество
        text = app_name[:12]
        if len(windows) > 1:
            text += f" ({len(windows)})"
        self.name_label = QLabel(text)
        self.name_label.setFont(QFont(".AppleSystemUIFont", 9))
        text_color = '#fff' if is_active_space else '#c5c5c7'
        self.name_label.setStyleSheet(f"color: {text_color}; background: transparent;")
        layout.addWidget(self.name_label)

        layout.addStretch()

        # Курсор pointer если есть несколько окон
        if len(windows) > 1:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        """Клик → QMenu с окнами"""
        if event.button() == Qt.MouseButton.LeftButton and len(self.windows) > 1:
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu {
                    background-color: rgba(40, 40, 42, 0.95);
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 8px;
                    padding: 4px;
                }
                QMenu::item {
                    color: #ffffff;
                    padding: 5px 15px 5px 8px;
                    border-radius: 4px;
                    font-size: 12px;
                }
                QMenu::item:selected {
                    background-color: rgba(10, 132, 255, 0.8);
                }
                QMenu::item:disabled {
                    color: #888;
                }
            """)

            # Окна
            for w in self.windows[:12]:
                title = w.get("title", "") if isinstance(w, dict) else str(w)
                minimized = w.get("minimized", False) if isinstance(w, dict) else False
                if title:
                    title = title[:42] + "..." if len(title) > 42 else title
                    # Свёрнутые помечаем иконкой
                    prefix = "📥 " if minimized else ""
                    action = QAction(f"{prefix}{title}", menu)
                    if minimized:
                        action.setEnabled(False)  # Серый цвет для свёрнутых
                    menu.addAction(action)

            if len(self.windows) > 12:
                menu.addSeparator()
                more = QAction(f"...ещё {len(self.windows) - 12}", menu)
                more.setEnabled(False)
                menu.addAction(more)

            menu.exec(event.globalPosition().toPoint())
        else:
            super().mousePressEvent(event)


class WindowItemWidget(QWidget):
    """Виджет отдельного окна (для развёрнутого отображения)"""

    def __init__(self, title: str, is_active_space: bool = False, minimized: bool = False):
        super().__init__()
        self.setFixedHeight(18)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 0, 0)  # Отступ слева
        layout.setSpacing(4)

        # Маркер (свёрнутые - специальная иконка)
        if minimized:
            bullet = QLabel("📥")
            bullet.setFont(QFont(".AppleSystemUIFont", 8))
        else:
            bullet = QLabel("›")
            bullet.setFont(QFont(".AppleSystemUIFont", 10))
        bullet.setFixedWidth(14)
        color = '#666' if minimized else ('#aaa' if is_active_space else '#777')
        bullet.setStyleSheet(f"color: {color}; background: transparent;")
        layout.addWidget(bullet)

        # Название окна (свёрнутые - серым)
        title_short = title[:22] + "..." if len(title) > 22 else title
        title_label = QLabel(title_short)
        title_label.setFont(QFont(".AppleSystemUIFont", 9))
        if minimized:
            text_color = '#666'
        else:
            text_color = '#ddd' if is_active_space else '#aaa'
        title_label.setStyleSheet(f"color: {text_color}; background: transparent;")
        layout.addWidget(title_label)

        layout.addStretch()


class SpaceCard(QFrame):
    """Карточка для одного Space — Apple style с иконками и анимациями"""

    def __init__(self, space_num: int, name: str = "", apps: list = None, is_active: bool = False, exists: bool = True):
        super().__init__()
        self.space_num = space_num
        self.space_name = name
        self.apps = apps or []
        self.is_active = is_active
        self.exists = exists
        self._glow_animation = None

        self.setFixedSize(230, 175)
        if exists:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.init_ui()
        self.update_style()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        # Заголовок: номер
        header = QHBoxLayout()
        header.setSpacing(8)

        self.num_label = QLabel(str(self.space_num))
        self.num_label.setFont(QFont(".AppleSystemUIFont", 18, QFont.Weight.Medium))
        header.addWidget(self.num_label)

        header.addStretch()
        layout.addLayout(header)

        # Название
        self.name_label = QLabel(self.space_name or f"Desktop {self.space_num}")
        self.name_label.setFont(QFont(".AppleSystemUIFont", 11, QFont.Weight.Medium))
        layout.addWidget(self.name_label)

        # Контейнер для иконок приложений
        self.apps_container = QWidget()
        self.apps_container.setStyleSheet("background: transparent;")
        self.apps_layout = QVBoxLayout(self.apps_container)
        self.apps_layout.setContentsMargins(0, 4, 0, 0)
        self.apps_layout.setSpacing(2)
        layout.addWidget(self.apps_container)

        layout.addStretch()

    def update_style(self):
        if not self.exists:
            self.setStyleSheet("""
                SpaceCard {
                    background-color: rgba(30, 30, 30, 0.3);
                    border: none;
                    border-radius: 12px;
                }
                QLabel { color: #3a3a3a; background: transparent; }
            """)
            self._stop_glow()
        elif self.is_active:
            self.setStyleSheet("""
                SpaceCard {
                    background-color: rgba(10, 132, 255, 0.9);
                    border: none;
                    border-radius: 12px;
                }
                QLabel { color: #ffffff; background: transparent; }
            """)
            self._start_glow()
        else:
            self.setStyleSheet("""
                SpaceCard {
                    background-color: rgba(58, 58, 60, 0.5);
                    border: none;
                    border-radius: 12px;
                }
                SpaceCard:hover {
                    background-color: rgba(72, 72, 74, 0.7);
                }
                QLabel { color: #d5d5d7; background: transparent; }
            """)
            self._stop_glow()

    def _start_glow(self):
        """Запустить анимацию свечения для активного Space"""
        if self._glow_animation:
            return

        # Создаём эффект тени для свечения
        glow = QGraphicsDropShadowEffect()
        glow.setBlurRadius(20)
        glow.setXOffset(0)
        glow.setYOffset(0)
        glow.setColor(QColor(10, 132, 255, 150))
        self.setGraphicsEffect(glow)

    def _stop_glow(self):
        """Остановить анимацию свечения"""
        self.setGraphicsEffect(None)
        self._glow_animation = None

    def set_active(self, active: bool):
        self.is_active = active
        self.update_style()

    def set_name(self, name: str):
        self.space_name = name
        self.name_label.setText(name or f"Desktop {self.space_num}")

    # Приложения которые всегда показываем развёрнуто
    EXPANDED_APPS = {'Terminal', 'Терминал', 'iTerm2', 'iTerm', 'Warp', 'Alacritty', 'Hyper'}

    def set_apps(self, windows: list):
        """Установить список окон: Terminal развёрнуто, остальные свёрнуто"""
        self.apps = windows

        # Очистить старые виджеты
        while self.apps_layout.count():
            item = self.apps_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not windows:
            empty_label = QLabel("Empty")
            empty_label.setFont(QFont(".AppleSystemUIFont", 10))
            empty_label.setStyleSheet(f"color: {'#888' if not self.is_active else '#aaa'}; background: transparent;")
            self.apps_layout.addWidget(empty_label)
            return

        # Группировать по приложениям
        groups = group_windows_by_app(windows)

        items_shown = 0
        max_items = 5  # Максимум элементов в карточке

        for app_name, app_windows in groups.items():
            if items_shown >= max_items:
                break

            # Проверяем нужно ли развернуть это приложение
            is_expanded_app = app_name in self.EXPANDED_APPS

            if is_expanded_app:
                # Показываем иконку приложения
                app_widget = AppItemWidget(app_name, [], self.is_active)  # Пустой список - без счётчика
                self.apps_layout.addWidget(app_widget)
                items_shown += 1

                # Показываем каждое окно отдельно
                for w in app_windows[:3]:  # Макс 3 окна терминала
                    if items_shown >= max_items:
                        break
                    title = w.get("title", "") if isinstance(w, dict) else str(w)
                    minimized = w.get("minimized", False) if isinstance(w, dict) else False
                    if title:
                        win_widget = WindowItemWidget(title, self.is_active, minimized)
                        self.apps_layout.addWidget(win_widget)
                        items_shown += 1
            else:
                # Свёрнутое отображение с QMenu
                widget = AppItemWidget(app_name, app_windows, self.is_active)
                self.apps_layout.addWidget(widget)
                items_shown += 1

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
    """Диалог настроек — Apple style"""

    def __init__(self, parent, rows: int, cols: int, total_spaces: int):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setStyleSheet("""
            QDialog {
                background-color: rgba(30, 30, 30, 0.95);
                border-radius: 12px;
            }
            QLabel {
                color: #e5e5e7;
                font-family: ".AppleSystemUIFont";
                font-size: 13px;
            }
            QSpinBox {
                background-color: rgba(118, 118, 128, 0.24);
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 10px;
                font-family: ".AppleSystemUIFont";
                font-size: 13px;
            }
            QPushButton {
                background-color: rgba(118, 118, 128, 0.24);
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-family: ".AppleSystemUIFont";
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(118, 118, 128, 0.4);
            }
            QPushButton:pressed {
                background-color: rgba(118, 118, 128, 0.5);
            }
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
    """Диалог переименования — Apple style"""

    def __init__(self, parent, space_num: int, current_name: str):
        super().__init__(parent)
        self.setWindowTitle(f"Rename Desktop {space_num}")
        self.setModal(True)
        self.setStyleSheet("""
            QDialog {
                background-color: rgba(30, 30, 30, 0.95);
                border-radius: 12px;
            }
            QLabel {
                color: #e5e5e7;
                font-family: ".AppleSystemUIFont";
                font-size: 13px;
            }
            QLineEdit {
                background-color: rgba(118, 118, 128, 0.24);
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 10px 12px;
                font-family: ".AppleSystemUIFont";
                font-size: 14px;
                selection-background-color: rgba(10, 132, 255, 0.5);
            }
            QLineEdit:focus {
                background-color: rgba(118, 118, 128, 0.32);
            }
            QPushButton {
                background-color: rgba(118, 118, 128, 0.24);
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-family: ".AppleSystemUIFont";
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(118, 118, 128, 0.4);
            }
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
        # Главный контейнер — Apple vibrancy style
        container = QFrame()
        container.setStyleSheet("""
            QFrame#mainContainer {
                background-color: rgba(28, 28, 30, 0.92);
                border-radius: 14px;
                border: 0.5px solid rgba(255, 255, 255, 0.1);
            }
        """)
        container.setObjectName("mainContainer")
        self.setCentralWidget(container)

        # Тень для окна
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setXOffset(0)
        shadow.setYOffset(10)
        shadow.setColor(QColor(0, 0, 0, 120))
        container.setGraphicsEffect(shadow)

        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(16, 12, 16, 16)
        main_layout.setSpacing(12)

        # Заголовок с drag area
        drag_header = DragHeader(self)
        header_layout = QHBoxLayout(drag_header)
        header_layout.setContentsMargins(4, 0, 0, 0)

        title = QLabel("Spaces")
        title.setFont(QFont(".AppleSystemUIFont", 15, QFont.Weight.DemiBold))
        title.setStyleSheet("color: #ffffff; background: transparent;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Кнопка закрытия — macOS style
        close_btn = QPushButton()
        close_btn.setFixedSize(12, 12)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff5f57;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #ff3b30;
            }
        """)
        close_btn.clicked.connect(self.hide)
        header_layout.addWidget(close_btn)

        main_layout.addWidget(drag_header)

        # Grid с карточками Spaces
        self.grid_widget = QWidget()
        self.grid_widget.setStyleSheet("background: transparent;")
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(10)
        main_layout.addWidget(self.grid_widget)

        self.rebuild_grid()

        # Кнопки управления — минималистичные
        controls = QHBoxLayout()
        controls.setSpacing(8)

        btn_style = """
            QPushButton {
                background-color: rgba(118, 118, 128, 0.2);
                color: #e5e5e7;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                font-family: ".AppleSystemUIFont";
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: rgba(118, 118, 128, 0.35);
            }
            QPushButton:pressed {
                background-color: rgba(118, 118, 128, 0.45);
            }
        """

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_apps)
        refresh_btn.setStyleSheet(btn_style)
        controls.addWidget(refresh_btn)

        scan_btn = QPushButton("Scan All")
        scan_btn.clicked.connect(self.scan_all_spaces)
        scan_btn.setStyleSheet(btn_style)
        controls.addWidget(scan_btn)

        controls.addStretch()

        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.show_settings)
        settings_btn.setStyleSheet(btn_style)
        controls.addWidget(settings_btn)

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
        self.tray_icon.setToolTip("Space Manager - Ctrl+` для открытия")

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
        # Fade-in анимация
        self.setWindowOpacity(0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.center_on_screen()

        # Плавное появление
        self._fade_animation = QPropertyAnimation(self, b"windowOpacity")
        self._fade_animation.setDuration(150)
        self._fade_animation.setStartValue(0)
        self._fade_animation.setEndValue(1)
        self._fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_animation.start()

        # Обновить приложения
        self.refresh_apps()

    def refresh_apps(self):
        """Обновить список окон включая свёрнутые"""
        windows = get_windows_on_current_space(include_minimized=True)
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

            # Собрать окна (включая свёрнутые)
            windows = get_windows_on_current_space(include_minimized=True)
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

    def hide_animated(self):
        """Плавное скрытие окна"""
        self._hide_animation = QPropertyAnimation(self, b"windowOpacity")
        self._hide_animation.setDuration(100)
        self._hide_animation.setStartValue(1)
        self._hide_animation.setEndValue(0)
        self._hide_animation.setEasingCurve(QEasingCurve.Type.InCubic)
        self._hide_animation.finished.connect(self._do_hide)
        self._hide_animation.start()

    def _do_hide(self):
        self.hide()
        self.setWindowOpacity(1)

    def closeEvent(self, event):
        event.ignore()
        self.hide_animated()


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

    # Глобальный hotkey: Ctrl+`
    current_keys = set()
    tilde_codes = {'`', '~', '§', '±'}  # Разные раскладки

    def on_press(key):
        current_keys.add(key)
        # Ctrl + ` (тильда)
        is_tilde = False
        try:
            if hasattr(key, 'char') and key.char in tilde_codes:
                is_tilde = True
        except:
            pass
        if keyboard.Key.ctrl in current_keys and is_tilde:
            hotkey_signal.toggle.emit()

    def on_release(key):
        current_keys.discard(key)

    # Запуск listener в отдельном потоке
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()

    print("Space Manager запущен!")
    print("Hotkey: Ctrl+`")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
