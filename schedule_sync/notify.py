"""Системные уведомления. Пробуем plyer, затем штатные средства ОС."""
from __future__ import annotations

import shutil
import subprocess
import sys

APP_NAME = "Расписание"


def send(title: str, message: str) -> bool:     # Показать системное уведомление. Никогда не бросает исключений
    message = message if len(message) <= 250 else message[:247] + "…"       # Обрезаем длинный текст
    try:        # Попытка №1: plyer (единый API для Win/macOS/Linux)
        from plyer import notification  # type: ignore

        notification.notify(title=title, message=message, app_name=APP_NAME, timeout=10)
        return True
    except Exception:
        pass
    try:        # Попытка №2: нативные утилиты ОС
        if sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], check=False, timeout=10)
            return True
        if sys.platform == "darwin" and shutil.which("osascript"):      # Экранируем кавычки/бэкслеши — иначе AppleScript-строка сломается
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                ["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'],
                check=False, timeout=10,
            )
            return True
    except Exception:
        pass
    return False
