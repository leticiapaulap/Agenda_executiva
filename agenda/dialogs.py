"""Formulários modais: capturam dados; validação e persistência ficam no serviço."""
from datetime import date
import logging
from tkinter import messagebox

import customtkinter as ctk

from .services import CATEGORIES, PRIORITIES, REMINDERS, format_date
from .theme import BG, CARD, INK, MUTED, BORDER, RED, font, label, button, asset_path, OptionMenu


class RecordDialog(ctk.CTkToplevel):
    def __init__(self, parent, kind='event', record=None, initial_date=None, mode='edit'):
        if mode not in ('edit', 'duplicate', 'reschedule'):
            raise ValueError('A operação solicitada não é válida.')
        if mode != 'edit' and (kind != 'event' or record is None):
            raise ValueError('Selecione um compromisso para esta operação.')
        super().__init__(parent)
        self.app, self.kind, self.mode = parent, kind, mode
        self.source_record = dict(record) if record is not None else None
        self.record = None if mode == 'duplicate' else self.source_record
        self.seed_record = dict(self.source_record) if self.source_record is not None else None
        if mode == 'duplicate':
            self.seed_record['title'] += ' (cópia)'
            self.seed_record['completed'] = 0
        self.saved = False
        self.saved_id = None
        self.inputs = {}
        title = {'duplicate': 'Duplicar compromisso', 'reschedule': 'Reagendar compromisso'}.get(
            mode, ('Editar' if record else 'Novo') + (' compromisso' if kind == 'event' else ' tarefa'))
        self.title(title)
        self.configure(fg_color=BG)
        requested_height = 510 if mode == 'reschedule' else 720
        width, height = 670, min(requested_height, max(480, self.winfo_screenheight() - 110))
        self.geometry(f'{width}x{height}+{max(0, (self.winfo_screenwidth()-width)//2)}+{max(0, (self.winfo_screenheight()-height)//2-20)}')
        self.minsize(560, 480)
        self.transient(parent)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color='transparent')
        head.grid(row=0, column=0, sticky='ew', padx=28, pady=(22, 12))
        label(head, title, 24, True).pack(anchor='w')
        subtitle = ('A cópia será criada somente ao salvar.' if mode == 'duplicate' else
                    'Altere a data e os horários; os demais dados serão preservados.' if mode == 'reschedule' else
                    'Os campos com * são obrigatórios.')
        label(head, subtitle, 12, color=MUTED, wraplength=580, justify='left').pack(anchor='w', pady=(4, 0))
        self.body = ctk.CTkScrollableFrame(self, fg_color='transparent', corner_radius=0)
        self.body.grid(row=1, column=0, sticky='nsew', padx=22)
        self.body.grid_columnconfigure((0, 1), weight=1, uniform='form')
        if mode == 'reschedule':
            label(self.body, self.source_record['title'], 18, True, wraplength=550,
                  justify='left').grid(row=0, column=0, columnspan=2, sticky='w', padx=5, pady=(8, 15))
            self.entry('Data * · DD/MM/AAAA', 'event_date', 1, span=2)
            self.entry('Início * · HH:MM', 'event_time', 2)
            self.entry('Término * · HH:MM', 'end_time', 2, 1)
            label(self.body, 'Os lembretes acompanharão o novo horário.', 12, color=MUTED,
                  wraplength=550, justify='left').grid(row=3, column=0, columnspan=2, sticky='w', padx=5, pady=12)
        else:
            self.entry('Título *', 'title', 0, span=2)
            self.textbox('Descrição', 'description', 1)
        if kind == 'event' and mode != 'reschedule':
            self.entry('Data * · DD/MM/AAAA', 'event_date', 2, default=format_date(initial_date or date.today()))
            self.select('Categoria', 'category', 2, 1, CATEGORIES, 'Reunião')
            self.entry('Início * · HH:MM', 'event_time', 3, default='09:00')
            default_end = ''
            if not record:
                default_end = '10:00'
            elif record.get('end_time'):
                default_end = record['end_time']
            self.entry('Término * · HH:MM', 'end_time', 3, 1, default=default_end)
            self.entry('Local / link', 'location', 4, span=2)
            self.select('Prioridade', 'priority', 5, 0, PRIORITIES, 'Normal')
            try:
                minutes = int(record['reminder_minutes'] if record else parent.settings.get('default_reminder', '15'))
            except (ValueError, TypeError):
                minutes = 15
            selected = next((key for key, val in REMINDERS.items() if val == minutes), 'Sem lembrete')
            self.select('Lembrete', 'reminder_minutes', 5, 1, list(REMINDERS), selected, raw=True)
            self.textbox('Observações', 'notes', 6)
            label(self.body, 'Lembretes funcionam enquanto a agenda estiver aberta.', 12,
                  color=MUTED, wraplength=520, anchor='w').grid(row=7, column=0, columnspan=2, sticky='w', padx=5, pady=8)
            if record and not record.get('end_time'):
                label(self.body, 'Registro anterior: informe o término para atualizar.', 12,
                      color=MUTED).grid(row=8, column=0, columnspan=2, sticky='w', padx=5)
        elif kind == 'task':
            self.entry('Prazo · DD/MM/AAAA (opcional)', 'due_date', 2)
            self.select('Prioridade', 'priority', 2, 1, PRIORITIES, 'Normal')
        foot = ctk.CTkFrame(self, fg_color='transparent')
        foot.grid(row=2, column=0, sticky='ew', padx=28, pady=(10, 22))
        self.error = label(foot, '', 12, color=RED, wraplength=580, anchor='w', justify='left')
        self.error.pack(fill='x', pady=(0, 8))
        save_text = {'duplicate': 'Salvar cópia', 'reschedule': 'Salvar reagendamento'}.get(
            mode, 'Salvar compromisso' if kind == 'event' else 'Salvar tarefa')
        button(foot, save_text, self.save).pack(side='right')
        button(foot, 'Cancelar', self.destroy, secondary=True, width=100).pack(side='right', padx=10)
        self.bind('<Escape>', lambda _: self.destroy())
        self.bind('<Control-Return>', lambda _: self.save())
        self.after(100, self.activate)
        logging.info('RecordDialog constructed mode=%s kind=%s event_id=%s window=%s parent=%s',
                     mode, kind, (record or {}).get('id'), self, parent)

    def activate(self):
        self.grab_set()
        self.lift(self.app)
        self.focus_force()
        self.inputs['event_date' if self.mode == 'reschedule' else 'title'].focus_set()
        logging.info('RecordDialog activated mode=%s event_id=%s viewable=%s focused=%s grab=%s',
                     self.mode, (self.record or {}).get('id'), self.winfo_viewable(),
                     self.focus_get(), self.grab_current())
        try:
            self.iconbitmap(str(asset_path('app.ico')))
        except Exception:
            logging.exception('Unable to set icon for record dialog')

    def value(self, key, default=''):
        value = self.seed_record.get(key, default) if self.seed_record else default
        if key in ('event_date', 'due_date') and value:
            return format_date(value)
        return value or ''

    def field_box(self, title, row, col, span):
        box = ctk.CTkFrame(self.body, fg_color='transparent')
        box.grid(row=row, column=col, columnspan=span, sticky='ew', padx=5, pady=7)
        label(box, title, 12, True).pack(anchor='w', pady=(0, 5))
        return box

    def entry(self, title, key, row, col=0, span=1, default=''):
        field = ctk.CTkEntry(self.field_box(title, row, col, span), height=38,
                             font=font(), border_color=BORDER)
        field.insert(0, self.value(key, default))
        field.pack(fill='x')
        self.inputs[key] = field

    def select(self, title, key, row, col, values, default, raw=False):
        values = list(values)
        current = default if raw else self.value(key, default)
        if current not in values:
            values.append(current)
        field = OptionMenu(self.field_box(title, row, col, 1), values=values,
                                 height=38, font=font(13), dynamic_resizing=False)
        field.set(current)
        field.pack(fill='x')
        self.inputs[key] = field

    def textbox(self, title, key, row):
        field = ctk.CTkTextbox(self.field_box(title, row, 0, 2), height=80, font=font(),
                              border_width=1, border_color=BORDER, text_color=INK, wrap='word')
        field.insert('1.0', self.value(key))
        field.pack(fill='x')
        self.inputs[key] = field

    def save(self):
        data = {key: (field.get('1.0', 'end-1c') if isinstance(field, ctk.CTkTextbox) else field.get())
                for key, field in self.inputs.items()}
        record_id = self.record['id'] if self.record else None
        logging.info('Save requested kind=%s mode=%s event_id=%s db=%s fields=%s',
                     self.kind, self.mode, record_id, self.app.db.path, sorted(data))
        try:
            if self.kind == 'event':
                if self.mode == 'reschedule':
                    current = self.app.db.get_event(record_id)
                    if current is None:
                        raise ValueError('O compromisso não foi encontrado. Atualize a agenda.')
                    data = {**current, **data}
                elif self.mode == 'duplicate':
                    data['completed'] = 0
                if not data['end_time'].strip():
                    raise ValueError('Informe o horário de término do compromisso.')
                if self.mode != 'reschedule':
                    data['reminder_minutes'] = REMINDERS[data['reminder_minutes']]
                validated = self.app.service.validate_event(data)
                conflicts = self.app.service.conflicts(validated, exclude_id=record_id)
                if conflicts:
                    lines = '\n'.join(f'• {e["event_time"]} — {e["title"]}' for e in conflicts[:5])
                    if not messagebox.askyesno('Conflito de horários',
                            f'Já existem compromissos nesse período:\n\n{lines}\n\nDeseja salvar mesmo assim?', parent=self):
                        return
                self.saved_id = self.app.service.save_event(data, event_id=record_id)
                logging.info('%s event succeeded event_id=%s saved_id=%s db=%s',
                             'UPDATE' if record_id is not None else 'INSERT',
                             record_id, self.saved_id, self.app.db.path)
            else:
                self.saved_id = self.app.service.save_task(data, task_id=record_id)
        except ValueError as exc:
            logging.exception('Save validation failed kind=%s mode=%s event_id=%s',
                              self.kind, self.mode, record_id)
            self.error.configure(text=str(exc))
            return
        except Exception as exc:
            logging.exception('Save failed kind=%s mode=%s event_id=%s db=%s',
                              self.kind, self.mode, record_id, self.app.db.path)
            self.app.show_error(exc, parent=self)
            return
        self.saved = True
        self.destroy()
        self.app.render()
        logging.info('Record dialog saved; interface refreshed kind=%s mode=%s id=%s view=%s',
                     self.kind, self.mode, self.saved_id, self.app.current_view)
        feedback = {'duplicate': 'Cópia do compromisso salva.', 'reschedule': 'Compromisso reagendado.'}.get(
            self.mode, 'Compromisso salvo.' if self.kind == 'event' else 'Tarefa salva.')
        self.app.feedback(feedback)


