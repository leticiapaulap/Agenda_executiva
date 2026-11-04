"""Menu contextual discreto, compartilhado pelas listas de compromissos."""
import logging
import tkinter as tk

import customtkinter as ctk

from .theme import BORDER, CARD, INK, MUTED, RED, SOFT, font, label


class EventActionMenu(ctk.CTkToplevel):
    def __init__(self, app, event, anchor):
        super().__init__(app)
        self.app, self.event_id, self.anchor = app, event['id'], anchor
        self._focus_job = None
        self._show_job = None
        self._opening = True
        self.buttons = {}
        self.withdraw()
        self.overrideredirect(True)
        self.transient(app)
        self.resizable(False, False)
        self.configure(fg_color=CARD)
        body = ctk.CTkFrame(self, fg_color=CARD, corner_radius=11,
                            border_width=1, border_color=BORDER)
        body.pack(fill='both', expand=True)
        label(body, 'AÇÕES DO COMPROMISSO', 10, True, color=MUTED).pack(anchor='w', padx=15, pady=(11, 5))
        options = [('edit', '✎', 'Editar'),
                   ('toggle', '↺' if event['completed'] else '✓', 'Reabrir' if event['completed'] else 'Concluir'),
                   ('reschedule', '↷', 'Reagendar'), ('duplicate', '▣', 'Duplicar'), ('delete', '×', 'Excluir')]
        for action, icon, text in options:
            if action == 'delete':
                ctk.CTkFrame(body, height=1, fg_color=BORDER).pack(fill='x', padx=12, pady=4)
            event_id = self.event_id
            item = ctk.CTkButton(body, text=f'{icon}    {text}', anchor='w', height=34,
                                 fg_color='transparent', hover_color=SOFT, corner_radius=7,
                                 text_color=RED if action == 'delete' else INK, font=font(13),
                                 command=lambda key=action, record_id=event_id:
                                 self.dispatch_action(key, record_id))
            item.pack(fill='x', padx=6, pady=(0, 3 if action != 'delete' else 7))
            self.buttons[action] = item
        # Os tamanhos CTk são lógicos; a posição é calculada após a aplicação do DPI.
        ratio = ctk.ScalingTracker.get_widget_scaling(app) / self._get_window_scaling()
        self.geometry(f'{int(224 * ratio)}x{int(254 * ratio)}')
        self.update_idletasks()
        width, height = self.winfo_width(), self.winfo_height()
        x = max(0, min(anchor.winfo_rootx() + anchor.winfo_width() - width,
                       self.winfo_screenwidth() - width))
        y = anchor.winfo_rooty() + anchor.winfo_height() + 4
        if y + height > self.winfo_screenheight() - 12:
            y = anchor.winfo_rooty() - height - 4
        self.geometry(f'+{x}+{max(0, y)}')
        self.bind('<Escape>', self.dismiss)
        self.bind('<FocusOut>', self.schedule_focus_check)
        self.bind('<Down>', lambda e: self.move_focus(1))
        self.bind('<Up>', lambda e: self.move_focus(-1))
        self.bind('<Return>', self.invoke_focused)
        self.bind('<space>', self.invoke_focused)
        self.deiconify()
        self.lift()
        self._show_job = self.after(30, self.focus_first)

    def dispatch_action(self, action, event_id):
        logging.info('Menu click action=%s event_id=%s app_db=%s menu=%s',
                     action, event_id, self.app.db.path, self.winfo_exists())
        self.app.perform_event_action(action, event_id)

    def focus_first(self):
        self._show_job = None
        if self.winfo_exists():
            self.focus_force()
            self.buttons['edit'].focus_set()
            self._opening = False
            logging.info('Action menu focused event_id=%s viewable=%s focused=%s',
                         self.event_id, self.winfo_viewable(), self.focus_get())

    def move_focus(self, direction):
        items = list(self.buttons.values())
        focused = self.focused_button()
        index = items.index(focused) if focused in items else 0
        items[(index + direction) % len(items)].focus_set()
        return 'break'

    def invoke_focused(self, _):
        widget = self.focused_button()
        if widget in self.buttons.values():
            widget.invoke()
        return 'break'

    def focused_button(self):
        # CTkButton transfere o foco ao Label Tcl interno, não ao Frame CTk.
        focused = self.focus_get()
        while focused is not None and focused is not self:
            if focused in self.buttons.values():
                return focused
            focused = getattr(focused, 'master', None)
        return None

    def schedule_focus_check(self, _):
        if self._opening:
            logging.debug('Ignoring FocusOut while opening menu event_id=%s', self.event_id)
            return
        if self._focus_job:
            self.after_cancel(self._focus_job)
        self._focus_job = self.after_idle(self.check_focus)

    def check_focus(self):
        self._focus_job = None
        try:
            focused = self.focus_get()
            logging.debug('Action menu focus check event_id=%s focused=%s',
                          self.event_id, focused)
            if focused is None or focused.winfo_toplevel() is not self:
                self.destroy()
        except tk.TclError:
            self.destroy()

    def dismiss(self, _=None):
        anchor = self.anchor
        self.destroy()
        if anchor.winfo_exists():
            anchor.focus_set()
        return 'break'

    def destroy(self):
        for job in (self._focus_job, self._show_job):
            if job:
                self.after_cancel(job)
        if getattr(self.app, '_event_menu', None) is self:
            self.app._event_menu = None
        super().destroy()
