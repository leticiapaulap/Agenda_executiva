"""Interface desktop. Persistência e validações são delegadas aos serviços."""
import calendar
import logging
import sqlite3
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .dialogs import RecordDialog, DeleteEventDialog, EventDetailsDialog
from .event_menu import EventActionMenu
from .services import AgendaService, CATEGORIES, PRIORITIES, REMINDERS, format_date, long_date, parse_date
from .storage import Database
from .theme import (BG, CARD, INK, MUTED, BORDER, SOFT, BLUE, NAV, GREEN, RED,
                    PRIORITY_COLORS, asset_path, font, label, button, card, OptionMenu)

MONTHS = ('Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
          'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro')
VIEWS = {'dashboard': ('Painel Inicial', 'Uma visão clara do seu dia.'),
         'calendar': ('Calendário', 'Reserve tempo para o que é importante.'),
         'events': ('Compromissos', 'Sua agenda profissional, organizada.'),
         'tasks': ('Tarefas', 'Transforme prioridades em entregas.'),
         'settings': ('Configurações', 'Uma agenda do seu jeito.')}


class AgendaApp(ctk.CTk):
    def __init__(self, db=None, *, enable_reminders=True):
        self.db = db or Database()
        self.db.initialize()
        self.service = AgendaService(self.db)
        self.settings = self.db.get_settings()
        ctk.set_appearance_mode(self.appearance(self.settings))
        ctk.set_default_color_theme('blue')
        ctk.set_widget_scaling(self.scaling(self.settings))
        super().__init__()
        self.title('Agenda Executiva')
        width = min(1240, max(1000, self.winfo_screenwidth() - 80))
        height = min(820, max(650, self.winfo_screenheight() - 100))
        self.geometry(f'{width}x{height}+{max(0, (self.winfo_screenwidth()-width)//2)}+{max(0, (self.winfo_screenheight()-height)//2-20)}')
        self.minsize(1000, 650)
        self.configure(fg_color=BG)
        self.current_view = 'dashboard'
        self.selected_date = date.today()
        self.calendar_month = self.selected_date.replace(day=1)
        self._search_job = self._toast_job = self._reminder_job = self._clock_job = None
        self._closed = False
        self._dialog = None
        self._delete_dialog = None
        self._event_menu = None
        self._last_day = date.today()
        self._page = 0
        self._filters = {'events': {'search': '', 'period': 'Todos', 'start': '', 'end': '',
                                   'status': 'Todos', 'category': 'Todas', 'priority': 'Todas'},
                         'tasks': {'search': '', 'status': 'Pendentes', 'priority': 'Todas'}}
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.build_shell()
        self.render()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Control-n>', lambda _: self.open_dialog())
        self.bind('<Control-f>', lambda _: self.focus_search())
        self.bind('<Button-1>', self.close_event_menu_on_click, add='+')
        self.bind('<MouseWheel>', lambda _: self.close_event_menu(), add='+')
        self.bind('<Configure>', self.dismiss_menu_on_resize, add='+')
        self.after(200, self.set_icon)
        self._clock_job = self.after(60000, self.tick)
        if enable_reminders:
            self._reminder_job = self.after(2500, self.check_reminders)

    @staticmethod
    def appearance(settings):
        return {'Claro': 'Light', 'Escuro': 'Dark', 'Sistema': 'System'}.get(settings.get('appearance'), 'Light')

    @staticmethod
    def scaling(settings):
        return {'90': .9, '100': 1., '110': 1.1}.get(settings.get('scaling'), 1.)

    def set_icon(self):
        try:
            self.iconbitmap(str(asset_path('app.ico')))
        except tk.TclError:
            logging.exception('Unable to set application icon')

    def build_shell(self):
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=NAV)
        self.sidebar.grid(row=0, column=0, sticky='ns')
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(8, weight=1)
        logo = ctk.CTkFrame(self.sidebar, fg_color='#254773', width=42, height=42, corner_radius=12)
        logo.grid(row=0, column=0, sticky='w', padx=24, pady=(30, 12))
        logo.pack_propagate(False)
        label(logo, 'A', 24, True, color='white').pack(expand=True)
        label(self.sidebar, 'AGENDA', 24, True, color='white').grid(row=1, column=0, sticky='w', padx=24)
        label(self.sidebar, 'E X E C U T I V A', 11, color='#A8BDDA').grid(row=2, column=0, sticky='w', padx=25, pady=(0, 36))
        self.nav_buttons = {}
        for row, (key, icon) in enumerate([('dashboard', '◈'), ('calendar', '▦'), ('events', '≡'),
                                           ('tasks', '✓'), ('settings', '⚙')], 3):
            nav = ctk.CTkButton(self.sidebar, text=f'{icon}    {VIEWS[key][0]}', anchor='w',
                                font=font(14), height=46, corner_radius=9,
                                fg_color='transparent', hover_color='#233E60', text_color='#B8C9DF',
                                command=lambda k=key: self.navigate(k))
            nav.grid(row=row, column=0, sticky='ew', padx=12, pady=3)
            self.nav_buttons[key] = nav
        bottom = ctk.CTkFrame(self.sidebar, fg_color='#1B3453', corner_radius=12)
        bottom.grid(row=9, column=0, sticky='ew', padx=16, pady=22)
        self.profile_label = label(bottom, self.settings['manager_name'] or 'Sua agenda pessoal', 13, True,
                                   color='white', wraplength=162, anchor='w', justify='left')
        self.profile_label.pack(anchor='w', padx=14, pady=(15, 4))
        label(bottom, '●  Local e offline', 11, color='#9ED7C6').pack(anchor='w', padx=14, pady=(0, 15))
        self.main = ctk.CTkFrame(self, fg_color='transparent', corner_radius=0)
        self.main.grid(row=0, column=1, sticky='nsew', padx=28, pady=(25, 12))
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self.main, fg_color='transparent')
        head.grid(row=0, column=0, sticky='ew', pady=(0, 22))
        head.grid_columnconfigure(0, weight=1)
        self.heading = label(head, '', 28, True, anchor='w')
        self.heading.grid(row=0, column=0, sticky='w')
        self.subheading = label(head, '', 13, color=MUTED, anchor='w')
        self.subheading.grid(row=1, column=0, sticky='w', pady=(4, 0))
        self.add_button = button(head, '+ Novo compromisso', self.open_dialog, width=178)
        self.add_button.grid(row=0, column=1, rowspan=2, padx=(12, 0))
        self.content = ctk.CTkScrollableFrame(self.main, fg_color='transparent', corner_radius=0)
        self.content.grid(row=1, column=0, sticky='nsew')
        self.content.grid_columnconfigure(0, weight=1)
        self.status = label(self.main, 'Pronto  ·  Seus dados ficam neste computador.', 11, color=MUTED, anchor='w')
        self.status.grid(row=2, column=0, sticky='ew', pady=(10, 0))

    def navigate(self, view):
        self.current_view = view
        self._page = 0
        self.render()
        self.content._parent_canvas.yview_moveto(0)

    def render(self):
        logging.info('Render view=%s db=%s', self.current_view, self.db.path)
        self.close_event_menu()
        if self._search_job:
            self.after_cancel(self._search_job)
            self._search_job = None
        self.settings = self.db.get_settings()
        self.profile_label.configure(text=self.settings['manager_name'] or 'Sua agenda pessoal')
        for key, nav in self.nav_buttons.items():
            nav.configure(fg_color='#29496F' if key == self.current_view else 'transparent',
                          text_color='white' if key == self.current_view else '#B8C9DF')
        title, subtitle = VIEWS[self.current_view]
        self.heading.configure(text=title)
        self.subheading.configure(text=subtitle)
        self.add_button.configure(text='+ Nova tarefa' if self.current_view == 'tasks' else '+ Novo compromisso')
        for child in self.content.winfo_children():
            child.destroy()
        try:
            {'dashboard': self.dashboard, 'calendar': self.calendar_view,
             'events': self.events_view, 'tasks': self.tasks_view, 'settings': self.settings_view}[self.current_view]()
        except Exception as exc:
            self.show_error(exc)

    def section_title(self, parent, title, subtitle=None):
        label(parent, title, 18, True).pack(anchor='w', padx=20, pady=(18, 4 if subtitle else 14))
        if subtitle:
            label(parent, subtitle, 12, color=MUTED, wraplength=650, justify='left').pack(anchor='w', padx=20, pady=(0, 15))

    def dashboard(self):
        now = datetime.now()
        greeting = 'Bom dia' if now.hour < 12 else 'Boa tarde' if now.hour < 18 else 'Boa noite'
        name = self.settings['manager_name'].strip()
        self.heading.configure(text=f'{greeting}{", " + name.split()[0] if name else ""}.')
        self.subheading.configure(text=long_date(date.today()))
        today = self.db.list_events(start=date.today().isoformat(), end=date.today().isoformat())
        pending = self.db.list_events(status='Pendentes')
        tasks = self.db.list_tasks(status='Pendentes')
        stats = ctk.CTkFrame(self.content, fg_color='transparent')
        stats.grid(row=0, column=0, sticky='ew', pady=(0, 18))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform='stats')
        for i, (value, text, color) in enumerate([(len(today), 'Compromissos hoje', BLUE),
                (len(pending), 'Agenda pendente', BLUE), (len(tasks), 'Tarefas pendentes', '#B77216'),
                (sum(bool(e['completed']) for e in today), 'Concluídos hoje', '#107A66')]):
            box = card(stats)
            box.grid(row=0, column=i, sticky='nsew', padx=(0 if i == 0 else 6, 0 if i == 3 else 6))
            label(box, f'{value:02d}', 30, True, color=color).pack(anchor='w', padx=18, pady=(15, 0))
            label(box, text, 12, color=MUTED).pack(anchor='w', padx=18, pady=(0, 16))
        upcoming = [e for e in pending if f'{e["event_date"]} {e["event_time"]}' >= now.strftime('%Y-%m-%d %H:%M')]
        meetings = [e for e in upcoming if e['category'].casefold().startswith('reuni')]
        next_event = meetings[0] if meetings else (upcoming[0] if upcoming else None)
        hero = ctk.CTkFrame(self.content, fg_color=('#E7EFFD', '#203D62'), corner_radius=14)
        hero.grid(row=1, column=0, sticky='ew', pady=(0, 20))
        label(hero, 'A SEGUIR  /  PRÓXIMA REUNIÃO' if meetings else 'A SEGUIR  /  PRÓXIMO COMPROMISSO',
              11, True, color=BLUE if ctk.get_appearance_mode() == 'Light' else '#A8C8FF').pack(anchor='w', padx=24, pady=(20, 6))
        if next_event:
            label(hero, next_event['title'], 23, True, wraplength=650, justify='left').pack(anchor='w', padx=24)
            info = f'{format_date(next_event["event_date"])}  ·  {next_event["event_time"]}'
            if next_event['end_time']:
                info += f' – {next_event["end_time"]}'
            if next_event['location']:
                info += f'  ·  {next_event["location"]}'
            label(hero, info, 13, color=MUTED, wraplength=650, justify='left').pack(anchor='w', padx=24, pady=(4, 12))
            button(hero, 'Ver compromisso', lambda e=next_event: self.open_event_details(e['id']), secondary=True,
                   width=150).pack(anchor='w', padx=24, pady=(0, 20))
        else:
            label(hero, 'Espaço para planejar.', 23, True).pack(anchor='w', padx=24)
            label(hero, 'Cadastre seu próximo compromisso e tenha o dia sob controle.', 13,
                  color=MUTED).pack(anchor='w', padx=24, pady=(5, 24))
        daily = card(self.content)
        daily.grid(row=2, column=0, sticky='ew', pady=(0, 18))
        self.section_title(daily, 'Sua agenda de hoje', 'Horários, prioridades e detalhes em um só lugar.')
        if today:
            for event in today[:8]:
                self.event_row(daily, event)
            button(daily, 'Abrir calendário', lambda: self.navigate('calendar'), secondary=True).pack(anchor='w', padx=18, pady=(5, 16))
        else:
            self.empty(daily, 'Seu dia está livre.', 'Use esse espaço para planejar suas próximas reuniões.', 'Agendar compromisso', self.open_dialog)
        task_box = card(self.content)
        task_box.grid(row=3, column=0, sticky='ew')
        self.section_title(task_box, 'Prioridades em foco')
        for task in tasks[:4]:
            self.task_row(task_box, task)
        if not tasks:
            self.empty(task_box, 'Tudo em dia.', 'Adicione uma tarefa para organizar suas próximas entregas.', 'Criar tarefa', lambda: self.open_dialog(kind='task'))
        else:
            button(task_box, 'Ver todas as tarefas', lambda: self.navigate('tasks'), secondary=True).pack(anchor='w', padx=18, pady=(5, 16))

    def empty(self, parent, title, subtitle, action=None, command=None):
        box = ctk.CTkFrame(parent, fg_color='transparent')
        box.pack(fill='x', padx=24, pady=(15, 28))
        label(box, '○', 30, color=MUTED).pack()
        label(box, title, 17, True).pack(pady=(5, 3))
        label(box, subtitle, 13, color=MUTED, wraplength=550, justify='center').pack()
        if action:
            button(box, action, command, secondary=True).pack(pady=(15, 0))

    def event_row(self, parent, event):
        row = ctk.CTkFrame(parent, fg_color='transparent')
        row.event_id = event['id']
        row.pack(fill='x', padx=18, pady=(0, 12))
        row.grid_columnconfigure(1, weight=1)
        time_box = ctk.CTkFrame(row, fg_color=SOFT, width=72, corner_radius=9)
        time_box.grid(row=0, column=0, sticky='ns', padx=(0, 14))
        label(time_box, event['event_time'], 16, True).pack(padx=10, pady=(10, 0))
        label(time_box, format_date(event['event_date'])[:5], 11, color=MUTED).pack(pady=(0, 10))
        details = ctk.CTkFrame(row, fg_color='transparent')
        details.grid(row=0, column=1, sticky='ew')
        label(details, event['title'], 15, True, color=MUTED if event['completed'] else INK,
              wraplength=340, anchor='w', justify='left').pack(anchor='w')
        meta = f'{event["category"]}  ·  {event["priority"]}'
        if event['end_time']:
            meta += f'  ·  até {event["end_time"]}'
        if event['location']:
            meta += f'  ·  {event["location"]}'
        label(details, meta, 12, color=MUTED, wraplength=340, anchor='w', justify='left').pack(anchor='w')
        if event.get('description'):
            label(details, event['description'][:180], 12, color=MUTED, wraplength=340, justify='left').pack(anchor='w')
        label(details, 'Concluído' if event['completed'] else 'Pendente', 11, True,
              color=GREEN if event['completed'] else PRIORITY_COLORS.get(event['priority'], BLUE)).pack(anchor='w')
        # Somente um controle visível. Selecionar/clicar a linha nunca altera dados.
        more = ctk.CTkButton(row, text='⋯', width=38, height=34, corner_radius=8,
                             font=font(23), fg_color='transparent', hover_color=SOFT, text_color=MUTED)
        event_id = event['id']
        more.configure(command=lambda record_id=event_id, anchor=more:
                       self.on_event_menu_click(record_id, anchor))
        more.event_id = event_id
        more.grid(row=0, column=2, sticky='e', padx=(12, 0))
        more.bind('<Return>', lambda _=None, record_id=event_id, anchor=more:
                  self.on_event_menu_click(record_id, anchor))
        more.bind('<space>', lambda _=None, record_id=event_id, anchor=more:
                  self.on_event_menu_click(record_id, anchor))
        self.bind_event_details(row, event_id, exclude=more)

    def on_event_menu_click(self, event_id, anchor):
        logging.info('Three-dot click event_id=%s view=%s widget=%s mapped=%s db=%s',
                     event_id, self.current_view, anchor, anchor.winfo_ismapped(), self.db.path)
        self.open_event_menu(event_id, anchor)

    def bind_event_details(self, widget, event_id, exclude=None):
        if widget is exclude:
            return
        # CTkLabel faz o encaminhamento para o Label Tcl; inclui o espaço livre.
        widget.bind('<Double-Button-1>',
                    lambda _=None, record_id=event_id: self.open_event_details(record_id), add='+')
        for child in widget.winfo_children():
            # Os controles internos CTk já recebem o binding público do pai.
            if isinstance(child, (ctk.CTkFrame, ctk.CTkLabel)):
                self.bind_event_details(child, event_id, exclude=exclude)

    def small_action(self, parent, title, command, danger=False):
        ctk.CTkButton(parent, text=title, command=command, width=54, height=34,
                      corner_radius=7, fg_color='transparent', hover_color=SOFT,
                      text_color=RED if danger else MUTED, font=font(12)).pack(side='left', padx=1)

    def task_row(self, parent, task):
        row = ctk.CTkFrame(parent, fg_color='transparent')
        row.pack(fill='x', padx=20, pady=(0, 15))
        row.grid_columnconfigure(1, weight=1)
        var = tk.BooleanVar(value=bool(task['completed']))
        ctk.CTkCheckBox(row, text='', width=24, variable=var, checkbox_width=22, checkbox_height=22,
                        command=lambda: self.toggle('task', task)).grid(row=0, column=0, padx=(0, 10))
        body = ctk.CTkFrame(row, fg_color='transparent')
        body.grid(row=0, column=1, sticky='ew')
        label(body, task['title'], 15, True, color=MUTED if task['completed'] else INK,
              wraplength=430, justify='left').pack(anchor='w')
        due = task.get('due_date')
        overdue = due and due < date.today().isoformat() and not task['completed']
        meta = task['priority'] + ('  ·  Prazo: ' + format_date(due) if due else '  ·  Sem prazo')
        if overdue:
            meta += '  ·  Atrasada'
        label(body, meta, 12, color=RED if overdue else MUTED).pack(anchor='w')
        if task.get('description'):
            label(body, task['description'][:180], 12, color=MUTED, wraplength=430, justify='left').pack(anchor='w')
        actions = ctk.CTkFrame(row, fg_color='transparent')
        actions.grid(row=0, column=2, padx=(8, 0))
        self.small_action(actions, 'Editar', lambda: self.open_dialog(kind='task', record=task))
        self.small_action(actions, 'Excluir', lambda: self.delete('task', task), danger=True)

    def calendar_view(self):
        box = card(self.content)
        box.grid(row=0, column=0, sticky='ew', pady=(0, 18))
        box.grid_columnconfigure(tuple(range(7)), weight=1, uniform='days')
        head = ctk.CTkFrame(box, fg_color='transparent')
        head.grid(row=0, column=0, columnspan=7, sticky='ew', padx=18, pady=18)
        head.grid_columnconfigure(0, weight=1)
        label(head, f'{MONTHS[self.calendar_month.month - 1]} {self.calendar_month.year}', 21, True).grid(row=0, column=0, sticky='w')
        button(head, '‹', lambda: self.change_month(-1), secondary=True, width=38).grid(row=0, column=1, padx=3)
        button(head, 'Hoje', self.calendar_today, secondary=True, width=65).grid(row=0, column=2, padx=3)
        button(head, '›', lambda: self.change_month(1), secondary=True, width=38).grid(row=0, column=3, padx=3)
        for col, day in enumerate(('SEG', 'TER', 'QUA', 'QUI', 'SEX', 'SÁB', 'DOM')):
            label(box, day, 11, True, color=MUTED).grid(row=1, column=col, pady=(0, 6))
        last_day = calendar.monthrange(self.calendar_month.year, self.calendar_month.month)[1]
        events = self.db.list_events(start=self.calendar_month.isoformat(),
                                     end=self.calendar_month.replace(day=last_day).isoformat())
        counts = {}
        for event in events:
            counts[event['event_date']] = counts.get(event['event_date'], 0) + 1
        for r, week in enumerate(calendar.Calendar(firstweekday=0).monthdayscalendar(self.calendar_month.year, self.calendar_month.month), 2):
            for col, day in enumerate(week):
                if not day:
                    ctk.CTkFrame(box, fg_color='transparent', height=64).grid(row=r, column=col)
                    continue
                value = self.calendar_month.replace(day=day)
                selected, current = value == self.selected_date, value == date.today()
                count = counts.get(value.isoformat(), 0)
                text = str(day) + (f'\n● {count}' if count else '')
                ctk.CTkButton(box, text=text, width=1, height=64, corner_radius=10, font=font(14, current or selected),
                    fg_color=BLUE if selected else SOFT if current else 'transparent',
                    hover_color='#1E4EAE' if selected else SOFT, text_color='white' if selected else INK,
                    border_width=1 if current else 0, border_color=BLUE,
                    command=lambda d=value: self.select_day(d)).grid(row=r, column=col, sticky='ew', padx=5, pady=4)
        label(box, '● Dia com compromissos     ·     Contorno azul: hoje', 11, color=MUTED).grid(row=9, column=0, columnspan=7, sticky='w', padx=20, pady=15)
        daily = card(self.content)
        daily.grid(row=1, column=0, sticky='ew')
        self.section_title(daily, f'Agenda · {format_date(self.selected_date)}', long_date(self.selected_date))
        day_events = self.db.list_events(start=self.selected_date.isoformat(), end=self.selected_date.isoformat())
        for event in day_events:
            self.event_row(daily, event)
        if not day_events:
            self.empty(daily, 'Nenhum compromisso nesta data.', 'Selecione outro dia ou adicione um novo compromisso.', 'Agendar nesta data', self.open_dialog)
        else:
            button(daily, '+ Agendar nesta data', self.open_dialog, secondary=True).pack(anchor='w', padx=18, pady=(0, 16))

    def change_month(self, step):
        index = self.calendar_month.year * 12 + self.calendar_month.month - 1 + step
        year, month = divmod(index, 12)
        if 1 <= year <= 9999:
            self.calendar_month = date(year, month + 1, 1)
            self.render()

    def calendar_today(self):
        self.selected_date = date.today()
        self.calendar_month = self.selected_date.replace(day=1)
        self.render()

    def select_day(self, value):
        self.selected_date = value
        self.render()

    def filter_option(self, parent, key, values, row, col, title, view):
        box = ctk.CTkFrame(parent, fg_color='transparent')
        box.grid(row=row, column=col, sticky='ew', padx=5, pady=6)
        label(box, title, 11, True, color=MUTED).pack(anchor='w', pady=(0, 4))
        menu = OptionMenu(box, values=list(values), font=font(12), height=35,
                               fg_color=SOFT, text_color=INK, button_color=BLUE, dynamic_resizing=False,
                               command=lambda value: self.change_filter(view, key, value))
        menu.set(self._filters[view][key])
        menu.pack(fill='x')
        return menu

    def events_view(self):
        filters = card(self.content)
        filters.grid(row=0, column=0, sticky='ew', pady=(0, 18))
        filters.grid_columnconfigure((0, 1, 2), weight=1, uniform='filters')
        self.build_search(filters, 'events')
        self.filter_option(filters, 'period', ['Todos', 'Hoje', 'Próximos 7 dias', 'Este mês', 'Personalizado'], 1, 0, 'PERÍODO', 'events')
        self.filter_option(filters, 'status', ['Todos', 'Pendentes', 'Concluídos'], 1, 1, 'STATUS', 'events')
        self.filter_option(filters, 'priority', ['Todas', *PRIORITIES], 1, 2, 'PRIORIDADE', 'events')
        self.filter_option(filters, 'category', ['Todas', *CATEGORIES], 2, 0, 'CATEGORIA', 'events')
        for col, key, title in [(1, 'start', 'DE · DD/MM/AAAA'), (2, 'end', 'ATÉ · DD/MM/AAAA')]:
            box = ctk.CTkFrame(filters, fg_color='transparent')
            box.grid(row=2, column=col, sticky='ew', padx=5, pady=(6, 14))
            label(box, title, 11, True, color=MUTED).pack(anchor='w', pady=(0, 4))
            entry = ctk.CTkEntry(box, height=35, font=font(13), placeholder_text='Período personalizado')
            entry.insert(0, self._filters['events'][key])
            entry.pack(fill='x')
            entry.bind('<KeyRelease>', lambda _, k=key, field=entry: self.change_filter('events', k, field.get()))
        self.results = card(self.content)
        self.results.grid(row=1, column=0, sticky='ew')
        self.update_results()

    def tasks_view(self):
        filters = card(self.content)
        filters.grid(row=0, column=0, sticky='ew', pady=(0, 18))
        filters.grid_columnconfigure((0, 1, 2), weight=1, uniform='filters')
        self.build_search(filters, 'tasks')
        self.filter_option(filters, 'status', ['Pendentes', 'Concluídas', 'Todas'], 1, 0, 'EXIBIR', 'tasks')
        self.filter_option(filters, 'priority', ['Todas', *PRIORITIES], 1, 1, 'PRIORIDADE', 'tasks')
        label(filters, 'Prioridade e prazo\npara um dia organizado.', 12, color=MUTED).grid(row=1, column=2, pady=(8, 18))
        self.results = card(self.content)
        self.results.grid(row=1, column=0, sticky='ew')
        self.update_results()
        label(self.content, 'Registros antigos da categoria Tarefa continuam disponíveis em Compromissos.', 11,
              color=MUTED).grid(row=2, column=0, sticky='w', pady=12)

    def build_search(self, parent, view):
        self.search_field = ctk.CTkEntry(parent, placeholder_text='Pesquisar título, descrição, local…' if view == 'events'
                                       else 'Pesquisar tarefas…', font=font(14), height=42, border_color=BORDER)
        self.search_field.grid(row=0, column=0, columnspan=3, sticky='ew', padx=12, pady=(14, 8))
        self.search_field.insert(0, self._filters[view]['search'])
        self.search_field.bind('<KeyRelease>', lambda _: self.change_filter(view, 'search', self.search_field.get()))

    def focus_search(self):
        if self.current_view not in ('events', 'tasks'):
            self.navigate('events')
        self.search_field.focus_set()

    def change_filter(self, view, key, value):
        self._filters[view][key] = value
        self._page = 0
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(300, self.update_results)

    def update_results(self):
        self._search_job = None
        self.close_event_menu()
        if self.current_view not in ('events', 'tasks'):
            return
        for child in self.results.winfo_children():
            child.destroy()
        f = self._filters[self.current_view]
        try:
            if self.current_view == 'events':
                start = end = None
                today = date.today()
                if f['period'] == 'Hoje':
                    start = end = today.isoformat()
                elif f['period'] == 'Próximos 7 dias':
                    start, end = today.isoformat(), (today + timedelta(days=6)).isoformat()
                elif f['period'] == 'Este mês':
                    start = today.replace(day=1).isoformat()
                    end = today.replace(day=calendar.monthrange(today.year, today.month)[1]).isoformat()
                elif f['period'] == 'Personalizado':
                    start = parse_date(f['start']).isoformat() if f['start'].strip() else None
                    end = parse_date(f['end']).isoformat() if f['end'].strip() else None
                    if start and end and start > end:
                        raise ValueError('A data inicial deve ser anterior ou igual à data final.')
                records = self.db.list_events(search=f['search'], start=start, end=end, status=f['status'],
                                              category=f['category'], priority=f['priority'])
            else:
                status = {'Concluídas': 'Concluídos', 'Todas': 'Todos'}.get(f['status'], f['status'])
                records = self.db.list_tasks(search=f['search'], status=status, priority=f['priority'])
        except ValueError as exc:
            label(self.results, str(exc), 13, color=RED, wraplength=650).pack(padx=20, pady=25)
            return
        except Exception as exc:
            self.show_error(exc)
            return
        self.section_title(self.results, f'{len(records)} ' + ('compromisso(s)' if self.current_view == 'events' else 'tarefa(s)'))
        if not records:
            self.empty(self.results, 'Nenhum item por aqui.', 'Cadastre um item ou ajuste os filtros de pesquisa.')
            return
        self._page = min(self._page, (len(records) - 1) // 30)
        for record in records[self._page * 30:(self._page + 1) * 30]:
            (self.event_row if self.current_view == 'events' else self.task_row)(self.results, record)
        if len(records) > 30:
            pagination = ctk.CTkFrame(self.results, fg_color='transparent')
            pagination.pack(fill='x', padx=20, pady=(0, 18))
            button(pagination, 'Anterior', lambda: self.page(-1), secondary=True, width=100).pack(side='left')
            label(pagination, f'Página {self._page + 1} de {(len(records) + 29) // 30}', 12, color=MUTED).pack(side='left', padx=15)
            button(pagination, 'Próxima', lambda: self.page(1), secondary=True, width=100).pack(side='left')

    def page(self, step):
        self._page = max(0, self._page + step)
        self.update_results()

    def open_dialog(self, record=None, kind=None, mode='edit'):
        self.close_event_menu()
        if self._dialog is not None and self._dialog.winfo_exists():
            logging.warning('Dialog request ignored; existing dialog=%s mode=%s event_id=%s',
                            self._dialog, mode, (record or {}).get('id'))
            self._dialog.focus_force()
            self._dialog.lift(self)
            return
        kind = kind or ('task' if self.current_view == 'tasks' else 'event')
        logging.info('Opening record dialog mode=%s kind=%s event_id=%s db=%s',
                     mode, kind, (record or {}).get('id'), self.db.path)
        try:
            self._dialog = RecordDialog(self, kind=kind, record=record, mode=mode,
                                        initial_date=self.selected_date if self.current_view == 'calendar' else date.today())
            logging.info('Record dialog opened window=%s viewable=%s transient=%s',
                         self._dialog, self._dialog.winfo_viewable(), self._dialog.transient())
        except Exception:
            logging.exception('Failed opening record dialog mode=%s kind=%s event_id=%s',
                              mode, kind, (record or {}).get('id'))
            raise

    def close_event_menu(self):
        if self._event_menu is not None:
            self._event_menu.destroy()
            self._event_menu = None

    def close_event_menu_on_click(self, event):
        menu = self._event_menu
        if menu is None:
            return
        widget = event.widget
        while widget is not None:
            if widget is menu.anchor or widget is menu:
                logging.debug('Keeping action menu open for its own click widget=%s', event.widget)
                return
            widget = getattr(widget, 'master', None)
        logging.info('Closing action menu after outside click event_id=%s widget=%s',
                     menu.event_id, event.widget)
        self.close_event_menu()

    def dismiss_menu_on_resize(self, event):
        if event.widget is self:
            self.close_event_menu()

    def open_event_menu(self, event_id, anchor):
        self.close_event_menu()
        try:
            event = self.require_event(event_id)
            logging.info('Opening event menu event_id=%s title=%r anchor=%s mapped=%s',
                         event_id, event['title'], anchor, anchor.winfo_ismapped())
            self._event_menu = EventActionMenu(self, event, anchor)
            logging.info('Event menu opened window=%s viewable=%s rect=%s',
                         self._event_menu, self._event_menu.winfo_viewable(),
                         (self._event_menu.winfo_rootx(), self._event_menu.winfo_rooty(),
                          self._event_menu.winfo_width(), self._event_menu.winfo_height()))
        except Exception as exc:
            logging.exception('Failed opening event menu event_id=%s', event_id)
            self.show_error(exc)

    def require_event(self, event_id):
        event = self.db.get_event(event_id)
        if event is None:
            raise ValueError('O compromisso não foi encontrado. Atualize a agenda.')
        return event

    def perform_event_action(self, action, event_id):
        self.close_event_menu()
        try:
            # Buscar pelo ID evita usar o estado de uma linha anterior ao filtro.
            event = self.require_event(event_id)
            logging.info('Event action=%s event_id=%s title=%r db=%s',
                         action, event_id, event['title'], self.db.path)
            if action in ('edit', 'reschedule', 'duplicate'):
                self.open_dialog(record=event, kind='event', mode=action)
            elif action == 'toggle':
                self.toggle('event', event)
            elif action == 'delete':
                self.delete('event', event)
            else:
                raise ValueError('Ação de compromisso inválida.')
        except Exception as exc:
            logging.exception('Event action failed action=%s event_id=%s', action, event_id)
            self.show_error(exc)

    def open_event_details(self, event_id):
        self.close_event_menu()
        if self._dialog is not None and self._dialog.winfo_exists():
            self._dialog.focus_force()
            return 'break'
        try:
            self._dialog = EventDetailsDialog(self, self.require_event(event_id))
        except Exception as exc:
            self.show_error(exc)
        return 'break'

    def toggle(self, kind, record):
        try:
            (self.db.toggle_event if kind == 'event' else self.db.toggle_task)(record['id'])
            self.render()
            self.feedback('Item reaberto.' if record['completed'] else 'Item concluído. Bom trabalho!')
        except Exception as exc:
            self.show_error(exc)

    def delete(self, kind, record):
        try:
            record_id = record['id']
            logging.info('Delete requested kind=%s record_id=%s db=%s',
                         kind, record_id, self.db.path)
            if kind == 'event':
                record = self.require_event(record_id)
                if self._delete_dialog is not None and self._delete_dialog.winfo_exists():
                    self._delete_dialog.focus_force()
                    self._delete_dialog.lift(self)
                    return
                self._delete_dialog = DeleteEventDialog(self, record)
                logging.info('Delete dialog opened event_id=%s window=%s viewable=%s transient=%s',
                             record_id, self._delete_dialog, self._delete_dialog.winfo_viewable(),
                             self._delete_dialog.transient())
                return
            elif kind == 'task':
                prompt = f'Excluir “{record["title"]}”?\n\nEsta ação não pode ser desfeita.'
                delete_record = self.db.delete_task
            else:
                raise ValueError('Tipo de registro inválido para exclusão.')
            if not messagebox.askyesno('Excluir registro', prompt, parent=self):
                return
            delete_record(record_id)
            self.render()
            self.feedback('Registro excluído.')
        except Exception as exc:
            logging.exception('Delete request failed kind=%s record_id=%s',
                              kind, record.get('id') if isinstance(record, dict) else None)
            self.show_error(exc)

    def confirm_delete_event(self, event_id, dialog):
        try:
            logging.info('DELETE confirm event_id=%s db=%s', event_id, self.db.path)
            self.require_event(event_id)
            self.db.delete_event(event_id)
            logging.info('DELETE succeeded event_id=%s remaining=%s',
                         event_id, self.db.get_event(event_id) is not None)
        except Exception as exc:
            logging.exception('DELETE failed event_id=%s db=%s', event_id, self.db.path)
            self.show_error(exc, parent=dialog)
            return
        dialog.destroy()
        self.render()
        self.feedback('Compromisso excluído.')
        logging.info('Interface refreshed after DELETE event_id=%s view=%s',
                     event_id, self.current_view)

    def settings_view(self):
        box = card(self.content)
        box.grid(row=0, column=0, sticky='ew', pady=(0, 18))
        self.section_title(box, 'Perfil e preferências', 'Personalize a experiência da sua agenda.')
        form = ctk.CTkFrame(box, fg_color='transparent')
        form.pack(fill='x', padx=20, pady=(0, 18))
        form.grid_columnconfigure(1, weight=1)
        self.settings_fields = {}
        label(form, 'Nome do gestor', 13, True).grid(row=0, column=0, sticky='w', padx=(0, 22), pady=10)
        entry = ctk.CTkEntry(form, height=38, font=font())
        entry.insert(0, self.settings['manager_name'])
        entry.grid(row=0, column=1, sticky='ew', pady=10)
        self.settings_fields['manager_name'] = entry
        for row, key, title, values in [(1, 'appearance', 'Aparência', ['Claro', 'Escuro', 'Sistema']),
                                       (2, 'scaling', 'Escala da interface (%)', ['90', '100', '110']),
                                       (3, 'default_reminder', 'Lembrete padrão', list(REMINDERS))]:
            label(form, title, 13, True).grid(row=row, column=0, sticky='w', padx=(0, 22), pady=10)
            menu = OptionMenu(form, values=values, height=38, font=font(13), dynamic_resizing=False)
            current = self.settings[key]
            if key == 'default_reminder':
                current = next((k for k, v in REMINDERS.items() if str(v) == current), 'Sem lembrete')
            menu.set(current if current in values else values[0])
            menu.grid(row=row, column=1, sticky='ew', pady=10)
            self.settings_fields[key] = menu
        self.reminders_var = tk.BooleanVar(value=self.settings['reminders_enabled'] == '1')
        ctk.CTkSwitch(form, text='Ativar lembretes de compromissos', variable=self.reminders_var,
                       font=font(13)).grid(row=4, column=0, columnspan=2, sticky='w', pady=(16, 5))
        label(form, 'Mantenha o aplicativo aberto para receber alertas. O Windows pode silenciá-los\n'
                    'conforme as configurações de notificações e Não incomodar.', 12,
              color=MUTED, justify='left', wraplength=620).grid(row=5, column=0, columnspan=2, sticky='w', pady=(5, 16))
        button(form, 'Salvar preferências', self.save_settings).grid(row=6, column=1, sticky='e')
        data = card(self.content)
        data.grid(row=1, column=0, sticky='ew')
        self.section_title(data, 'Seus dados, sob seu controle', 'O backup inclui compromissos, tarefas e preferências.')
        label(data, str(self.db.path), 12, color=MUTED, wraplength=640, justify='left').pack(anchor='w', padx=20, pady=(0, 16))
        actions = ctk.CTkFrame(data, fg_color='transparent')
        actions.pack(fill='x', padx=20, pady=(0, 20))
        button(actions, 'Fazer backup', self.backup).pack(side='left', padx=(0, 10))
        button(actions, 'Restaurar backup', self.restore, secondary=True).pack(side='left')
        label(data, 'A restauração substitui os dados atuais. Uma cópia de segurança é criada antes\n'
                    'da operação. Apenas bancos compatíveis e íntegros são aceitos.', 12,
              color=MUTED, wraplength=620, justify='left').pack(anchor='w', padx=20, pady=(0, 20))

    def save_settings(self):
        values = {k: v.get().strip() for k, v in self.settings_fields.items()}
        values['default_reminder'] = str(REMINDERS[values['default_reminder']])
        values['reminders_enabled'] = '1' if self.reminders_var.get() else '0'
        try:
            self.db.save_settings(values)
            ctk.set_appearance_mode(self.appearance(values))
            ctk.set_widget_scaling(self.scaling(values))
            self.render()
            self.feedback('Preferências salvas.')
        except Exception as exc:
            self.show_error(exc)

    def backup(self):
        destination = filedialog.asksaveasfilename(parent=self, title='Salvar backup da agenda', defaultextension='.db',
            initialfile=f'agenda-backup-{datetime.now():%Y%m%d-%H%M%S}.db', filetypes=[('Banco SQLite', '*.db')])
        if destination:
            try:
                self.db.backup(destination)
                self.feedback('Backup salvo com sucesso.')
            except Exception as exc:
                self.show_error(exc)

    def restore(self):
        source = filedialog.askopenfilename(parent=self, title='Selecionar backup da agenda',
                                           filetypes=[('Banco SQLite', '*.db'), ('Todos os arquivos', '*.*')])
        if source and messagebox.askyesno('Restaurar backup', 'Os dados atuais serão substituídos pelos dados deste backup.\n'
                    'Uma cópia dos dados atuais será salva antes.\n\nDeseja continuar?', parent=self):
            try:
                safety = self.db.restore(source)
                self.settings = self.db.get_settings()
                ctk.set_appearance_mode(self.appearance(self.settings))
                ctk.set_widget_scaling(self.scaling(self.settings))
                self.render()
                messagebox.showinfo('Backup restaurado', f'Dados restaurados.\n\nCópia anterior preservada em:\n{safety}', parent=self)
            except Exception as exc:
                self.show_error(exc)

    def feedback(self, text):
        self.status.configure(text='✓  ' + text, text_color=GREEN)
        if self._toast_job:
            self.after_cancel(self._toast_job)
        self._toast_job = self.after(7000, self.clear_feedback)

    def clear_feedback(self):
        self._toast_job = None
        self.status.configure(text='Pronto  ·  Seus dados ficam neste computador.', text_color=MUTED)

    def show_error(self, error, parent=None):
        logging.exception('Falha na operação da agenda: %s', error)
        detail = str(error) if isinstance(error, ValueError) else (
            'Não foi possível acessar o banco. Verifique se há espaço no disco e tente novamente.'
            if isinstance(error, (sqlite3.Error, OSError)) else 'Não foi possível concluir a operação. Consulte agenda.log na pasta dos dados.')
        messagebox.showerror('Não foi possível concluir', detail, parent=parent or self)

    def tick(self):
        if self._closed:
            return
        if self.current_view == 'dashboard' and not (self._dialog and self._dialog.winfo_exists()):
            self.render()
        elif date.today() != self._last_day and self.current_view == 'calendar':
            self.render()
        self._last_day = date.today()
        self._clock_job = self.after(60000, self.tick)

    def check_reminders(self):
        from .notifications import send_native
        import threading
        try:
            for event in self.service.due_reminders():
                if not self.service.claim_reminder(event):
                    continue
                self.show_reminder(event)
                threading.Thread(target=send_native, args=(event,), daemon=True).start()
        except Exception:
            logging.exception('Falha ao verificar lembretes')
        finally:
            if not self._closed:
                self._reminder_job = self.after(30000, self.check_reminders)

    def show_reminder(self, event):
        panel = ctk.CTkToplevel(self)
        panel.title('Lembrete da agenda')
        panel.geometry('440x270')
        panel.resizable(False, False)
        panel.configure(fg_color=CARD)
        panel.attributes('-topmost', True)
        label(panel, 'É HORA DE SE PREPARAR', 11, True, color=BLUE).pack(anchor='w', padx=24, pady=(22, 8))
        label(panel, event['title'][:130], 19, True, wraplength=390, justify='left').pack(anchor='w', padx=24)
        label(panel, f'{format_date(event["event_date"])} às {event["event_time"]}\n{event["location"][:150]}',
              13, color=MUTED, wraplength=390, justify='left').pack(anchor='w', padx=24, pady=8)
        button(panel, 'Entendido', panel.destroy, width=110).pack(anchor='e', padx=24, pady=(5, 15))
        try:
            self.bell()
        except tk.TclError:
            logging.exception('Unable to play reminder sound')
        self.feedback('Lembrete: ' + event['title'])

    def close(self):
        self._closed = True
        self.close_event_menu()
        for job in (self._clock_job, self._reminder_job, self._search_job, self._toast_job):
            if job:
                self.after_cancel(job)
        self.destroy()
