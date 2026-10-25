"""Простое окно на tkinter: профили, выбор группы, папка Obsidian, автообновление."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from . import fetcher, notify, storage
from .parser import list_groups
from .service import SyncService
from .storage import Profile


class App(tk.Tk):
    # главный конструктор класса - инициализация графического окна,
    # загрузка первоначальных параметров и запуск основных системных потоков
    def __init__(self) -> None:
        super().__init__()
        self.title("Расписание → Obsidian")
        self.geometry("820x640")
        self.minsize(720, 560)

        self.settings = storage.load_settings()
        self.logq: queue.Queue[str] = queue.Queue()
        self.service = SyncService(self.settings, log=self.logq.put, notifier=self._notify)
        self.current: str | None = None

        self._build()
        self._reload_profiles()
        self.after(200, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # создает и структурирует элементы управления в виде иерархии фреймов
    # левую панель со списком профилей - листбокс, правую панель с полями ввода параметров профиля,
    # блок управления интервалами обновления и многострочное текстовое поле журнала с полосой прокрутки
    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        left = ttk.LabelFrame(root, text="Пользователи (профили)")
        left.pack(side="left", fill="y")
        self.listbox = tk.Listbox(left, width=22, exportselection=False)
        self.listbox.pack(fill="y", expand=True, **pad)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._on_select())
        ttk.Button(left, text="Добавить", command=self._add).pack(fill="x", **pad)
        ttk.Button(left, text="Удалить", command=self._delete).pack(fill="x", **pad)

        right = ttk.Frame(root)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        form = ttk.LabelFrame(right, text="Профиль")
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        self.v_name = tk.StringVar()
        self.v_url = tk.StringVar()
        self.v_group = tk.StringVar()
        self.v_vault = tk.StringVar()
        self.v_folder = tk.StringVar(value="Расписание")
        self.v_enabled = tk.BooleanVar(value=True)
        self.v_notify = tk.BooleanVar(value=True)

        # размещение строк в форме редактирования профиля
        
        def row(r: int, label: str, widget: tk.Widget) -> None:
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", **pad)
            widget.grid(row=r, column=1, sticky="ew", **pad)

        row(0, "Имя", ttk.Entry(form, textvariable=self.v_name))
        row(1, "Ссылка на таблицу", ttk.Entry(form, textvariable=self.v_url))

        gbox = ttk.Frame(form)
        gbox.columnconfigure(0, weight=1)
        self.cmb_group = ttk.Combobox(gbox, textvariable=self.v_group)
        self.cmb_group.grid(row=0, column=0, sticky="ew")
        self.btn_groups = ttk.Button(gbox, text="Загрузить группы", command=self._load_groups)
        self.btn_groups.grid(row=0, column=1, padx=(6, 0))
        row(2, "Группа", gbox)

        vbox = ttk.Frame(form)
        vbox.columnconfigure(0, weight=1)
        ttk.Entry(vbox, textvariable=self.v_vault).grid(row=0, column=0, sticky="ew")
        ttk.Button(vbox, text="Обзор…", command=self._browse).grid(row=0, column=1, padx=(6, 0))
        row(3, "Папка хранилища Obsidian", vbox)

        row(4, "Подпапка", ttk.Entry(form, textvariable=self.v_folder))
        flags = ttk.Frame(form)
        ttk.Checkbutton(flags, text="Обновлять этот профиль", variable=self.v_enabled).pack(side="left")
        ttk.Checkbutton(flags, text="Уведомлять об изменениях", variable=self.v_notify).pack(side="left", padx=12)
        row(5, "", flags)
        ttk.Button(form, text="Сохранить профиль", command=self._save_profile).grid(
            row=6, column=1, sticky="e", **pad)

        auto = ttk.LabelFrame(right, text="Обновление")
        auto.pack(fill="x", pady=8)
        self.v_interval = tk.IntVar(value=self.settings.interval_minutes)
        ttk.Label(auto, text="Интервал, минут:").pack(side="left", **pad)
        ttk.Spinbox(auto, from_=5, to=1440, width=6, textvariable=self.v_interval,
                    command=self._save_interval).pack(side="left")
        self.btn_now = ttk.Button(auto, text="Обновить сейчас", command=self._sync_now)
        self.btn_now.pack(side="left", padx=12)
        self.btn_auto = ttk.Button(auto, text="Запустить автообновление", command=self._toggle_auto)
        self.btn_auto.pack(side="left")
        self.lbl_status = ttk.Label(auto, text="автообновление выключено")
        self.lbl_status.pack(side="left", padx=12)

        logf = ttk.LabelFrame(right, text="Журнал")
        logf.pack(fill="both", expand=True)
        self.txt = tk.Text(logf, height=12, state="disabled", wrap="word")
        sb = ttk.Scrollbar(logf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True)

    # очищает компонент листбокс, заполняет его актуальными именами профилей
    # и программно восстанавливает выбор активного профиля

    def _reload_profiles(self, select: str | None = None) -> None:
        self.listbox.delete(0, "end")
        for p in self.settings.profiles:
            self.listbox.insert("end", p.name)
        names = [p.name for p in self.settings.profiles]
        target = select if select in names else (names[0] if names else None)
        if target:
            self.listbox.selection_set(names.index(target))
            self._on_select()
        else:
            self.current = None
            self._fill(Profile(name=""))

    # заполнение формы ввода данными выбранного профиля

    def _fill(self, p: Profile) -> None:
        self.v_name.set(p.name)
        self.v_url.set(p.sheet_url)
        self.v_group.set(p.group)
        self.v_vault.set(p.vault_path)
        self.v_folder.set(p.folder)
        self.v_enabled.set(p.enabled)
        self.v_notify.set(p.notify)

    # подтягивает настройки выбранного профиля и заполняет через филл

    def _on_select(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        p = self.settings.profiles[sel[0]]
        self.current = p.name
        self._fill(p)

    # создание нового профиля
    
    def _add(self) -> None:
        n, base = 1, "Новый профиль"
        names = {p.name for p in self.settings.profiles}
        while f"{base} {n}" in names:
            n += 1
        p = Profile(name=f"{base} {n}")
        self.settings.profiles.append(p)
        storage.save_settings(self.settings)
        self._reload_profiles(p.name)

    # удаление профиля

    def _delete(self) -> None:
        if not self.current:
            return
        if not messagebox.askyesno("Удалить", f"Удалить профиль «{self.current}»?"):
            return
        self.settings.profiles = [p for p in self.settings.profiles if p.name != self.current]
        storage.save_settings(self.settings)
        self._reload_profiles()

    # проверяет корректность введенных данных и сохраняет изменения

    def _save_profile(self) -> bool:
        name = self.v_name.get().strip()
        if not name:
            messagebox.showwarning("Профиль", "Укажите имя профиля.")
            return False
        if name != self.current and self.settings.get(name):
            messagebox.showwarning("Профиль", "Профиль с таким именем уже есть.")
            return False
        p = self.settings.get(self.current) if self.current else None
        if p is None:
            p = Profile(name=name)
            self.settings.profiles.append(p)
        p.name = name
        p.sheet_url = self.v_url.get().strip()
        p.group = self.v_group.get().strip()
        p.vault_path = self.v_vault.get().strip()
        p.folder = self.v_folder.get().strip() or "Расписание"
        p.enabled = self.v_enabled.get()
        p.notify = self.v_notify.get()
        self._save_interval()
        self.current = name
        self._reload_profiles(name)
        self._log(f"Профиль «{name}» сохранён.")
        return True

    # сохранение интервала обновления

    def _save_interval(self) -> None:
        try:
            self.settings.interval_minutes = max(5, int(self.v_interval.get()))
        except (tk.TclError, ValueError):
            self.settings.interval_minutes = 30
        storage.save_settings(self.settings)

    # путь к хранилищу

    def _browse(self) -> None:
        d = filedialog.askdirectory(title="Папка хранилища Obsidian (vault)")
        if d:
            self.v_vault.set(d)

    # получение списка учебных групп из гугл таблички

    def _load_groups(self) -> None:
        url = self.v_url.get().strip()
        self.btn_groups.state(["disabled"])

        def work() -> None:
            try:
                groups = list_groups(fetcher.fetch_rows(url))
                self.after(0, lambda: self._groups_loaded(groups))
            except Exception as e:
                self.after(0, lambda: self._groups_failed(e))

        threading.Thread(target=work, daemon=True).start()

    # обновляет выпадающий список доступных групп

    def _groups_loaded(self, groups: list[str]) -> None:
        self.btn_groups.state(["!disabled"])
        self.cmb_group["values"] = groups
        if groups and self.v_group.get() not in groups:
            self.v_group.set(groups[0])
        self._log(f"Найдено групп: {len(groups)}")

    # сбой загрузки групп

    def _groups_failed(self, e: Exception) -> None:
        self.btn_groups.state(["!disabled"])
        messagebox.showerror("Не удалось загрузить группы", str(e))

    # принудительный запуск процесса синхронизации данных

    def _sync_now(self) -> None:
        if self.current and not self._save_profile():
            return
        self.btn_now.state(["disabled"])

        def work() -> None:
            try:
                self.service.sync_all()
            finally:
                self.after(0, lambda: self.btn_now.state(["!disabled"]))

        threading.Thread(target=work, daemon=True).start()

    # состояние фонового автообновления по таймеру

    def _toggle_auto(self) -> None:
        if self.service.running:
            self.service.stop()
            self.btn_auto.configure(text="Запустить автообновление")
            self.lbl_status.configure(text="автообновление выключено")
            self._log("Автообновление остановлено.")
        else:
            if self.current and not self._save_profile():
                return
            self.service.start()
            self.btn_auto.configure(text="Остановить автообновление")
            self._log(f"Автообновление запущено, интервал {self.settings.interval_minutes} мин.")

    # уведы

    def _notify(self, title: str, message: str) -> None:
        if not notify.send(title, message):
            self.after(0, lambda: messagebox.showinfo(title, message))

    def _log(self, text: str) -> None:
        self.logq.put(text)

    # периодическое обновление интерфейса

    def _pump(self) -> None:
        try:
            while True:
                line = self.logq.get_nowait()
                self.txt.configure(state="normal")
                self.txt.insert("end", f"{datetime.now():%H:%M:%S}  {line}\n")
                self.txt.see("end")
                self.txt.configure(state="disabled")
        except queue.Empty:
            pass
        if self.service.running and self.service.next_run:
            self.lbl_status.configure(text=f"следующее обновление в {self.service.next_run:%H:%M}")
        self.after(500, self._pump)

    # закрытие программы - закрывает все окна и останавливает процессы

    def _close(self) -> None:
        self.service.stop()
        self.destroy()

# точка входа в приложение
def main() -> None:
    App().mainloop()