class DeleteEventDialog(ctk.CTkToplevel):
    def __init__(self, parent, event):
        super().__init__(parent)
        self.app, self.event_id = parent, event['id']
        self.title('Excluir compromisso')
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.transient(parent)
        width, height = 440, 210
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.geometry(f'{width}x{height}+{max(0, x)}+{max(0, y)}')
        body = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14,
                            border_width=1, border_color=BORDER)
        body.pack(fill='both', expand=True, padx=14, pady=14)
        label(body, 'Deseja realmente excluir o compromisso?', 15, True,
              wraplength=380, justify='left').pack(anchor='w', padx=18, pady=(18, 5))
        label(body, event['title'], 14, color=INK, wraplength=380,
              justify='left').pack(anchor='w', padx=18)
        actions = ctk.CTkFrame(body, fg_color='transparent')
        actions.pack(fill='x', padx=18, pady=(18, 14))
        self.buttons = {
            'delete': ctk.CTkButton(actions, text='Excluir', command=self.confirm, font=font(13, True),
                                    height=38, corner_radius=9, fg_color=RED, text_color='white',
                                    hover_color=('#9F3044', '#D66E80')),
            'cancel': button(actions, 'Cancelar', self.destroy, secondary=True, width=100),
        }
        self.buttons['delete'].pack(side='right')
        self.buttons['cancel'].pack(side='right', padx=10)
        self.bind('<Escape>', lambda _: self.destroy())
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.after(50, self.activate)
        logging.info('DeleteEventDialog constructed event_id=%s title=%r window=%s parent=%s',
                     self.event_id, event['title'], self, parent)

    def activate(self):
        if self.winfo_exists():
            self.grab_set()
            self.lift(self.app)
            self.focus_force()
            self.buttons['cancel'].focus_set()
            logging.info('DeleteEventDialog activated event_id=%s viewable=%s focused=%s grab=%s',
                         self.event_id, self.winfo_viewable(), self.focus_get(),
                         self.grab_current())

    def confirm(self):
        logging.info('Delete confirmation clicked event_id=%s', self.event_id)
        self.app.confirm_delete_event(self.event_id, self)

    def destroy(self):
        if getattr(self.app, '_delete_dialog', None) is self:
            self.app._delete_dialog = None
        super().destroy()


