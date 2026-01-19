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
import ctypes
from ctypes import c_uint32, c_uint64, c_void_p, c_int
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QPushButton,
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSystemTrayIcon,
    QMenu, QDialog, QSpinBox, QMessageBox, QFrame, QScrollArea,
    QGraphicsDropShadowEffect, QGraphicsBlurEffect, QGraphicsOpacityEffect
)
from PyQt6.QtCore import (
    Qt, QTimer, QSize, QMetaObject, Q_ARG, pyqtSignal, QObject,
    QPropertyAnimation, QEasingCurve, QSequentialAnimationGroup, QParallelAnimationGroup,
    QMimeData, QProcess
)
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut, QFont, QAction, QPixmap, QPainter, QColor, QFontDatabase, QDrag
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

# SkyLight API для работы со Spaces
_skylight = None
_sls_connection = None
_space_ids_cache = {}


def _init_skylight():
    """Инициализировать SkyLight framework"""
    global _skylight, _sls_connection
    if _skylight is not None:
        return True
    try:
        _skylight = ctypes.CDLL('/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight')

        # SLSMainConnectionID
        SLSMainConnectionID = _skylight.SLSMainConnectionID
        SLSMainConnectionID.restype = c_uint32
        _sls_connection = SLSMainConnectionID()

        return _sls_connection > 0
    except Exception as e:
        print(f"SkyLight init error: {e}")
        return False


def get_space_ids_map():
    """Получить карту: номер Space -> ManagedSpaceID"""
    global _space_ids_cache
    if _space_ids_cache:
        return _space_ids_cache

    if not _init_skylight():
        return {}

    try:
        SLSCopyManagedDisplaySpaces = _skylight.SLSCopyManagedDisplaySpaces
        SLSCopyManagedDisplaySpaces.argtypes = [c_uint32]
        SLSCopyManagedDisplaySpaces.restype = c_void_p

        spaces_ref = SLSCopyManagedDisplaySpaces(_sls_connection)
        if not spaces_ref:
            return {}

        spaces = objc.objc_object(c_void_p=spaces_ref)

        result = {}
        for display in spaces:
            if isinstance(display, dict):
                space_list = display.get('Spaces', [])
                for i, s in enumerate(space_list):
                    space_id = s.get('ManagedSpaceID')
                    if space_id:
                        # Индекс начинается с 1
                        result[i + 1] = int(space_id)

        _space_ids_cache = result
        return result
    except Exception as e:
        print(f"get_space_ids error: {e}")
        return {}


# Кэш AeroSpace окон (обновляется при refresh_apps)
_aerospace_windows_cache = {}
_aerospace_cache_time = 0
_focused_workspace_cache = 1  # Кэш текущего активного workspace

def get_aerospace_windows_sync():
    """Синхронное получение окон AeroSpace (вызывать ДО Qt или из pre-cache)"""
    try:
        result = subprocess.run(
            ['/opt/homebrew/bin/aerospace', 'list-windows', '--all',
             '--format', '%{window-id}|%{app-name}|%{window-title}|%{workspace}'],
            capture_output=True, text=True, timeout=5,
            stdin=subprocess.DEVNULL
        )
        if result.returncode == 0:
            return result.stdout
    except Exception as e:
        print(f"[AEROSPACE] Sync error: {e}", flush=True)
    return None


def get_focused_workspace() -> int:
    """Получить текущий активный workspace из кэша (обновляется в pre-cache)"""
    global _focused_workspace_cache
    return _focused_workspace_cache


def update_focused_workspace_sync() -> int:
    """Синхронное обновление активного workspace (вызывать ДО Qt!)"""
    global _focused_workspace_cache
    try:
        result = subprocess.run(
            ['/opt/homebrew/bin/aerospace', 'list-workspaces', '--focused'],
            capture_output=True, text=True, timeout=2,
            stdin=subprocess.DEVNULL
        )
        if result.returncode == 0 and result.stdout.strip():
            ws = result.stdout.strip()
            if ws.isdigit():
                _focused_workspace_cache = int(ws)
                print(f"[PRE-CACHE] Focused workspace: {_focused_workspace_cache}", flush=True)
                return _focused_workspace_cache
    except Exception as e:
        print(f"[AEROSPACE] update_focused_workspace error: {e}", flush=True)
    return _focused_workspace_cache


