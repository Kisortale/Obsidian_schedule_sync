"""Командная строка: python run.py <команда>."""
from __future__ import annotations

import argparse
import sys
import time

from . import fetcher, storage
from .parser import list_groups
from .service import SyncService


def build_parser() -> argparse.ArgumentParser:      # Собирает парсер аргументов: одна подкоманда = одно действие пользователя
    ap = argparse.ArgumentParser(prog="run.py", description="Расписание из Google Таблицы в Obsidian")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="открыть окно (по умолчанию)")

    g = sub.add_parser("groups", help="показать группы из таблицы")
    g.add_argument("--url", default=storage.DEFAULT_URL)

    a = sub.add_parser("add", help="добавить или обновить профиль")
    a.add_argument("--name", required=True)
    a.add_argument("--group", required=True)
    a.add_argument("--vault", required=True, help="папка хранилища Obsidian")
    a.add_argument("--url", default=storage.DEFAULT_URL)
    a.add_argument("--folder", default="Расписание")
    a.add_argument("--no-notify", action="store_true")

    sub.add_parser("sync", help="один раз обновить все профили")

    d = sub.add_parser("daemon", help="обновлять по интервалу (без окна)")
    d.add_argument("--interval", type=int, help="минут (по умолчанию из настроек)")
    return ap


def main(argv: list[str] | None = None) -> int:     # Точка входа. Возвращает код выхода процесса (0 — успех)
    args = build_parser().parse_args(argv)
    cmd = args.cmd or "gui"     # без аргументов открываем окно

    if cmd == "gui":         # Импорт внутри ветки: GUI-зависимости не грузятся в консольных режимах
        from .gui import main as gui_main
        gui_main()
        return 0

    if cmd == "groups":      # Разовый запрос к таблице - печатает список групп
        for name in list_groups(fetcher.fetch_rows(args.url)):
            print(name)
        return 0

    settings = storage.load_settings()

    if cmd == "add":       # Создаём новый профиль или обновляем существующий по имени
        p = settings.get(args.name)
        if p is None:
            p = storage.Profile(name=args.name)
            settings.profiles.append(p)
        p.group, p.vault_path, p.sheet_url = args.group, args.vault, args.url
        p.folder, p.notify = args.folder, not args.no_notify
        storage.save_settings(settings)
        print(f"Профиль «{p.name}» сохранён.")
        return 0

    if not settings.profiles:       # Код, если хотя бы один профиль упал
        print("Нет профилей. Добавьте: python run.py add --name Я --group 26КНТ-1 --vault ПУТЬ")
        return 1

    svc = SyncService(settings)
    if cmd == "sync":
        results = svc.sync_all()
        return 0 if results and all(r.ok for r in results) else 2

    if cmd == "daemon":     # Фоновый режим, код ждет только Ctrl+C
        if args.interval:
            settings.interval_minutes = args.interval
        svc.start()
        print(f"Автообновление каждые {settings.interval_minutes} мин. Ctrl+C — выход.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            svc.stop()
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
