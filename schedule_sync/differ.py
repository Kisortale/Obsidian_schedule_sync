"""Сравнение двух версий расписания."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional

from .models import Lesson, WEEKDAYS_RU

COMPARE_FIELDS = {
    "start": "начало", "end": "конец", "room": "аудитория", "building": "корпус",
    "teacher": "преподаватель", "cancelled": "отмена", "online": "online",
}


@dataclass
class Changes:                                                              #Создает объект - результат сравнения расписаний
    added: list[Lesson] = field(default_factory=list)
    removed: list[Lesson] = field(default_factory=list)
    changed: list[tuple[Lesson, Lesson, list[str]]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def diff(old: Iterable[Lesson], new: Iterable[Lesson], since: Optional[date] = None) -> Changes:            #сравнивает расписания(ищет изменения именно с since, до не смотрит)
    since_iso = (since or date.today()).isoformat()
    o = {l.key: l for l in old if l.date >= since_iso}
    n = {l.key: l for l in new if l.date >= since_iso}
    ch = Changes()
    for k, l in n.items():
        if k not in o:
            ch.added.append(l)
        else:
            fields = [label for f, label in COMPARE_FIELDS.items() if getattr(o[k], f) != getattr(l, f)]
            if fields:
                ch.changed.append((o[k], l, fields))
    for k, l in o.items():
        if k not in n:
            ch.removed.append(l)
    for lst in (ch.added, ch.removed):
        lst.sort(key=lambda l: (l.date, l.start))
    ch.changed.sort(key=lambda t: (t[1].date, t[1].start))
    return ch


def _when(l: Lesson) -> str:                                                        #Преобразует дату занятия в красивый текст
    d = datetime.strptime(l.date, "%Y-%m-%d")
    return f"{WEEKDAYS_RU[d.weekday()]} {d:%d.%m} {l.start}"


def _place(l: Lesson) -> str:                                               #Место проведения занятия
    if l.online:
        return "online"
    return ", ".join(x for x in (f"ауд. {l.room}" if l.room else "", l.building) if x)


def describe(l: Lesson) -> str:                                     #Создает полное описание 1 занятия
    kind = f" ({l.kind})" if l.kind else ""
    place = _place(l)
    return f"{_when(l)} {l.subject}{kind}" + (f", {place}" if place else "")


def format_changes(ch: Changes) -> list[str]:                               #Берет объект cahnges и преобраезует его в список строк с изменениями
    lines: list[str] = []
    for l in ch.added:
        lines.append(f"➕ Добавлено: {describe(l)}")
    for l in ch.removed:
        lines.append(f"➖ Убрано: {describe(l)}")
    for old, new, fields in ch.changed:
        if new.cancelled and not old.cancelled:
            lines.append(f"❌ Отмена: {describe(old)}")
            continue
        parts = []
        for f, label in COMPARE_FIELDS.items():
            if label in fields and f not in ("cancelled",):
                a, b = getattr(old, f), getattr(new, f)
                parts.append(f"{label}: {a or '—'} → {b or '—'}")
        if "отмена" in fields and old.cancelled and not new.cancelled:
            parts.append("отмена снята")
        kind = f" ({new.kind})" if new.kind else ""
        lines.append(f"✏️ {_when(new)} {new.subject}{kind}: " + "; ".join(parts))
    return lines