def refresh_aerospace_cache():
    """Обновить кэш окон AeroSpace"""
    global _aerospace_windows_cache, _aerospace_cache_time
    import time
    import os

    try:
        # Используем pre-cached данные если свежие (< 3 сек)
        if _aerospace_cache_time and (time.time() - _aerospace_cache_time) < 3:
            print(f"[CACHE] Using cached data ({len(_aerospace_windows_cache)} windows)", flush=True)
            return

        print("[CACHE] Refreshing aerospace windows...", flush=True)

        # Запускаем синхронно через os.system (блокирует, но aerospace быстрый ~0.03 сек)
        tmp_file = '/tmp/aerospace_windows.txt'
        os.system(f'/opt/homebrew/bin/aerospace list-windows --all --format "%{{window-id}}|%{{app-name}}|%{{window-title}}|%{{workspace}}" > {tmp_file} 2>/dev/null')

        # Читаем результат сразу (os.system уже подождал)
        if os.path.exists(tmp_file) and os.path.getsize(tmp_file) > 0:
            with open(tmp_file, 'r') as f:
                output = f.read()
            if output:
                _parse_aerospace_output(output)
                print(f"[CACHE] Refreshed: {len(_aerospace_windows_cache)} windows", flush=True)
    except Exception as e:
        print(f"AeroSpace cache refresh error: {e}", flush=True)


def _parse_aerospace_output(output):
    """Парсить вывод aerospace list-windows"""
    global _aerospace_windows_cache, _aerospace_cache_time
    import time

    _aerospace_windows_cache = {}
    for line in output.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('|')
        if len(parts) >= 4:
            wid = parts[0].strip()
            app = parts[1].strip()
            title = parts[2].strip()
            workspace = parts[3].strip()
            # Сохраняем и ID, и workspace
            key = f"{app}|{title}"
            _aerospace_windows_cache[key] = {
                'id': int(wid),
                'workspace': workspace,
                'app': app,
                'title': title
            }
    _aerospace_cache_time = time.time()
    print(f"[CACHE] AeroSpace cache updated: {len(_aerospace_windows_cache)} windows", flush=True)


def get_windows_by_workspace():
    """Получить окна сгруппированные по workspace (из aerospace)"""
    result = {}
    for key, data in _aerospace_windows_cache.items():
        ws = data['workspace']
        if ws not in result:
            result[ws] = []
        result[ws].append({
            'app': data['app'],
            'title': data['title'],
            'window_id': data['id']
        })
    return result


def get_window_id_by_title(app_name: str, window_title: str) -> int:
    """Найти Window ID по имени приложения и заголовку окна.

    Использует кэш AeroSpace (новый формат с dict).
    """
    if not _aerospace_windows_cache:
        print(f"[GET_ID] Cache empty!", flush=True)
        return 0

    # Точное совпадение
    key = f"{app_name}|{window_title}"
    if key in _aerospace_windows_cache:
        wid = _aerospace_windows_cache[key]['id']
        print(f"[GET_ID] Exact match: {app_name} -> {wid}", flush=True)
        return wid

    # Частичное совпадение (title может быть обрезан)
    for cached_key, data in _aerospace_windows_cache.items():
        cached_app = data['app']
        cached_title = data['title']
        if cached_app == app_name:
            # Проверяем совпадение начала title (первые 30 символов)
            if (cached_title[:30] == window_title[:30] or
                window_title.startswith(cached_title[:30]) or
                cached_title.startswith(window_title[:30])):
                wid = data['id']
                print(f"[GET_ID] Partial match: {app_name} -> {wid}", flush=True)
                return wid

    print(f"[GET_ID] Not found: {app_name} | {window_title[:40]}", flush=True)
    return 0