class EventDetailsDialog(ctk.CTkToplevel):
    """Consulta somente leitura; nenhuma seleção ou abertura modifica registros."""

    def __init__(self, parent, event):
        super().__init__(parent)
        self.app = parent
        self.event = dict(event)
        self.title('Detalhes do compromisso')
        self.configure(fg_color=BG)
        width, height = 670, min(720, max(480, self.winfo_screenheight() - 110))
        self.geometry(f'{width}x{height}+{max(0, (self.winfo_screenwidth()-width)//2)}+{max(0, (self.winfo_screenheight()-height)//2-20)}')
        self.minsize(560, 480)
        self.transient(parent)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        label(self, 'Detalhes do compromisso', 24, True).grid(row=0, column=0, sticky='w', padx=28, pady=(24, 16))
        body = ctk.CTkScrollableFrame(self, fg_color='transparent', corner_radius=0)
        body.grid(row=1, column=0, sticky='nsew', padx=22)
        body.grid_columnconfigure(0, weight=1)
        reminder = next((key for key, value in REMINDERS.items()
                         if str(value) == str(event.get('reminder_minutes', -1))), 'Sem lembrete')
        fields = [
            ('Título', event['title']),
            ('Data', format_date(event['event_date'])),
            ('Início', event['event_time']),
            ('Término', event.get('end_time') or 'Não informado'),
            ('Categoria', event.get('category') or 'Compromisso'),
            ('Prioridade', event.get('priority') or 'Normal'),
            ('Status', 'Concluído' if event.get('completed') else 'Pendente'),
            ('Local / link', event.get('location') or 'Não informado'),
            ('Lembrete', reminder),
            ('Descrição', event.get('description') or 'Nenhuma descrição.'),
            ('Observações', event.get('notes') or 'Nenhuma observação.'),
        ]
        for row, (title, value) in enumerate(fields):
            box = ctk.CTkFrame(body, fg_color='transparent')
            box.grid(row=row, column=0, sticky='ew', padx=5, pady=(4, 13))
            label(box, title, 12, True, color=MUTED).pack(anchor='w')
            label(box, str(value), 15 if title == 'Título' else 14, title == 'Título',
                  wraplength=530, anchor='w', justify='left').pack(anchor='w', pady=(4, 0))
        foot = ctk.CTkFrame(self, fg_color='transparent')
        foot.grid(row=2, column=0, sticky='ew', padx=28, pady=(14, 22))
        button(foot, 'Editar compromisso', self.edit).pack(side='right')
        button(foot, 'Fechar', self.destroy, secondary=True, width=100).pack(side='right', padx=10)
        self.bind('<Escape>', lambda _: self.destroy())
        self.after(100, self.activate)
        logging.info('EventDetailsDialog constructed event_id=%s title=%r window=%s parent=%s',
                     event['id'], event['title'], self, parent)

    def activate(self):
        self.grab_set()
        self.lift(self.app)
        self.focus_force()
        self.focus_set()
        logging.info('EventDetailsDialog activated event_id=%s viewable=%s focused=%s grab=%s',
                     self.event['id'], self.winfo_viewable(), self.focus_get(),
                     self.grab_current())
        try:
            self.iconbitmap(str(asset_path('app.ico')))
        except Exception:
            logging.exception('Unable to set icon for event details dialog')

    def edit(self):
        try:
            current = self.app.db.get_event(self.event['id'])
            if current is None:
                raise ValueError('O compromisso não foi encontrado. Atualize a agenda.')
        except Exception as exc:
            self.app.show_error(exc, parent=self)
            return
        self.destroy()
        self.app.open_dialog(record=current, kind='event')
