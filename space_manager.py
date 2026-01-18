#!/usr/bin/env python3
"""
Space Manager - Аналог Space Capsule для macOS
Grid view для рабочих столов с названиями и быстрым переключением.

Автор: Клэр для Ларри
"""

import sys
import json
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QPushButton,
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSystemTrayIcon,
    QMenu, QDialog, QSpinBox, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut, QFont, QAction

CONFIG_PATH = Path.home() / "Клэр" / "apps" / "space-manager" / "config.json"

class SpaceButton(QPushButton):
    """Кнопка для одного Space"""

    def __init__(self, space_num: int, name: str = "", is_active: bool = False):
        super().__init__()
        self.space_num = space_num
        self.space_name = name or f"Space {space_num}"
        self.is_active = is_active
        self.update_style()

    def update_style(self):
        name_display = self.space_name if self.space_name else f"Space {self.space_num}"
        self.setText(f"{self.space_num}\n{name_display}")

        if self.is_active:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #007AFF;
                    color: white;
                    border: 2px solid #005CBB;
                    border-radius: 12px;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 15px;
                    min-width: 120px;
                    min-height: 80px;
                }
                QPushButton:hover {
                    background-color: #0056CC;
                }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #2D2D2D;
                    color: #CCCCCC;
                    border: 2px solid #444444;
                    border-radius: 12px;
                    font-size: 14px;
                    padding: 15px;
                    min-width: 120px;
                    min-height: 80px;
                }
                QPushButton:hover {
                    background-color: #3D3D3D;
                    border-color: #007AFF;
                }
            """)

    def set_active(self, active: bool):
        self.is_active = active
        self.update_style()

    def set_name(self, name: str):
        self.space_name = name
        self.update_style()


class SettingsDialog(QDialog):
    """Диалог настроек"""

    def __init__(self, parent, rows: int, cols: int, total_spaces: int):
        super().__init__(parent)
        self.setWindowTitle("Настройки Space Manager")
        self.setModal(True)

        layout = QVBoxLayout(self)

        # Grid размер
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
        layout.addLayout(grid_layout)

        # Всего Spaces
        spaces_layout = QHBoxLayout()
        spaces_layout.addWidget(QLabel("Всего Spaces:"))
        self.spaces_spin = QSpinBox()
        self.spaces_spin.setRange(1, 16)
        self.spaces_spin.setValue(total_spaces)
        spaces_layout.addWidget(self.spaces_spin)
        layout.addLayout(spaces_layout)

        # Подсказка
        hint = QLabel("Совет: Создай нужное количество рабочих столов\nв Mission Control перед использованием.")
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

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"Название для Space {space_num}:"))

        self.name_edit = QLineEdit()
        self.name_edit.setText(current_name)
        self.name_edit.setPlaceholderText("Например: API проект, Frontend, Research...")
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
    """Главное окно Space Manager"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Space Manager")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)

        # Конфигурация по умолчанию
        self.config = {
            "rows": 2,
            "cols": 3,
            "total_spaces": 6,
            "space_names": {},
            "active_space": 1
        }
        self.load_config()

        self.space_buttons = {}
        self.init_ui()
        self.setup_shortcuts()
        self.setup_tray()

        # Таймер для проверки активного Space
        self.check_timer = QTimer()
        self.check_timer.timeout.connect(self.check_active_space)
        self.check_timer.start(1000)  # каждую секунду

    def load_config(self):
        """Загрузить конфигурацию"""
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, 'r') as f:
                    saved = json.load(f)
                    self.config.update(saved)
            except:
                pass

    def save_config(self):
        """Сохранить конфигурацию"""
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    def init_ui(self):
        """Инициализация UI"""
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # Заголовок
        title = QLabel("Space Manager")
        title.setFont(QFont("SF Pro Display", 18, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: white; margin-bottom: 10px;")
        main_layout.addWidget(title)

        # Подсказка
        hint = QLabel("Клик = переключить  |  Двойной клик = переименовать  |  ⌘Q = закрыть")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #888888; font-size: 11px; margin-bottom: 15px;")
        main_layout.addWidget(hint)

        # Grid с кнопками Spaces
        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(10)
        main_layout.addWidget(self.grid_widget)

        self.rebuild_grid()

        # Кнопки управления
        controls = QHBoxLayout()

        settings_btn = QPushButton("⚙️ Настройки")
        settings_btn.clicked.connect(self.show_settings)
        settings_btn.setStyleSheet("""
            QPushButton {
                background-color: #444444;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #555555;
            }
        """)
        controls.addWidget(settings_btn)

        controls.addStretch()

        hide_btn = QPushButton("Скрыть")
        hide_btn.clicked.connect(self.hide)
        hide_btn.setStyleSheet("""
            QPushButton {
                background-color: #444444;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #555555;
            }
        """)
        controls.addWidget(hide_btn)

        main_layout.addLayout(controls)

        # Стиль окна
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1E1E1E;
            }
            QWidget {
                background-color: #1E1E1E;
            }
        """)

        self.adjustSize()
        self.center_on_screen()

    def rebuild_grid(self):
        """Перестроить grid кнопок"""
        # Удалить старые кнопки
        for btn in self.space_buttons.values():
            btn.deleteLater()
        self.space_buttons.clear()

        # Создать новые
        rows = self.config["rows"]
        cols = self.config["cols"]
        total = self.config["total_spaces"]
        active = self.config["active_space"]
        names = self.config["space_names"]

        space_num = 1
        for row in range(rows):
            for col in range(cols):
                if space_num > total:
                    break

                name = names.get(str(space_num), "")
                is_active = (space_num == active)

                btn = SpaceButton(space_num, name, is_active)
                btn.clicked.connect(lambda checked, n=space_num: self.switch_to_space(n))
                btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, n=space_num: self.show_context_menu(n, pos)
                )
                # Двойной клик для переименования
                btn.mouseDoubleClickEvent = lambda event, n=space_num: self.rename_space(n)

                self.grid_layout.addWidget(btn, row, col)
                self.space_buttons[space_num] = btn
                space_num += 1

        self.adjustSize()

    def center_on_screen(self):
        """Центрировать окно на экране"""
        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width() - self.width()) // 2,
            (screen.height() - self.height()) // 2
        )

    def setup_shortcuts(self):
        """Настроить горячие клавиши"""
        # Cmd+Q для выхода
        quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        quit_shortcut.activated.connect(QApplication.quit)

        # Escape для скрытия
        esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        esc_shortcut.activated.connect(self.hide)

        # Ctrl+1-9 для переключения
        for i in range(1, 10):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            shortcut.activated.connect(lambda n=i: self.switch_to_space(n))

    def setup_tray(self):
        """Настроить иконку в трее"""
        self.tray_icon = QSystemTrayIcon(self)
        # Используем эмодзи как текстовую иконку (в реальном приложении нужна картинка)
        self.tray_icon.setToolTip("Space Manager")

        # Меню трея
        tray_menu = QMenu()

        show_action = QAction("Показать", self)
        show_action.triggered.connect(self.show_and_raise)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        quit_action = QAction("Выход", self)
        quit_action.triggered.connect(QApplication.quit)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.tray_activated)
        self.tray_icon.show()

    def tray_activated(self, reason):
        """Клик по иконке в трее"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_and_raise()

    def show_and_raise(self):
        """Показать и поднять окно"""
        self.show()
        self.raise_()
        self.activateWindow()

    def switch_to_space(self, space_num: int):
        """Переключиться на Space"""
        if space_num > self.config["total_spaces"]:
            return

        # AppleScript для переключения
        script = f'''
        tell application "System Events"
            key code {17 + space_num} using control down
        end tell
        '''
        # key code: 18=1, 19=2, 20=3, 21=4, 22=5, 23=6, 24=7, 25=8, 26=9
        # Но для Ctrl+1 нужны другие коды...

        # Альтернативный способ - через keystroke
        script = f'''
        tell application "System Events"
            keystroke "{space_num}" using control down
        end tell
        '''

        try:
            subprocess.run(["osascript", "-e", script], check=True)
            self.config["active_space"] = space_num
            self.update_active_button(space_num)
            self.save_config()
        except subprocess.CalledProcessError as e:
            QMessageBox.warning(self, "Ошибка",
                f"Не удалось переключиться на Space {space_num}.\n"
                "Убедитесь, что включены горячие клавиши в System Settings → Keyboard → Shortcuts → Mission Control")

    def update_active_button(self, active_num: int):
        """Обновить выделение активного Space"""
        for num, btn in self.space_buttons.items():
            btn.set_active(num == active_num)

    def check_active_space(self):
        """Проверить текущий активный Space (заглушка - требует SIP отключения для реального определения)"""
        # К сожалению, macOS не дает простого API для определения текущего Space
        # Это требует приватных API или отключения SIP
        pass

    def rename_space(self, space_num: int):
        """Переименовать Space"""
        current_name = self.config["space_names"].get(str(space_num), "")

        dialog = RenameDialog(self, space_num, current_name)
        if dialog.exec():
            new_name = dialog.name_edit.text().strip()
            self.config["space_names"][str(space_num)] = new_name
            self.space_buttons[space_num].set_name(new_name)
            self.save_config()

    def show_context_menu(self, space_num: int, pos):
        """Показать контекстное меню для Space"""
        menu = QMenu(self)

        rename_action = QAction(f"Переименовать Space {space_num}", self)
        rename_action.triggered.connect(lambda: self.rename_space(space_num))
        menu.addAction(rename_action)

        menu.exec(self.space_buttons[space_num].mapToGlobal(pos))

    def show_settings(self):
        """Показать настройки"""
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

    def closeEvent(self, event):
        """Перехват закрытия - скрыть вместо закрытия"""
        event.ignore()
        self.hide()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # Не закрывать при скрытии окна

    # Проверка PyQt6
    window = SpaceManager()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
