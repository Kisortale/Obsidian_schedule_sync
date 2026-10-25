"""Синхронизация профилей и фоновый планировщик."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

from . import differ, fetcher, notify, obsidian, storage
from .models import Lesson
from .parser import parse_schedule
from .storage import Profile, Settings

Log = Callable[[str], None]


@dataclass
class SyncResult:                               #Хранит результат одной синхронизации
    profile: str
    ok: bool
    lessons: int = 0
    files_changed: int = 0
    changes: Optional[differ.Changes] = None
    message: str = ""


class SyncService:                                                                              #По сути синхронизирует всё что есть в программе
    def __init__(self, settings: Settings, log: Log = print,
                 notifier: Callable[[str, str], object] = notify.send):
        self.settings = settings
        self.log = log
        self.notifier = notifier
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.next_run: Optional[datetime] = None

    
    def sync_profile(self, p: Profile, rows_cache: Optional[dict] = None,                                                   #Полностью синхронизирует один профиль.
                     today: Optional[date] = None) -> SyncResult:
        today = today or date.today()
        try:
            if not p.group:
                return SyncResult(p.name, False, message="не выбрана группа")
            if not p.vault_path:
                return SyncResult(p.name, False, message="не указана папка хранилища Obsidian")
            cache = rows_cache if rows_cache is not None else {}
            if p.sheet_url not in cache:
                cache[p.sheet_url] = fetcher.fetch_rows(p.sheet_url)
            res = parse_schedule(cache[p.sheet_url], p.group, today=today)
            for w in res.warnings[:20]:
                self.log(f"[{p.name}] ⚠ {w}")

            old = storage.load_snapshot(p)
            old_lessons = [Lesson.from_dict(d) for d in old["lessons"]] if old and old.get("group") == p.group else None
            stale = {l.date for l in old_lessons} if old_lessons else set()

            changed = obsidian.write_days(p.vault_path, p.folder, p.group, res.lessons, stale)
            obsidian.write_index(p.vault_path, p.folder, p.group, today)

            changes = None
            if old_lessons is not None:
                changes = differ.diff(old_lessons, res.lessons, since=today)
                if changes:
                    lines = differ.format_changes(changes)
                    obsidian.append_changelog(p.vault_path, p.folder, lines)
                    self.log(f"[{p.name}] Изменения в расписании ({len(lines)}):")
                    for ln in lines:
                        self.log("    " + ln)
                    if p.notify:
                        head = f"Расписание {p.group}: изменений {len(lines)}"
                        self.notifier(head, "\n".join(lines[:4]) + ("\n…" if len(lines) > 4 else ""))

            storage.save_snapshot(p, [l.to_dict() for l in res.lessons], datetime.now().isoformat(timespec="seconds"))
            msg = f"занятий: {len(res.lessons)}, файлов обновлено: {len(changed)}"
            if old_lessons is None:
                msg += " (первая синхронизация)"
            return SyncResult(p.name, True, len(res.lessons), len(changed), changes, msg)
        except Exception as e:  # сеть, формат таблицы, права на папку и т.п.
            return SyncResult(p.name, False, message=f"{type(e).__name__}: {e}")

    
    def sync_all(self) -> list[SyncResult]:                                 #Синхронизирует все включённые профили.
        with self._lock:
            cache: dict = {}
            results = []
            for p in self.settings.profiles:
                if not p.enabled:
                    continue
                r = self.sync_profile(p, cache)
                self.log(f"[{r.profile}] {'✔' if r.ok else '✖'} {r.message}")
                results.append(r)
            return results

    
    @property
    def running(self) -> bool:                                                                      #Показывает работает ли сейчас фоновая синхронизация
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:                                        #Запускает автоматическую синхронизацию
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="schedule-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:                                                     #Останавливает автоматическую синхронизацию
        self._stop.set()
        self.next_run = None

    def _loop(self) -> None:                                                        #Это внутренний цикл автоматической синхронизации (с помощью него программа сама обновляет расписание каждые n минут)
        while not self._stop.is_set():
            self.sync_all()
            wait = max(1, int(self.settings.interval_minutes)) * 60
            self.next_run = datetime.fromtimestamp(datetime.now().timestamp() + wait)
            if self._stop.wait(wait):
                break
        self.next_run = None