def move_window_to_space(window_id: int, target_space_num: int) -> tuple:
    """
    Переместить окно на указанный Space/Workspace.

    Returns: (success: bool, message: str)

    Поддерживаемые методы (в порядке приоритета):
    1. AeroSpace (рекомендуется, не требует SIP)
    2. yabai (требует частичного отключения SIP)
    3. SkyLight API (часто не работает на современных macOS)
    """
    global _aerospace_cache_time
    import os

    # Метод 1: AeroSpace - через os.system в background (обход Qt блокировки)
    try:
        cmd = f'/opt/homebrew/bin/aerospace move-node-to-workspace {target_space_num} --window-id {window_id} </dev/null >/dev/null 2>&1 &'
        print(f"[MOVE] Executing: {cmd}", flush=True)
        os.system(cmd)
        # Сбрасываем кэш чтобы следующий refresh получил свежие данные
        _aerospace_cache_time = 0
        return True, "Перемещено через AeroSpace"
    except Exception as e:
        print(f"AeroSpace error: {e}")

    # Метод 2: yabai
    try:
        result = subprocess.run(
            ['yabai', '-m', 'window', str(window_id), '--space', str(target_space_num)],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            return True, "Перемещено через yabai"
    except FileNotFoundError:
        pass  # yabai не установлен
    except Exception as e:
        print(f"yabai error: {e}")

    # Метод 3: SkyLight API (fallback)
    if _init_skylight():
        space_ids = get_space_ids_map()
        target_space_id = space_ids.get(target_space_num)

        if target_space_id:
            try:
                wid_num = objc.lookUpClass('NSNumber').numberWithUnsignedInt_(window_id)
                ns_array = objc.lookUpClass('NSArray').arrayWithObject_(wid_num)

                SLSMoveWindowsToManagedSpace = _skylight.SLSMoveWindowsToManagedSpace
                SLSMoveWindowsToManagedSpace.argtypes = [c_uint32, c_void_p, c_uint64]
                SLSMoveWindowsToManagedSpace.restype = c_int

                result = SLSMoveWindowsToManagedSpace(
                    _sls_connection,
                    objc.pyobjc_id(ns_array),
                    target_space_id
                )

                if result == 0:
                    return True, "Перемещено через SkyLight"

            except Exception as e:
                print(f"SkyLight move error: {e}")

    return False, "Установите AeroSpace: brew install --cask nikitabobko/tap/aerospace"


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


def activate_window(app_name: str, window_title: str):
    """Активировать конкретное окно приложения"""
    # Экранируем кавычки в названии
    escaped_title = window_title.replace('"', '\\"').replace("'", "'\"'\"'")
    escaped_app = app_name.replace('"', '\\"')

    script = f'''
    tell application "{escaped_app}"
        activate
    end tell
    delay 0.1
    tell application "System Events"
        tell process "{escaped_app}"
            set frontmost to true
            try
                set targetWindow to first window whose name contains "{escaped_title}"
                perform action "AXRaise" of targetWindow
            end try
        end tell
    end tell
    '''

    subprocess.Popen(
        ["osascript", "-e", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )


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


class WindowItemWidget(QPushButton):
    """Виджет отдельного окна (на основе QPushButton для надёжного приёма событий мыши)"""

    def __init__(self, title: str, is_active_space: bool = False, minimized: bool = False, app_name: str = "", space_num: int = 0, window_id: int = 0):
        super().__init__()
        self.app_name = app_name
        self.window_title = title
        self.minimized = minimized
        self.is_active = is_active_space
        self.space_num = space_num  # Текущий Space окна
        self.window_id = window_id  # AeroSpace window ID для перемещения
        self._drag_start_pos = None

        self.setFixedHeight(24)
        self.setMouseTracking(True)
        if not minimized:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Получаем иконку
        icon_text = ""
        if minimized:
            icon_text = "📥 "
        elif app_name:
            pixmap = get_app_icon(app_name, 12)
            if not pixmap.isNull():
                self.setIcon(QIcon(pixmap))
                self.setIconSize(QSize(12, 12))

        # Формируем текст кнопки
        if minimized:
            display_title = title[:30] + "..." if len(title) > 30 else title
            display_title = icon_text + display_title + " (свёрнуто)"
            self.text_color = '#666'
        else:
            display_title = title[:40] + "..." if len(title) > 40 else title
            self.text_color = '#ddd' if is_active_space else '#aaa'

        self.setText(display_title)
        self.setFont(QFont(".AppleSystemUIFont", 9))

        self._update_style(hovered=False)

        # Подключаем клик
        self.clicked.connect(self._on_clicked)

    def _on_clicked(self):
        """Обработка клика - активировать окно"""
        if not self.minimized:
            print(f"[CLICK] Button clicked: {self.app_name}", flush=True)
            activate_window(self.app_name, self.window_title)
            main_window = self.window()
            if main_window:
                QTimer.singleShot(300, main_window.hide)

    def _update_style(self, hovered: bool):
        if self.minimized:
            self.setStyleSheet("""
                QPushButton {
                    background: rgba(50,50,52,0.2);
                    border: none;
                    border-radius: 4px;
                    text-align: left;
                    padding-left: 8px;
                    color: #666;
                }
            """)
        elif hovered:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(10, 132, 255, 0.5);
                    border: none;
                    border-radius: 4px;
                    text-align: left;
                    padding-left: 8px;
                    color: {self.text_color};
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(50,50,52,0.3);
                    border: none;
                    border-radius: 4px;
                    text-align: left;
                    padding-left: 8px;
                    color: {self.text_color};
                }}
                QPushButton:hover {{
                    background: rgba(10, 132, 255, 0.5);
                }}
            """)

    def enterEvent(self, event):
        print(f"[HOVER] Enter: {self.app_name}")
        if not self.minimized:
            self._update_style(hovered=True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._update_style(hovered=False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.minimized:
            # Сохраняем позицию для drag detection
            self._drag_start_pos = event.pos()
            print(f"[MOUSE] Press at {event.pos().x()},{event.pos().y()} on {self.app_name}", flush=True)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.minimized and self._drag_start_pos is not None:
            dist = (event.pos() - self._drag_start_pos).manhattanLength()
            if dist > 10:
                print(f"[MOUSE] Drag threshold reached: {dist}px", flush=True)
                self._drag_start_pos = None
                self._start_drag()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # Сбрасываем drag position при отпускании кнопки
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        """Контекстное меню для перемещения окна на другой Space"""
        if self.minimized:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: rgba(40, 40, 42, 0.95);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                color: #fff;
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: rgba(10, 132, 255, 0.8);
            }
        """)

        # Заголовок
        title_action = menu.addAction(f"📦 {self.app_name}")
        title_action.setEnabled(False)
        menu.addSeparator()

        # Подменю "Переместить на Space"
        move_menu = menu.addMenu("➜ Переместить на Space")
        move_menu.setStyleSheet(menu.styleSheet())

        for i in range(1, 17):  # 16 spaces
            if i != self.space_num:  # Не показываем текущий Space
                action = move_menu.addAction(f"Space {i}")
                action.triggered.connect(lambda checked, target=i: self._move_to_space(target))

        menu.exec(event.globalPos())

    def _move_to_space(self, target_space: int):
        """Переместить окно на указанный Space"""
        # Используем сохранённый window_id напрямую
        window_id = self.window_id
        if not window_id:
            # Fallback: пробуем найти через кэш
            window_id = get_window_id_by_title(self.app_name, self.window_title)

        print(f"[MOVE] Moving {self.app_name} (ID={window_id}) from Space {self.space_num} to Space {target_space}", flush=True)

        if window_id:
            success, message = move_window_to_space(window_id, target_space)
            print(f"[MOVE] Result: {success}, {message}", flush=True)

            if success:
                # Мгновенное обновление UI (без полного refresh)
                main_window = self.window()
                if main_window and hasattr(main_window, 'space_cards'):
                    # Скрываем себя (убираем из source карточки)
                    self.setVisible(False)
                    self.setEnabled(False)

                    # Добавляем в target карточку
                    if target_space in main_window.space_cards:
                        target_card = main_window.space_cards[target_space]
                        # Показываем успех на target карточке
                        target_card._show_success_flash()
                        # Добавляем окно в target
                        target_card._add_window_to_card(self.app_name, self.window_title, window_id)
        else:
            print(f"[MOVE] Window ID not found for {self.app_name}", flush=True)

    def _start_drag(self):
        """Начать drag операцию"""
        print(f"[DRAG] Starting drag: {self.app_name} - {self.window_title[:30]}, space={self.space_num}, wid={self.window_id}")
        drag = QDrag(self)
        mime_data = QMimeData()

        # Сохраняем информацию об окне в MIME (включая window_id!)
        data = json.dumps({
            "app_name": self.app_name,
            "window_title": self.window_title,
            "source_space": self.space_num,
            "window_id": self.window_id
        })
        mime_data.setData("application/x-space-window", data.encode())
        mime_data.setText(f"{self.app_name}: {self.window_title}")

        drag.setMimeData(mime_data)

        # Создаём визуализацию drag
        pixmap = QPixmap(180, 24)
        pixmap.fill(QColor(40, 40, 42, 220))
        painter = QPainter(pixmap)
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont(".AppleSystemUIFont", 10))
        text = f"{self.app_name}: {self.window_title[:20]}..."
        painter.drawText(5, 16, text)
        painter.end()

        drag.setPixmap(pixmap)
        drag.setHotSpot(pixmap.rect().center())

        # Меняем курсор на drag
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        drag.exec(Qt.DropAction.MoveAction)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


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
        self._is_drop_target = False  # Для визуализации drop

        self.setFixedSize(250, 190)
        if exists:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setAcceptDrops(True)  # Принимаем drop
        self.init_ui()
        self.update_style()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        # Заголовок: номер + редактируемый title
        header = QHBoxLayout()
        header.setSpacing(6)

        self.num_label = QLabel(str(self.space_num))
        self.num_label.setFont(QFont(".AppleSystemUIFont", 18, QFont.Weight.Medium))
        header.addWidget(self.num_label)

        # Редактируемый title рядом с номером
        self.name_label = QLabel(self.space_name if self.space_name else "")
        self.name_label.setFont(QFont(".AppleSystemUIFont", 12))
        header.addWidget(self.name_label)

        header.addStretch()

        # Кнопка редактирования title (скрыта по умолчанию)
        if self.exists:
            self.edit_btn = QPushButton("✎")
            self.edit_btn.setFixedSize(20, 20)
            self.edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.edit_btn.setToolTip("Редактировать название")
            self.edit_btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: none;
                    font-size: 12px;
                    color: #888;
                }
                QPushButton:hover {
                    color: #fff;
                }
            """)
            self.edit_btn.clicked.connect(self._on_edit_click)
            self.edit_btn.hide()  # Скрыта по умолчанию
            header.addWidget(self.edit_btn)
        else:
            self.edit_btn = None

        layout.addLayout(header)

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
        self.name_label.setText(name if name else "")

    def enterEvent(self, event):
        """Показать кнопку редактирования при наведении"""
        if self.edit_btn:
            self.edit_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Скрыть кнопку редактирования"""
        if self.edit_btn:
            self.edit_btn.hide()
        super().leaveEvent(event)

    def _on_edit_click(self):
        """Клик на кнопку редактирования"""
        # Находим главное окно и вызываем rename_space
        main_window = self.window()
        if hasattr(main_window, 'rename_space'):
            main_window.rename_space(self.space_num)

    # === Drag-n-drop support ===

    def dragEnterEvent(self, event):
        """Принимаем drag если это окно"""
        print(f"[DRAG] dragEnterEvent on Space {self.space_num}")
        if event.mimeData().hasFormat("application/x-space-window"):
            # Парсим данные чтобы проверить source
            try:
                data = json.loads(bytes(event.mimeData().data("application/x-space-window")).decode())
                source_space = data.get("source_space", 0)
                print(f"[DRAG] source_space={source_space}, target={self.space_num}")
                # Не принимаем drop на тот же Space
                if source_space != self.space_num:
                    event.acceptProposedAction()
                    self._is_drop_target = True
                    self._update_drop_style()
                    print("[DRAG] Accepted!")
                    return
            except Exception as e:
                print(f"[DRAG] Parse error: {e}")
        event.ignore()

    def dragLeaveEvent(self, event):
        """Убираем подсветку при выходе"""
        self._is_drop_target = False
        self.update_style()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        """Обрабатываем drop — перемещаем окно"""
        print(f"[DROP] dropEvent triggered on Space {self.space_num}")
        self._is_drop_target = False
        self.update_style()

        if not event.mimeData().hasFormat("application/x-space-window"):
            print("[DROP] No valid mime data")
            event.ignore()
            return

        try:
            data = json.loads(bytes(event.mimeData().data("application/x-space-window")).decode())
            app_name = data.get("app_name", "")
            window_title = data.get("window_title", "")
            source_space = data.get("source_space", 0)
            window_id = data.get("window_id", 0)  # Берём window_id напрямую из drag data!
            print(f"[DROP] Data: app={app_name}, title={window_title[:30]}, source={source_space}, wid={window_id}")

            if source_space == self.space_num:
                print("[DROP] Same space, ignoring")
                event.ignore()
                return

            # Если window_id не был в drag data - пробуем найти через кэш
            if not window_id:
                window_id = get_window_id_by_title(app_name, window_title)
            print(f"[DROP] Window ID: {window_id}")
            if window_id:
                print(f"Moving window {window_id} ({app_name}: {window_title}) from Space {source_space} to Space {self.space_num}")

                # Визуальный фидбек - мгновенно показываем успех
                self._show_success_flash()

                # Перемещаем окно
                success, message = move_window_to_space(window_id, self.space_num)

                if success:
                    event.acceptProposedAction()

                    # Мгновенно обновляем UI (без полного refresh)
                    main_window = self.window()
                    if main_window and hasattr(main_window, 'space_cards'):
                        # Убираем окно из source карточки
                        if source_space in main_window.space_cards:
                            source_card = main_window.space_cards[source_space]
                            self._remove_window_from_card(source_card, app_name, window_title)

                        # Добавляем в target карточку (эту)
                        self._add_window_to_card(app_name, window_title, window_id)

                    # НЕ делаем полный refresh - доверяем мгновенному обновлению
                    # Refresh будет при следующем открытии Space Manager
                    return
                else:
                    print(f"Move failed: {message}")

            event.ignore()

        except Exception as e:
            print(f"Drop error: {e}")
            event.ignore()

    def _update_drop_style(self):
        """Стиль при наведении drag"""
        if self._is_drop_target:
            self.setStyleSheet("""
                SpaceCard {
                    background-color: rgba(52, 199, 89, 0.6);
                    border: 2px dashed rgba(255, 255, 255, 0.5);
                    border-radius: 12px;
                }
                QLabel { color: #ffffff; background: transparent; }
            """)

    def _show_success_flash(self):
        """Мигнуть зелёным при успешном drop"""
        self.setStyleSheet("""
            SpaceCard {
                background-color: rgba(52, 199, 89, 0.8);
                border: 2px solid rgba(52, 199, 89, 1);
                border-radius: 12px;
            }
            QLabel { color: #ffffff; background: transparent; }
        """)
        QTimer.singleShot(300, self.update_style)

    def _remove_window_from_card(self, card, app_name: str, window_title: str):
        """Убрать окно из карточки (мгновенно, без rebuild)"""
        for i in range(card.apps_layout.count()):
            item = card.apps_layout.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                if hasattr(widget, 'app_name') and hasattr(widget, 'window_title'):
                    if widget.app_name == app_name and widget.window_title.startswith(window_title[:20]):
                        # Только скрываем — удаление при полном refresh
                        widget.setVisible(False)
                        widget.setEnabled(False)
                        break

    def _add_window_to_card(self, app_name: str, window_title: str, window_id: int):
        """Добавить окно в эту карточку (мгновенно)"""
        win_widget = WindowItemWidget(window_title, self.is_active, False, app_name, self.space_num, window_id)
        # Вставляем в начало
        self.apps_layout.insertWidget(0, win_widget)

    def set_apps(self, windows: list):
        """Установить список окон - каждое окно отдельной строкой с иконкой"""
        self.apps = windows
        self._all_windows = windows  # Сохраняем для QMenu

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

        max_visible = 5  # Показываем до 5 окон напрямую

        # Показываем первые окна
        for i, w in enumerate(windows[:max_visible]):
            app_name = w.get("app", "") if isinstance(w, dict) else ""
            title = w.get("title", "") if isinstance(w, dict) else str(w)
            minimized = w.get("minimized", False) if isinstance(w, dict) else False
            window_id = w.get("window_id", 0) if isinstance(w, dict) else 0
            if title:
                win_widget = WindowItemWidget(title, self.is_active, minimized, app_name, self.space_num, window_id)
                self.apps_layout.addWidget(win_widget)
                print(f"[WIDGET] Created WindowItemWidget: {app_name} - {title[:30]}, space={self.space_num}, wid={window_id}", flush=True)

        # Если окон больше 5 - добавить "Смотреть все"
        if len(windows) > max_visible:
            see_all_btn = QPushButton(f"Смотреть все ({len(windows)})...")
            see_all_btn.setFont(QFont(".AppleSystemUIFont", 9))
            see_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            see_all_btn.setFixedHeight(20)
            text_color = '#aaa' if self.is_active else '#888'
            see_all_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {text_color};
                    background: transparent;
                    border: none;
                    text-align: left;
                    padding-left: 4px;
                }}
                QPushButton:hover {{
                    color: #fff;
                }}
            """)
            see_all_btn.clicked.connect(lambda: self._show_all_windows_menu(see_all_btn))
            self.apps_layout.addWidget(see_all_btn)

    def _show_all_windows_menu(self, button):
        """Показать QMenu со всеми окнами — клик активирует окно"""
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
                color: #666;
            }
        """)

        for w in self._all_windows:
            app_name = w.get("app", "") if isinstance(w, dict) else ""
            title = w.get("title", "") if isinstance(w, dict) else str(w)
            minimized = w.get("minimized", False) if isinstance(w, dict) else False
            if title:
                display_title = title[:50] + "..." if len(title) > 50 else title
                prefix = "📥 " if minimized else ""
                action = QAction(f"{prefix}{app_name}: {display_title}", menu)
                if minimized:
                    action.setEnabled(False)
                else:
                    # Подключаем активацию окна при клике
                    action.triggered.connect(
                        lambda checked, a=app_name, t=title: self._activate_and_hide(a, t)
                    )
                menu.addAction(action)

        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _activate_and_hide(self, app_name: str, title: str):
        """Активировать окно и скрыть Space Manager"""
        activate_window(app_name, title)
        main_window = self.window()
        if main_window:
            QTimer.singleShot(300, main_window.hide)

    def mousePressEvent(self, event):
        if not self.exists:
            return  # Игнорируем клики на несуществующих

        # Проверяем, не кликнули ли на WindowItemWidget
        global_pos = event.globalPosition().toPoint()
        app = QApplication.instance()
        widget_at = app.widgetAt(global_pos)

        print(f"[SPACECARD] mousePressEvent on space {self.space_num}", flush=True)
        print(f"[SPACECARD] global_pos={global_pos.x()},{global_pos.y()}", flush=True)
        print(f"[SPACECARD] widget_at={widget_at.__class__.__name__ if widget_at else 'None'}", flush=True)

        if widget_at:
            # Проверяем всю иерархию от виджета до SpaceCard
            w = widget_at
            while w:
                print(f"[SPACECARD] checking parent: {w.__class__.__name__}", flush=True)
                if isinstance(w, WindowItemWidget):
                    print(f"[SPACECARD] Found WindowItemWidget! Ignoring this event.", flush=True)
                    # Игнорируем событие - дочерний виджет его обработает
                    event.ignore()
                    return
                if w == self:
                    break
                w = w.parent()

        if event.button() == Qt.MouseButton.LeftButton:
            # Single click - переключение (только если клик не на окне)
            print(f"[SPACECARD] Click on card body, switching to space {self.space_num}")
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
        self.name_edit.setPlaceholderText("Название...")
        self.name_edit.selectAll()
        layout.addWidget(self.name_edit)

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

        # Кнопка для свёрнутых окон
        self.minimized_btn = QPushButton("📥")
        self.minimized_btn.setToolTip("Свёрнутые окна")
        self.minimized_btn.clicked.connect(self.show_minimized_menu)
        self.minimized_btn.setStyleSheet(btn_style)
        controls.addWidget(self.minimized_btn)

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
                    card.name_label.setText("")  # Пустой для несуществующих

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
        print("[SHOW] show_and_raise called!", flush=True)
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
        """Обновить список окон используя данные AeroSpace"""
        # Обновляем кэш AeroSpace окон (если устарел)
        refresh_aerospace_cache()

        # Получаем текущий активный workspace из AeroSpace
        focused_ws = get_focused_workspace()
        old_active = self.config.get("active_space", 1)
        if focused_ws != old_active:
            print(f"[REFRESH] Active workspace changed: {old_active} -> {focused_ws}", flush=True)
            self.config["active_space"] = focused_ws
            # Обновляем визуальное состояние карточек
            if old_active in self.space_cards:
                self.space_cards[old_active].set_active(False)
            if focused_ws in self.space_cards:
                self.space_cards[focused_ws].set_active(True)

        # Получаем окна из AeroSpace кэша по workspace
        windows_by_ws = get_windows_by_workspace()
        print(f"[REFRESH] AeroSpace workspaces: {list(windows_by_ws.keys())}, active: {focused_ws}", flush=True)

        # Обновляем все SpaceCard с данными AeroSpace
        for space_num, card in self.space_cards.items():
            ws_key = str(space_num)
            windows = windows_by_ws.get(ws_key, [])
            card.set_apps(windows)

        # Сохраняем в конфиг
        self.config["space_windows"] = {str(k): v for k, v in windows_by_ws.items()}

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

            # Собрать только видимые окна (свёрнутые глобальные - не привязаны к Space)
            windows = get_windows_on_current_space(include_minimized=False)
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
        """Обновить UI с окнами (без свёрнутых - они отдельно)"""
        active = self.config.get("active_space", 1)

        # Сохранить окна для текущего Space (только видимые)
        if "space_windows" not in self.config:
            self.config["space_windows"] = {}
        if windows:
            self.config["space_windows"][str(active)] = windows[:10]
            self.save_config()

        # Обновить счётчик свёрнутых на кнопке
        minimized_count = len(self.config.get("minimized_windows", []))
        if minimized_count > 0:
            self.minimized_btn.setText(f"📥 {minimized_count}")
        else:
            self.minimized_btn.setText("📥")

        # Показать окна на всех карточках (БЕЗ свёрнутых)
        for num, card in self.space_cards.items():
            saved_windows = self.config.get("space_windows", {}).get(str(num), [])

            if num == active and windows:
                card.set_apps(windows)
            elif saved_windows:
                card.set_apps(saved_windows)
            else:
                card.set_apps([])

    def show_minimized_menu(self):
        """Показать меню со свёрнутыми окнами"""
        minimized = self.config.get("minimized_windows", [])

        if not minimized:
            # Пустое меню
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu {
                    background-color: rgba(40, 40, 42, 0.95);
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 8px;
                    padding: 8px;
                }
                QMenu::item {
                    color: #888;
                    padding: 8px 16px;
                    font-size: 12px;
                }
            """)
            action = QAction("Нет свёрнутых окон", menu)
            action.setEnabled(False)
            menu.addAction(action)
            menu.exec(self.minimized_btn.mapToGlobal(self.minimized_btn.rect().topLeft()))
            return

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
                padding: 6px 16px 6px 10px;
                border-radius: 4px;
                font-size: 12px;
            }
            QMenu::item:selected {
                background-color: rgba(10, 132, 255, 0.8);
            }
        """)

        for w in minimized:
            app_name = w.get("app", "")
            title = w.get("title", "")
            if title:
                display_title = title[:45] + "..." if len(title) > 45 else title
                action = QAction(f"📥 {app_name}: {display_title}", menu)
                # При клике разворачиваем окно
                action.triggered.connect(
                    lambda checked, a=app_name, t=title: self._unminimize_window(a, t)
                )
                menu.addAction(action)

        menu.exec(self.minimized_btn.mapToGlobal(self.minimized_btn.rect().topLeft()))

    def _unminimize_window(self, app_name: str, title: str):
        """Развернуть свёрнутое окно"""
        # Активируем приложение - это автоматически развернёт окно
        activate_window(app_name, title)
        QTimer.singleShot(500, self.refresh_apps)  # Обновить список
        QTimer.singleShot(300, self.hide)

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

        # Скрыть окно через 2 секунды
        QTimer.singleShot(2000, self.hide)

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


