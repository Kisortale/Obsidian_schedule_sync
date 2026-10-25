"""Разбор «нестандартного» расписания из Google Таблицы.

Структура таблицы (см. README):
  * строка с названиями групп (26КНТ-1 ...), у каждой группы три колонки:
    занятие | Ауд. | Корпус, затем пустая колонка-разделитель;
  * слева колонки «День» (заполнена только в первой строке дня) и «Время»;
  * в одной ячейке может быть несколько занятий, перед каждым могут стоять даты:
    «07.09., 21.09.», «07.09.-19.10.», «с 09.09.», «06.10. в 09:30-10:10»,
    «11.09. - отмена занятия»;
  * аудитории и корпуса в соседних колонках идут в том же порядке через пробелы.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterator, Optional

from .models import Lesson
# шаблоны поиска группы, времени занятия и т. д.
GROUP_RE = re.compile(r"^\d{2}[А-ЯЁA-Z]+-\d+[А-Яа-яA-Za-z]*$")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})")
PERIOD_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.?\s*[-–—]\s*(\d{1,2})\.(\d{1,2})\.?")
DAYS = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}

_D = r"\d{1,2}\.\d{1,2}"
_ITEM = rf"(?:с\s+)?{_D}\.?(?:\s*[-–—]\s*{_D}\.?)?"
PREFIX_RE = re.compile(rf"^{_ITEM}(?:\s*[,.]\s*{_ITEM})*")
DATE_TOKEN_RE = re.compile(rf"(с\s+)?(\d{{1,2}})\.(\d{{1,2}})\.?(?:\s*[-–—]\s*(\d{{1,2}})\.(\d{{1,2}})\.?)?")
TIMEOVR_RE = re.compile(r"^в\s+(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})")
CANCEL_RE = re.compile(r"^[-–—]?\s*отмен\w*(?:\s+занятия)?", re.I)
TEACHER_RE = re.compile(
    r"(?:[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.|Вакансия)"
)
COURSE_RE = re.compile(r"^\d{2}_\d{2}_\S+$")
KINDS = {
    "лекция", "семинар", "практическое занятие", "лабораторная работа",
    "консультация", "экзамен", "зачет", "зачёт",
}
DEFAULT_BUILDINGS = {
    "БП": "ул. Б.Печёрской, 25/12",
    "К": "ул. Костина, 2Б",
    "Л": "ул. Львовской, 1В",
    "Р": "ул. Родионова, 136",
}


# ----------------------------------------------------------------- helpers
def normalize(s: str) -> str: # очистка таблиц от лишних пробелов (оставляет только один пробел)
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def _hm(h: str, m: str) -> str: # приводит время к формату hh:mm
    return f"{int(h):02d}:{m}"


def _cell(row: list[str], i: int) -> str: # возврат пустой строки в случае отсутствия i-колонки вместо ошибки
    return row[i] if 0 <= i < len(row) else ""


@dataclass # определяет структуру данных для хранения границ учебного модуля
class Period:
    start: date
    end: date

    def make_date(self, d: int, m: int) -> date: # превращение простого числа дня и месяца в полноценную дату с годом
        year = self.start.year if (m, d) >= (self.start.month, self.start.day) else self.start.year + 1
        return date(year, m, d)


def _pick_period(d1: int, m1: int, d2: int, m2: int, today: date, year: Optional[int]) -> Optional[Period]: # решает задачу автоматического определения актуального учебного модуля (ыбирает тот интервал дат, который находится ближе всего к сегодняшнему дню)
    best = None
    years = [year] if year else [today.year - 1, today.year, today.year + 1]
    for y in years:
        try:
            s = date(y, m1, d1)
            e = date(y if (m2, d2) >= (m1, d1) else y + 1, m2, d2)
        except ValueError:
            continue
        dist = 0 if s <= today <= e else min(abs((s - today).days), abs((e - today).days))
        if best is None or dist < best[0]:
            best = (dist, Period(s, e))
    return best[1] if best else None


def _weekly(a: date, b: date, weekday: int) -> Iterator[date]: # Генератор, который выдает все даты конкретного дня недели внутри заданного диапазона
    d = a + timedelta(days=(weekday - a.weekday()) % 7)
    while d <= b:
        yield d
        d += timedelta(days=7)


# --------------------------------------------------------------- structure
@dataclass
class Structure: # класс для хранения служебной информации о таблице расписания
    header_row: int
    groups: dict[str, int]
    day_col: int
    time_col: int
    buildings: dict[str, str]
    periods: list[tuple[int, Period]]


def analyze(rows: list[list[str]], today: Optional[date] = None, year: Optional[int] = None) -> Structure: # сканирует текст, прикладывая к нему шаблоны поиска (регулярные выражениея), чтобы динамически определить, где в таблице находятся нужные данные
    today = today or date.today()
    header = None
    for i, row in enumerate(rows):
        if any(GROUP_RE.match(normalize(c)) for c in row):
            header = i
            break
    if header is None:
        raise ValueError("В таблице не найдена строка с названиями групп (например, 26КНТ-1).")

    groups: dict[str, int] = {}
    for ci, c in enumerate(rows[header]):
        name = normalize(c)
        if GROUP_RE.match(name) and name not in groups:
            groups[name] = ci

    day_col, time_col = 1, 2
    for row in rows[: header + 1]:
        for ci, c in enumerate(row):
            low = normalize(c).lower()
            if low == "день":
                day_col = ci
            elif low == "время":
                time_col = ci

    buildings: dict[str, str] = {}
    periods: list[tuple[int, Period]] = []
    for row in rows[:header]:
        for ci, c in enumerate(row):
            t = normalize(c)
            if t.lower().startswith("корпус") and ci + 1 < len(row):
                code = normalize(row[ci + 1])
                if code and len(code) <= 4:
                    buildings.setdefault(code, re.sub(r"^корпус\s+(на\s+)?", "", t, flags=re.I))
            pm = PERIOD_RE.search(t)
            if pm and ("модул" in t.lower() or "семестр" in t.lower() or "курс" in t.lower()):
                p = _pick_period(int(pm[1]), int(pm[2]), int(pm[3]), int(pm[4]), today, year)
                if p:
                    periods.append((ci, p))
    for code, name in DEFAULT_BUILDINGS.items():
        buildings.setdefault(code, name)
    return Structure(header, groups, day_col, time_col, buildings, periods)


def list_groups(rows: list[list[str]]) -> list[str]: # достает из таблицы только список названий всех доступных групп
    return list(analyze(rows).groups)


# ------------------------------------------------------------- cell parser
@dataclass
class Entry: # создание записи для хранения информации об одной паре
    prefix: Optional[str] = None
    time: Optional[tuple[str, str]] = None
    subject: str = ""
    kind: str = ""
    course_code: str = ""
    teacher: str = ""
    cancel_prefixes: list[str] = field(default_factory=list)
    time_notes: list[tuple[str, tuple[str, str]]] = field(default_factory=list)


def parse_entries(text: str, warnings: Optional[list[str]] = None) -> list[Entry]: # разбирает сложную текстовую строку ячейки расписания на отдельные логические заготовки занятий
    warnings = warnings if warnings is not None else []
    t = normalize(text)
    if not t:
        return []


    chunks, pos = [], 0
    for m in TEACHER_RE.finditer(t):
        chunks.append(t[pos:m.end()])
        pos = m.end()
    tail = t[pos:].strip()
    if tail:
        chunks.append(tail)

    entries: list[Entry] = []
    for chunk in chunks:
        rest = chunk.strip()
        own_prefix: Optional[str] = None
        own_time: Optional[tuple[str, str]] = None
        while rest:
            m = PREFIX_RE.match(rest)
            if not m:
                break
            prefix = m.group(0)
            after = rest[m.end():].lstrip()

            c = CANCEL_RE.match(after)
            if c:
                if entries:
                    entries[-1].cancel_prefixes.append(prefix)
                else:
                    warnings.append(f"Отмена без занятия: {chunk!r}")
                rest = after[c.end():].lstrip()
                continue

            tm = TIMEOVR_RE.match(after)
            if tm:
                tov = (_hm(tm[1], tm[2]), _hm(tm[3], tm[4]))
                after2 = after[tm.end():].lstrip()
                if entries and (not after2 or PREFIX_RE.match(after2)):
                    entries[-1].time_notes.append((prefix, tov))   # заметка к предыдущему
                    rest = after2
                    continue
                own_prefix, own_time, rest = prefix, tov, after2
                break

            own_prefix, rest = prefix, after
            break

        if not rest:
            continue

        teacher = ""
        tms = list(TEACHER_RE.finditer(rest))
        body = rest
        if tms and tms[-1].end() == len(rest):
            teacher = tms[-1].group(0)
            body = rest[: tms[-1].start()]
        body = body.strip(" -–—")
        subject_parts, kind, code = [], "", ""
        for part in re.split(r"\s+[-–—]\s+", body):
            p = part.strip(" -–—")
            if not p:
                continue
            if not kind and p.lower() in KINDS:
                kind = p.lower()
            elif COURSE_RE.match(p):
                code = p
            else:
                subject_parts.append(p)
        if not subject_parts:
            warnings.append(f"Не удалось определить предмет: {chunk!r}")
            continue
        entries.append(Entry(own_prefix, own_time, " - ".join(subject_parts), kind, code, teacher))
    return entries


def _assign(tokens: list[str], n: int, warnings: list[str], what: str, ctx: str) -> list[str]: # приводит список значений к заданной длине n
    if n == 0:
        return []
    if len(tokens) == n:
        return tokens
    if len(tokens) == 1:
        return tokens * n
    if not tokens:
        return [""] * n
    warnings.append(f"{what}: {len(tokens)} значений на {n} занятий ({ctx})")
    return [tokens[i] if i < len(tokens) else tokens[-1] for i in range(n)]

 
 # main API
@dataclass
class ParseResult:
    lessons: list[Lesson]
    warnings: list[str]                 #для отладки
    groups: list[str]
    period: Optional[Period]


def parse_schedule(
    rows: list[list[str]],
    group: str,
    today: Optional[date] = None,
    year: Optional[int] = None,
) -> ParseResult:                       #гугл таблицы в lesson список
    today = today or date.today()
    st = analyze(rows, today, year)     #разбираем шапку таблицы
    group = normalize(group)
    if group not in st.groups:          #проверяем есть ли вообще группа в таблице
        raise ValueError(f"Группа «{group}» не найдена. Доступные: {', '.join(st.groups)}")
    gcol = st.groups[group]             #номер колонки где лежат занятия нужной группы   


    period: Optional[Period] = None    # период модуля — ближайший слева от колонки группы заголовок
    left = [p for c, p in st.periods if c <= gcol]
    if left:
        period = [p for c, p in st.periods if c <= gcol][-1]
    elif st.periods:
        period = st.periods[0][1]
    warnings: list[str] = []
    if period is None:                   #если не нашли берём сегодня + 60 дней, заглушка чтобы не было ошибки
        period = Period(today, today + timedelta(days=60))
        warnings.append("Период модуля в заголовке не найден — взяты ближайшие 60 дней.")

    lessons: dict[tuple, Lesson] = {}
    cur_day: Optional[int] = None
    slot: Optional[tuple[str, str]] = None

    def resolve(prefix: str, weekday: int) -> set[date]:
        out: set[date] = set()
        for m in DATE_TOKEN_RE.finditer(prefix):
            frm, d1, m1, d2, m2 = m.groups()
            try:
                a = period.make_date(int(d1), int(m1))
                if d2:
                    out.update(_weekly(a, period.make_date(int(d2), int(m2)), weekday))
                elif frm:
                    out.update(_weekly(a, period.end, weekday))
                else:
                    out.add(a)
            except ValueError:
                warnings.append(f"Некорректная дата в «{prefix}»")
        return out

    for r in range(st.header_row + 1, len(rows)):
        row = rows[r]
        day_txt = normalize(_cell(row, st.day_col)).lower()
        if day_txt in DAYS:
            cur_day = DAYS[day_txt]
        tm = TIME_RE.search(_cell(row, st.time_col))
        if not tm:
            continue
        slot = (_hm(tm[1], tm[2]), _hm(tm[3], tm[4]))
        if cur_day is None:
            continue

        text = _cell(row, gcol)
        if not normalize(text):
            continue
        ctx = f"{['Пн','Вт','Ср','Чт','Пт','Сб','Вс'][cur_day]} {slot[0]}, строка {r + 1}" #для сообщений об ошибке
        ents = parse_entries(text, warnings)
        rooms = _assign(normalize(_cell(row, gcol + 1)).split(), len(ents), warnings, "Аудитории", ctx)
        bld_raw = normalize(_cell(row, gcol + 2)) or normalize(_cell(row, gcol + 3))
        blds = _assign(bld_raw.split(), len(ents), warnings, "Корпуса", ctx)

        for e, room, bld in zip(ents, rooms, blds):
            if e.prefix is None:
                dates = set(_weekly(period.start, period.end, cur_day))
            else:
                dates = resolve(e.prefix, cur_day)
            override: dict[date, tuple[str, str]] = {}              #для конкретных дат (если у занятия своё время)
            if e.time and e.prefix:
                for d in resolve(e.prefix, cur_day):
                    override[d] = e.time
            for pref, tov in e.time_notes:
                for d in resolve(pref, cur_day):
                    override[d] = tov
            cancelled: set[date] = set()                            # собираем даты с отменой занятия
            for pref in e.cancel_prefixes:
                cancelled |= resolve(pref, cur_day)

            online = bld.lower() in ("online", "онлайн")
            
            
            for d in sorted(dates):                                  #cоздадим lesson на каждую дату
                start, end = override.get(d, slot)
                notes = []
                if d in override:
                    notes.append("время изменено")
                if d in cancelled:
                    notes.append("отмена занятия")
                les = Lesson(
                    date=d.isoformat(),
                    slot=slot[0],
                    start=start,
                    end=end,
                    subject=e.subject,
                    kind=e.kind,
                    teacher=e.teacher,
                    room="" if room in ("-", "–", "—") else room,    #нет аудитории
                    building="online" if online else bld,
                    building_name="" if online else st.buildings.get(bld, ""),
                    online=online,
                    course_code=e.course_code,
                    note=", ".join(notes),
                    cancelled=d in cancelled,
                )
                lessons[(les.key, les.room, les.teacher)] = les     #cкладываем в словарь

    out = sorted(lessons.values(), key=lambda l: (l.date, l.start, l.subject))   #сортируем - дате/начало/предмет
    return ParseResult(out, warnings, list(st.groups), period)



