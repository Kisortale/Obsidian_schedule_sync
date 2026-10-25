"""Запись расписания в хранилище Obsidian (обычные .md-файлы).

Генерируемая часть заметки обрамлена маркерами. Всё, что пользователь пишет
вне маркеров, при обновлении сохраняется.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from .models import Lesson, WEEKDAYS_RU

START = "<!-- schedule-sync:start -->"                  #Маркеры. Нужны для генерации заметки
END = "<!-- schedule-sync:end -->"
CHANGELOG = "Изменения.md"
INDEX = "Расписание.md"


def _fmt_date(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{WEEKDAYS_RU[d.weekday()]}, {d:%d.%m.%Y}"


def render_lesson(l: Lesson) -> str:                                        #Преобразует один объект Lesson в одну строку Markdown.
    title = f"**{l.subject}**" + (f" _({l.kind})_" if l.kind else "")
    parts = [f"**{l.start}–{l.end}**", title]
    if l.online:
        parts.append("online")
    else:
        if l.room:
            parts.append(f"ауд. {l.room}")
        if l.building:
            b = l.building + (f" ({l.building_name})" if l.building_name else "")
            parts.append(f"корпус {b}")
    if l.teacher:
        parts.append(l.teacher)
    if l.course_code:
        parts.append(f"`{l.course_code}`")
    line = " · ".join(parts)
    if l.cancelled:
        return f"- ~~{line}~~ ❌ отмена занятия"
    if l.note:
        line += f" — ⚠️ {l.note}"
    return f"- {line}"


def render_block(iso: str, lessons: list[Lesson]) -> str:                                               #Создаёт всю автоматически генерируемую часть заметки за один день
    body = "\n".join(render_lesson(l) for l in lessons) if lessons else "_Занятий нет._"
    return f"{START}\n# {_fmt_date(iso)}\n\n{body}\n{END}"


def _frontmatter(iso: str, group: str) -> str:                                                  #Создаёт YAML-заголовок Obsidian.
    return f"---\ntype: schedule\ndate: {iso}\ngroup: {group}\n---\n\n"


def _write_if_changed(path: Path, text: str) -> bool:                                                   #Записывает файл только если содержимое действительно изменилось.
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def _merge_block(existing: str, block: str) -> str:                             #Pаменяет только участок между маркерами старт и энд
    a, b = existing.find(START), existing.find(END)
    if a != -1 and b != -1 and b > a:
        return existing[:a] + block + existing[b + len(END):]
    sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + sep + block + "\n"


def write_days(                                                     #Создает маркдаун файлы по дням
    vault: str | Path,
    folder: str,
    group: str,
    lessons: Iterable[Lesson],
    stale_dates: Iterable[str] = (),
) -> list[str]:
    root = Path(vault) / folder
    by_date: dict[str, list[Lesson]] = {}
    for l in lessons:
        by_date.setdefault(l.date, []).append(l)

    changed: list[str] = []
    for iso, items in sorted(by_date.items()):
        items.sort(key=lambda l: (l.start, l.subject))
        path = root / f"{iso}.md"
        block = render_block(iso, items)
        if path.exists():
            text = _merge_block(path.read_text(encoding="utf-8"), block)
        else:
            text = _frontmatter(iso, group) + block + "\n"
        if _write_if_changed(path, text):
            changed.append(path.name)

                                                                                   # даты, которые раньше были в расписании, а теперь пропали
    for iso in stale_dates:
        if iso in by_date:
            continue
        path = root / f"{iso}.md"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if START in text and _write_if_changed(path, _merge_block(text, render_block(iso, []))):
                changed.append(path.name)
    return changed


def write_index(vault: str | Path, folder: str, group: str, today: date, days: int = 7) -> None:                                #Создает главную страницу Расписание.md
    root = Path(vault) / folder
    lines = [
        START,
        f"# Расписание {group}",
        "",
        f"_Обновлено: {datetime.now():%d.%m.%Y %H:%M}_ · [[{CHANGELOG[:-3]}|Журнал изменений]]",
        "",
    ]
    for i in range(days):
        d = (today + timedelta(days=i)).isoformat()
        if (root / f"{d}.md").exists():
            lines += [f"![[{d}]]", ""]
    lines.append(END)
    block = "\n".join(lines)
    path = root / INDEX
    text = _merge_block(path.read_text(encoding="utf-8"), block) if path.exists() else block + "\n"
    _write_if_changed(path, text)


def append_changelog(vault: str | Path, folder: str, lines: list[str]) -> None:                                                 #Добавляет запись в журнал изменений. 
    path = Path(vault) / folder / CHANGELOG
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "" if path.exists() else "# Журнал изменений расписания\n"
    entry = f"\n## {datetime.now():%Y-%m-%d %H:%M}\n\n" + "\n".join(f"- {l}" for l in lines) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(head + entry)