class DebugEventFilter(QObject):
    """Отладочный фильтр для отслеживания кликов"""
    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        # Логируем все типы событий мыши
        if event.type() == QEvent.Type.MouseButtonPress:
            print(f"[DEBUG-CLICK] {obj.__class__.__name__} at {event.pos().x()},{event.pos().y()}", flush=True)
        elif event.type() == QEvent.Type.MouseMove:
            pass  # Слишком много событий
        elif event.type() == QEvent.Type.Enter:
            print(f"[DEBUG-ENTER] {obj.__class__.__name__}", flush=True)
        return False  # Не перехватываем, просто логируем


def precache_aerospace_windows():
    """Предварительное кэширование окон AeroSpace ДО запуска Qt"""
    global _aerospace_windows_cache, _aerospace_cache_time
    import time

    print("[PRE-CACHE] Loading aerospace windows before Qt...", flush=True)
    try:
        # Кэшируем список окон
        result = subprocess.run(
            ['/opt/homebrew/bin/aerospace', 'list-windows', '--all',
             '--format', '%{window-id}|%{app-name}|%{window-title}|%{workspace}'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout:
            output = result.stdout
            print(f"[PRE-CACHE] Got {len(output)} bytes", flush=True)
            _parse_aerospace_output(output)
            print(f"[PRE-CACHE] Loaded {len(_aerospace_windows_cache)} windows", flush=True)

        # Кэшируем текущий активный workspace
        update_focused_workspace_sync()
    except Exception as e:
        print(f"[PRE-CACHE] Error: {e}", flush=True)


def main():
    # Кэшируем окна AeroSpace ДО создания Qt приложения
    precache_aerospace_windows()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Space Manager")

    # Глобальный фильтр для отладки
    debug_filter = DebugEventFilter()
    app.installEventFilter(debug_filter)

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
            # Обновляем focused workspace из отдельного потока (до показа окна)
            update_focused_workspace_sync()
            hotkey_signal.toggle.emit()

    def on_release(key):
        current_keys.discard(key)

    # Запуск listener в отдельном потоке
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()

    print("Space Manager запущен!", flush=True)
    print("Hotkey: Ctrl+`", flush=True)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
