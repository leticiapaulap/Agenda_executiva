"""Telas locais de contas e catálogo; dados e validações ficam nos serviços."""
from datetime import date
from pathlib import Path
import os
import hashlib
import hmac
import time
from tkinter import filedialog, messagebox

import customtkinter as ctk
from werkzeug.security import check_password_hash

from .services import format_date, parse_date
from .theme import BG, CARD, MUTED, RED, GREEN, BLUE, font, label, button, card, OptionMenu


CORPORATE_SCOPE = 'Corporativo / somente catálogo'
PERSONAL_SCOPE = 'Pessoal / armazenamento autorizado'
PASSWORD_MASK = '••••••••'


def cancel_dialog_jobs(dialog):
    # CTk 5.2.2 schedules icon/focus callbacks without cancelling them on destroy.
    # Cancel only callbacks registered by this dialog; leave other windows alone.
    commands = set(dialog._tclCommands or ())
    for job in dialog.tk.splitlist(dialog.tk.call('after', 'info')):
        details = dialog.tk.call('after', 'info', job)
        if details[0] in commands:
            dialog.after_cancel(job)


def reais(cents):
    whole, fraction = divmod(int(cents), 100)
    return f'R$ {whole:,}'.replace(',', '.') + f',{fraction:02d}'


class FeatureDialog(ctk.CTkToplevel):
    """Formulário modal, sem campos de credenciais no modo catálogo."""
    def __init__(self, app, kind, record=None):
        super().__init__(app)
        self.app, self.kind = app, kind
        self.record = record or {}
        self.inputs = {}
        self.saved = False
        self.saved_id = None
        title = ('Editar' if record else 'Cadastrar') + (' conta' if kind == 'bill' else ' acesso')
        self.title(title)
        self.configure(fg_color=BG)
        self.geometry(f'620x{min(740, self.winfo_screenheight() - 100)}')
        self.minsize(520, 480)
        self.transient(app)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        label(self, title, 24, True).grid(row=0, column=0, sticky='w', padx=26, pady=(22, 12))
        self.body = ctk.CTkScrollableFrame(self, fg_color='transparent')
        self.body.grid(row=1, column=0, sticky='nsew', padx=20)
        self.body.grid_columnconfigure(0, weight=1)
        if kind == 'bill':
            fields = [('Nome da conta *', 'title', ''), ('Categoria', 'category', ''),
                      ('Valor em reais * · 123,45', 'amount', ''),
                      ('Vencimento * · DD/MM/AAAA', 'due_date', format_date(date.today())),
                      ('Responsável', 'responsible', '')]
        else:
            label(self.body, 'Senhas pessoais exigem chave exclusiva e autenticação local configuradas. '
                  'Acessos corporativos permanecem somente no catálogo.', 12, color=MUTED,
                  wraplength=510, justify='left').grid(row=0, column=0, sticky='ew', pady=(0, 10))
            fields = [('Sistema ou serviço *', 'name', ''), ('URL pública de acesso', 'url', ''),
                      ('Categoria', 'category', '')]
        offset = 0 if kind == 'bill' else 1
        for row, (caption, key, default) in enumerate(fields, offset):
            box = ctk.CTkFrame(self.body, fg_color='transparent')
            box.grid(row=row, column=0, sticky='ew', pady=5)
            label(box, caption, 12, True).pack(anchor='w', pady=(0, 4))
            entry = ctk.CTkEntry(box, height=37, font=font(13))
            value = self.record.get(key, default)
            if key == 'due_date' and value:
                value = format_date(value)
            if key == 'amount' and value:
                value = str(value).replace('.', ',')
            entry.insert(0, str(value or ''))
            entry.pack(fill='x')
            self.inputs[key] = entry
        row = len(fields) + offset
        if kind == 'access':
            label(self.body, 'Tipo de acesso', 12, True).grid(row=row, column=0, sticky='w', pady=(8, 4))
            scope = OptionMenu(self.body, values=[CORPORATE_SCOPE, PERSONAL_SCOPE], height=37, font=font(13),
                               command=lambda _: self.update_password_state())
            scope.set(CORPORATE_SCOPE if self.record.get('is_corporate', True) else PERSONAL_SCOPE)
            scope.grid(row=row + 1, column=0, sticky='ew')
            self.inputs['is_corporate'] = scope
            label(self.body, 'Nova senha (opcional)', 12, True).grid(row=row + 2, column=0, sticky='w', pady=(10, 4))
            password = ctk.CTkEntry(self.body, height=37, font=font(13), show='•')
            password.grid(row=row + 3, column=0, sticky='ew')
            self.inputs['password'] = password
            label(self.body, 'Deixe em branco para manter a senha existente. '
                  'Nunca inclua credenciais nas observações.', 11, color=MUTED,
                  wraplength=510, justify='left').grid(row=row + 4, column=0, sticky='ew', pady=(5, 6))
            row += 5
            if self.record.get('has_password'):
                clear = ctk.CTkCheckBox(self.body, text='Remover a senha existente', font=font(12))
                clear.grid(row=row, column=0, sticky='w', pady=(3, 7))
                if not self.app.desktop_vault_available:
                    clear.configure(state='disabled')
                self.inputs['clear_password'] = clear
                row += 1
            self.update_password_state()
        if kind == 'bill':
            label(self.body, 'Recorrência', 12, True).grid(row=row, column=0, sticky='w', pady=(8, 4))
            menu = OptionMenu(self.body, values=['Única', 'Mensal'], height=37, font=font(13))
            menu.set(self.record.get('recurrence', 'Única'))
            menu.grid(row=row + 1, column=0, sticky='ew')
            self.inputs['recurrence'] = menu
            row += 2
            label(self.body, 'Alterações afetam esta ocorrência e as próximas ainda não pagas. A exclusão encerra as próximas '
                  'cobranças da série; o histórico pago é preservado.', 11, color=MUTED,
                  wraplength=510, justify='left').grid(row=row, column=0, sticky='ew', pady=(8, 4))
            row += 1
        label(self.body, 'Observações não sensíveis' if kind == 'access' else 'Observações',
              12, True).grid(row=row, column=0, sticky='w', pady=(8, 4))
        notes = ctk.CTkTextbox(self.body, height=100, font=font(13))
        notes.insert('1.0', self.record.get('notes', ''))
        notes.grid(row=row + 1, column=0, sticky='ew', pady=(0, 10))
        self.inputs['notes'] = notes
        foot = ctk.CTkFrame(self, fg_color='transparent')
        foot.grid(row=2, column=0, sticky='ew', padx=26, pady=(10, 20))
        self.error = label(foot, '', 12, color=RED, wraplength=540, justify='left')
        self.error.pack(fill='x', pady=(0, 8))
        button(foot, 'Salvar', self.save, width=110).pack(side='right')
        button(foot, 'Cancelar', self.destroy, secondary=True, width=110).pack(side='right', padx=10)
        self.bind('<Escape>', lambda _: self.destroy())
        self.bind('<Control-Return>', lambda _: self.save())
        self.after(100, self.activate)

    def activate(self):
        self.grab_set()
        self.lift(self.app)
        self.focus_force()
        self.inputs['title' if self.kind == 'bill' else 'name'].focus_set()

    def update_password_state(self):
        if 'password' not in self.inputs:
            return
        enabled = (self.app.desktop_vault_available and
                   self.inputs['is_corporate'].get() == PERSONAL_SCOPE)
        password = self.inputs['password']
        password.configure(state='normal')
        if not enabled:
            password.delete(0, 'end')
            password.configure(state='disabled')

    def save(self):
        values = {key: field.get('1.0', 'end-1c') if isinstance(field, ctk.CTkTextbox)
                  else field.get() for key, field in self.inputs.items()}
        if self.kind == 'access':
            values['is_corporate'] = values['is_corporate'] == CORPORATE_SCOPE
            values['clear_password'] = bool(values.get('clear_password', False))
            if values.get('password') or values.get('clear_password'):
                self.app.request_vault_auth(lambda: self.persist(values), parent=self)
                return
        self.persist(values)

    def persist(self, values):
        try:
            if self.kind == 'bill':
                values['due_date'] = parse_date(values['due_date']).isoformat()
                saved_id = self.app.finance.save_bill(values, bill_id=self.record.get('id'))
            else:
                saved_id = self.app.catalog.save_entry(values, entry_id=self.record.get('id'))
        except ValueError as exc:
            self.error.configure(text=str(exc))
            return
        except Exception as exc:
            if self.kind == 'access':
                self.error.configure(text='Não foi possível salvar o acesso. Verifique a configuração do cofre.')
                return
            self.app.show_error(exc, parent=self)
            return
        self.saved, self.saved_id = True, saved_id
        self.destroy()
        self.app.render()
        self.app.feedback('Registro salvo.')

    def destroy(self):
        password = self.inputs.get('password')
        if password is not None and password.winfo_exists():
            password.configure(state='normal')
            password.delete(0, 'end')
        cancel_dialog_jobs(self)
        super().destroy()


class VaultAuthDialog(ctk.CTkToplevel):
    """One authorization per sensitive action; never persists the entered password."""
    def __init__(self, app, callback, parent=None):
        super().__init__(parent or app)
        self.app, self.callback = app, callback
        self.title('Autenticar acesso ao cofre')
        self.geometry('500x300')
        self.resizable(False, False)
        self.configure(fg_color=CARD)
        self.transient(parent or app)
        label(self, 'Autenticar acesso ao cofre', 22, True).pack(anchor='w', padx=24, pady=(22, 12))
        label(self, 'Senha de autenticação local do cofre', 12, color=MUTED).pack(anchor='w', padx=24)
        self.password = ctk.CTkEntry(self, height=38, font=font(13), show='•')
        self.password.pack(fill='x', padx=24, pady=(6, 8))
        self.error = label(self, '', 12, color=RED, wraplength=450, justify='left')
        self.error.pack(fill='x', padx=24)
        actions = ctk.CTkFrame(self, fg_color='transparent')
        actions.pack(fill='x', padx=24, pady=14)
        button(actions, 'Confirmar', self.confirm, width=120).pack(side='right')
        button(actions, 'Cancelar', self.destroy, secondary=True, width=110).pack(side='right', padx=10)
        self.bind('<Return>', lambda _: self.confirm())
        self.bind('<Escape>', lambda _: self.destroy())
        self.after(100, self.activate)

    def activate(self):
        self.grab_set()
        self.lift()
        self.password.focus_set()

    def confirm(self):
        entered = self.password.get()
        self.password.delete(0, 'end')
        try:
            valid, message = self.app.check_desktop_vault_password(entered)
        except Exception:
            valid, message = False, 'Não foi possível autenticar o acesso ao cofre.'
        entered = None
        if not valid:
            self.error.configure(text=message)
            return
        callback = self.callback
        self.callback = None
        self.destroy()
        callback()

    def destroy(self):
        self.callback = None
        if self.password.winfo_exists():
            self.password.delete(0, 'end')
        cancel_dialog_jobs(self)
        super().destroy()


class PaymentDialog(ctk.CTkToplevel):
    def __init__(self, app, bill):
        super().__init__(app)
        self.app, self.bill = app, bill
        self.saved = False
        self.title('Registrar pagamento')
        self.configure(fg_color=CARD)
        self.geometry('520x340')
        self.resizable(False, False)
        self.transient(app)
        label(self, 'Registrar pagamento', 23, True).pack(anchor='w', padx=24, pady=(22, 12))
        label(self, bill['title'], 15, True, wraplength=470, justify='left').pack(anchor='w', padx=24)
        label(self, reais(bill['amount_cents']), 14, color=GREEN).pack(anchor='w', padx=24, pady=(3, 12))
        label(self, 'Data do pagamento · DD/MM/AAAA', 12, True).pack(anchor='w', padx=24)
        self.paid_on = ctk.CTkEntry(self, height=38, font=font(13))
        self.paid_on.insert(0, format_date(date.today()))
        self.paid_on.pack(fill='x', padx=24, pady=(5, 8))
        self.error = label(self, '', 12, color=RED, wraplength=470, justify='left')
        self.error.pack(fill='x', padx=24)
        actions = ctk.CTkFrame(self, fg_color='transparent')
        actions.pack(fill='x', padx=24, pady=(8, 18))
        button(actions, 'Confirmar pagamento', self.save, width=180).pack(side='right')
        button(actions, 'Cancelar', self.destroy, secondary=True, width=110).pack(side='right', padx=10)
        self.bind('<Escape>', lambda _: self.destroy())
        self.bind('<Return>', lambda _: self.save())
        self.after(100, self.activate)

    def activate(self):
        self.grab_set()
        self.lift(self.app)
        self.paid_on.focus_set()

    def save(self):
        try:
            self.app.finance.pay_bill(self.bill['id'], parse_date(self.paid_on.get()).isoformat())
        except ValueError as exc:
            self.error.configure(text=str(exc))
            return
        except Exception as exc:
            self.app.show_error(exc, parent=self)
            return
        self.saved = True
        self.destroy()
        self.app.render()
        self.app.feedback('Pagamento registrado.')


class FeatureViews:
    def initialize_desktop_vault(self):
        """Require independent encryption and local reauthentication configuration."""
        from .catalog import AccessCatalog, SQLiteCatalogRepository
        from .vault import VaultCipher
        password_hash = os.environ.get('VAULT_DESKTOP_PASSWORD_HASH', '')
        self._desktop_vault_password_hash = password_hash if (
            len(password_hash) <= 4096 and password_hash.startswith(('scrypt:', 'pbkdf2:'))
            and len(password_hash.split('$')) == 3) else ''
        cipher = VaultCipher() if self._desktop_vault_password_hash else VaultCipher({'VAULT_ENABLED': False})
        self.desktop_vault_available = bool(cipher.available and self._desktop_vault_password_hash)
        self.catalog = AccessCatalog(SQLiteCatalogRepository(self.db), cipher=cipher)
        self._vault_visible = {}
        self._vault_hide_jobs = {}
        self._vault_clipboard_job = None
        self._vault_clipboard_digest = None
        self._vault_auth_dialog = None
        self._vault_labels = {}

    def check_desktop_vault_password(self, password):
        if not self.desktop_vault_available:
            return False, 'Cofre indisponível. Configure a chave exclusiva e a autenticação local.'
        now = int(time.time())
        with self.db._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            settings = dict(connection.execute("SELECT key,value FROM settings WHERE key LIKE 'vault_auth_%'"))
            def integer(key):
                try:
                    return int(settings.get(key, 0))
                except (TypeError, ValueError):
                    return 0
            locked_until = integer('vault_auth_locked_until')
            failures = integer('vault_auth_failures')
            started = integer('vault_auth_window')
            if locked_until > now:
                return False, 'Muitas tentativas. Aguarde 15 minutos.'
            if now - started >= 900:
                failures, started = 0, now
            try:
                valid = (isinstance(password, str) and len(password) <= 1024 and
                         check_password_hash(self._desktop_vault_password_hash, password))
            except (ValueError, TypeError):
                valid = False
            if valid:
                failures, locked_until = 0, 0
            else:
                failures += 1
                if failures >= 5:
                    locked_until = now + 900
            connection.executemany('INSERT INTO settings(key,value) VALUES (?,?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                [('vault_auth_failures', str(failures)), ('vault_auth_window', str(started)),
                 ('vault_auth_locked_until', str(locked_until))])
        return (True, '') if valid else (False, 'Muitas tentativas. Aguarde 15 minutos.'
                                         if locked_until > now else 'Autenticação inválida.')

    def request_vault_auth(self, callback, parent=None):
        if not self.desktop_vault_available:
            self.feedback('Cofre indisponível. Configure a chave exclusiva e a autenticação local.')
            return
        if self._vault_auth_dialog is not None and self._vault_auth_dialog.winfo_exists():
            self._vault_auth_dialog.focus_force()
            return
        self._vault_auth_dialog = VaultAuthDialog(self, callback, parent)

    def hide_vault_password(self, entry_id):
        widget = self._vault_visible.pop(entry_id, None)
        if widget is not None and widget.winfo_exists():
            widget.configure(text=PASSWORD_MASK)
        job = self._vault_hide_jobs.pop(entry_id, None)
        if job:
            self.after_cancel(job)

    def clear_vault_clipboard(self):
        digest = self._vault_clipboard_digest
        self._vault_clipboard_digest = None
        if self._vault_clipboard_job:
            self.after_cancel(self._vault_clipboard_job)
            self._vault_clipboard_job = None
        if digest is not None:
            try:
                current = self.clipboard_get()
                if hmac.compare_digest(hashlib.sha256(current.encode('utf-8')).digest(), digest):
                    self.clipboard_clear()
            except Exception:
                pass

    def clear_vault_transients(self):
        for entry_id in list(self._vault_visible):
            self.hide_vault_password(entry_id)
        self.clear_vault_clipboard()
        self._vault_labels.clear()
        if self._vault_auth_dialog is not None and self._vault_auth_dialog.winfo_exists():
            self._vault_auth_dialog.destroy()

    def toggle_vault_password(self, entry, widget):
        if entry['id'] in self._vault_visible:
            self.hide_vault_password(entry['id'])
            return
        def reveal():
            if not widget.winfo_exists() or self.current_view != 'catalog':
                return
            try:
                password = self.catalog.reveal_password(entry['id'])
                widget.configure(text=password)
                password = None
                self._vault_visible[entry['id']] = widget
                self._vault_hide_jobs[entry['id']] = self.after(30000,
                    lambda: self.hide_vault_password(entry['id']))
            except Exception:
                widget.configure(text=PASSWORD_MASK)
                self.feedback('Não foi possível revelar a senha. Verifique as permissões e as chaves do cofre.')
        self.request_vault_auth(reveal)

    def copy_vault_password(self, entry):
        def copy():
            try:
                password = self.catalog.reveal_password(entry['id'])
                self.clear_vault_clipboard()
                self.clipboard_clear()
                self.clipboard_append(password)
                self._vault_clipboard_digest = hashlib.sha256(password.encode('utf-8')).digest()
                password = None
                self._vault_clipboard_job = self.after(30000, self.clear_vault_clipboard)
                self.feedback('Senha copiada. A cópia será removida em até 30 segundos, se não for substituída.')
            except Exception:
                self.feedback('Não foi possível copiar a senha. Verifique as permissões e as chaves do cofre.')
        self.request_vault_auth(copy)

    def open_feature_dialog(self, kind, record=None):
        if self._dialog is not None and self._dialog.winfo_exists():
            self._dialog.focus_force()
            return
        self._dialog = FeatureDialog(self, kind, record)

    def catalog_view(self):
        warning = card(self.content)
        warning.grid(row=0, column=0, sticky='ew', pady=(0, 16))
        self.section_title(warning, 'Cofre de Senhas' if self.desktop_vault_available else 'Catálogo de acessos · cofre indisponível',
                           'Senhas pessoais são criptografadas e exigem autenticação antes de salvar, revelar ou copiar. '
                           'Acessos corporativos permanecem somente no catálogo.' if self.desktop_vault_available else
                           'Para armazenar senhas pessoais, configure a chave exclusiva e o hash de autenticação local. '
                           'Dados e senhas existentes permanecem preservados. Acessos corporativos ficam no catálogo.')
        filters = card(self.content)
        filters.grid(row=1, column=0, sticky='ew', pady=(0, 16))
        filters.grid_columnconfigure(0, weight=1)
        search = ctk.CTkEntry(filters, placeholder_text='Pesquisar nome ou categoria', height=38, font=font(13))
        search.insert(0, self._catalog_search)
        search.grid(row=0, column=0, sticky='ew', padx=16, pady=16)
        def apply():
            self._catalog_search = search.get().strip()
            self.render()
        search.bind('<Return>', lambda _: apply())
        button(filters, 'Pesquisar', apply, width=110).grid(row=0, column=1, padx=(0, 16))
        self.search_entry = search
        rows = self.catalog.list_entries(search=self._catalog_search)
        result = card(self.content)
        result.grid(row=2, column=0, sticky='ew')
        self.section_title(result, f'{len(rows)} acesso(s)')
        for item in rows:
            row = ctk.CTkFrame(result, fg_color='transparent')
            row.pack(fill='x', padx=20, pady=(0, 15))
            row.grid_columnconfigure(0, weight=1)
            label(row, item['name'], 15, True, wraplength=480, justify='left').grid(row=0, column=0, sticky='w')
            label(row, item.get('category', '') or 'Sem categoria', 12, color=MUTED).grid(row=1, column=0, sticky='w')
            label(row, item.get('url', ''), 12, color=BLUE, wraplength=480,
                  justify='left').grid(row=2, column=0, sticky='w')
            if item.get('notes'):
                label(row, item['notes'], 12, color=MUTED, wraplength=480,
                      justify='left').grid(row=3, column=0, sticky='w')
            label(row, 'Atualizado em ' + str(item.get('updated_at', '')).replace('T', ' ')[:19],
                  11, color=MUTED).grid(row=4, column=0, sticky='w', pady=(4, 0))
            secret = label(row, PASSWORD_MASK if item.get('has_password') else 'Sem senha cadastrada',
                           13, color=MUTED, wraplength=480, justify='left')
            secret.grid(row=5, column=0, sticky='w', pady=(4, 0))
            self._vault_labels[item['id']] = secret
            actions = ctk.CTkFrame(row, fg_color='transparent')
            actions.grid(row=6, column=0, sticky='w', pady=(5, 0))
            self.small_action(actions, 'Editar', lambda entry=item: self.open_feature_dialog('access', entry))
            self.small_action(actions, 'Excluir', lambda entry=item: self.delete_access(entry), danger=True)
            if item.get('has_password') and self.desktop_vault_available:
                self.small_action(actions, '👁', lambda entry=item, widget=secret: self.toggle_vault_password(entry, widget))
                self.small_action(actions, 'Copiar', lambda entry=item: self.copy_vault_password(entry))
        if not rows:
            self.empty(result, 'Nenhum acesso cadastrado.', 'Cadastre um sistema para organizar seus links.')

    def delete_access(self, entry):
        if not messagebox.askyesno('Excluir acesso', 'Excluir este registro do catálogo?', parent=self):
            return
        try:
            self.catalog.delete_entry(entry['id'])
            self.render()
            self.feedback('Acesso excluído.')
        except Exception as exc:
            self.show_error(exc)

    def bills_view(self):
        filters = card(self.content)
        filters.grid(row=0, column=0, sticky='ew', pady=(0, 16))
        filters.grid_columnconfigure((0, 1, 2), weight=1, uniform='bill-filters')
        fields = {}
        for col, key, caption in [(0, 'month', 'MÊS · AAAA-MM'), (2, 'category', 'CATEGORIA')]:
            box = ctk.CTkFrame(filters, fg_color='transparent')
            box.grid(row=0, column=col, sticky='ew', padx=12, pady=12)
            label(box, caption, 11, True, color=MUTED).pack(anchor='w', pady=(0, 4))
            entry = ctk.CTkEntry(box, height=37, font=font(13))
            entry.insert(0, self._bill_filters[key])
            entry.pack(fill='x')
            fields[key] = entry
        box = ctk.CTkFrame(filters, fg_color='transparent')
        box.grid(row=0, column=1, sticky='ew', padx=12, pady=12)
        label(box, 'STATUS', 11, True, color=MUTED).pack(anchor='w', pady=(0, 4))
        status = OptionMenu(box, values=['Todos', 'Pendente', 'Paga', 'Vencida'], height=37, font=font(13))
        status.set(self._bill_filters['status'] or 'Todos')
        status.pack(fill='x')
        fields['status'] = status
        for col, key, caption in [(0, 'due_start', 'VENCIMENTO DE · DD/MM/AAAA'),
                                  (1, 'due_end', 'ATÉ · DD/MM/AAAA')]:
            box = ctk.CTkFrame(filters, fg_color='transparent')
            box.grid(row=1, column=col, sticky='ew', padx=12, pady=(0, 12))
            label(box, caption, 10, True, color=MUTED).pack(anchor='w', pady=(0, 4))
            entry = ctk.CTkEntry(box, height=37, font=font(13))
            entry.insert(0, self._bill_filters[key])
            entry.pack(fill='x')
            fields[key] = entry
        def apply():
            self._bill_filters = {key: field.get().strip() for key, field in fields.items()}
            self.render()
        button(filters, 'Filtrar', apply, width=110).grid(row=1, column=2, padx=12, pady=(10, 12))
        for field in fields.values():
            if isinstance(field, ctk.CTkEntry):
                field.bind('<Return>', lambda _: apply())
        params = dict(self._bill_filters)
        params['status'] = '' if params['status'] == 'Todos' else params['status']
        try:
            for key in ('due_start', 'due_end'):
                if params[key]:
                    params[key] = parse_date(params[key]).isoformat()
            rows = self.finance.list_bills(**params)
        except ValueError as exc:
            label(self.content, str(exc), 13, color=RED, wraplength=650).grid(row=1, column=0, pady=20)
            return
        totals = self.finance.totals(rows)
        summary = ctk.CTkFrame(self.content, fg_color='transparent')
        summary.grid(row=1, column=0, sticky='ew', pady=(0, 16))
        summary.grid_columnconfigure((0, 1, 2), weight=1, uniform='bill-summary')
        for col, (caption, key, color) in enumerate([('Total a pagar', 'to_pay_cents', BLUE),
                                                      ('Total pago', 'paid_cents', GREEN),
                                                      ('Total vencido', 'overdue_cents', RED)]):
            box = card(summary)
            box.grid(row=0, column=col, sticky='nsew', padx=(0 if col == 0 else 5, 0 if col == 2 else 5))
            label(box, caption, 12, color=MUTED).pack(anchor='w', padx=14, pady=(14, 3))
            label(box, reais(totals[key]), 20, True, color=color).pack(anchor='w', padx=14, pady=(0, 14))
        result = card(self.content)
        result.grid(row=2, column=0, sticky='ew', pady=(0, 16))
        self.section_title(result, f'{len(rows)} conta(s)', 'Totais conforme os filtros. Valores pagos permanecem no histórico.')
        for bill in rows:
            self.bill_row(result, bill)
        if not rows:
            self.empty(result, 'Nenhuma conta neste período.', 'Cadastre uma conta ou ajuste os filtros.')
        reminder_box = card(self.content)
        reminder_box.grid(row=3, column=0, sticky='ew', pady=(0, 16))
        self.section_title(reminder_box, 'Lembretes de hoje', 'Avisos com 7, 3 e 1 dia de antecedência; mantenha a agenda aberta.')
        reminders = self.finance.reminders()
        for bill in reminders:
            label(reminder_box, f'{bill["title"]} · vence em {bill["days_before"]} dia(s) · '
                  f'{format_date(bill["due_date"])}', 13, color=BLUE, wraplength=650,
                  justify='left').pack(anchor='w', padx=20, pady=(0, 12))
        if not reminders:
            label(reminder_box, 'Nenhum aviso para hoje.', 13, color=MUTED).pack(anchor='w', padx=20, pady=(0, 16))
        history = card(self.content)
        history.grid(row=4, column=0, sticky='ew')
        self.section_title(history, 'Histórico de pagamentos', 'Até 100 pagamentos recentes, inclusive de contas excluídas; todos permanecem no banco.')
        for payment in self.finance.payment_history()[:100]:
            label(history, f'{payment.get("title", "Conta")} · {reais(payment["amount_cents"])} · '
                  f'pago em {format_date(payment["paid_on"])}', 13, color=MUTED,
                  wraplength=650, justify='left').pack(anchor='w', padx=20, pady=(0, 12))

    def bill_row(self, parent, bill):
        row = ctk.CTkFrame(parent, fg_color='transparent')
        row.bill_id = bill['id']
        row.pack(fill='x', padx=20, pady=(0, 17))
        row.grid_columnconfigure(0, weight=1)
        label(row, bill['title'], 15, True, wraplength=480, justify='left').grid(row=0, column=0, sticky='w')
        label(row, reais(bill['amount_cents']), 15, True).grid(row=0, column=1, sticky='e')
        text = f'{format_date(bill["due_date"])} · {bill["category"] or "Sem categoria"} · {bill["recurrence"]}'
        if bill.get('responsible'):
            text += ' · ' + bill['responsible']
        label(row, text, 12, color=MUTED, wraplength=620, justify='left').grid(row=1, column=0, columnspan=2, sticky='w')
        state = bill['status'] + (' · Próxima do vencimento' if bill.get('near_due') else '')
        if bill.get('paid_on'):
            state += ' · ' + format_date(bill['paid_on'])
        color = RED if bill['status'] == 'Vencida' else GREEN if bill['status'] == 'Paga' else BLUE
        label(row, state, 12, True, color=color).grid(row=2, column=0, columnspan=2, sticky='w', pady=(3, 0))
        if bill.get('notes'):
            label(row, bill['notes'][:300], 12, color=MUTED, wraplength=620,
                  justify='left').grid(row=3, column=0, columnspan=2, sticky='w')
        actions = ctk.CTkFrame(row, fg_color='transparent')
        actions.grid(row=4, column=0, columnspan=2, sticky='w', pady=(5, 0))
        if bill['status'] != 'Paga':
            self.small_action(actions, 'Pagar', lambda item=bill: self.pay_bill_dialog(item))
            self.small_action(actions, 'Editar', lambda item=bill: self.open_feature_dialog('bill', item))
        self.small_action(actions, 'Excluir', lambda item=bill: self.delete_bill(item), danger=True)
        self.small_action(actions, 'Anexar', lambda item=bill: self.attach_bill_receipt(item))
        if bill.get('receipt_id'):
            self.small_action(actions, 'Recibo', lambda item=bill: self.open_bill_receipt(item))

    def delete_bill(self, bill):
        prompt = 'Excluir esta conta? O histórico de pagamentos será preservado.'
        if bill['recurrence'] == 'Mensal':
            prompt += '\nA série mensal será encerrada para próximas cobranças.'
        if not messagebox.askyesno('Excluir conta', prompt, parent=self):
            return
        try:
            self.finance.delete_bill(bill['id'])
            self.render()
            self.feedback('Conta excluída. Histórico preservado.')
        except Exception as exc:
            self.show_error(exc)

    def pay_bill_dialog(self, bill):
        if self._dialog is not None and self._dialog.winfo_exists():
            self._dialog.focus_force()
            return
        self._dialog = PaymentDialog(self, bill)

    def attach_bill_receipt(self, bill):
        from .receipts import ReceiptStore
        selected = filedialog.askopenfilename(parent=self, title='Anexar comprovante privado · PNG/JPEG até 5 MB',
                    filetypes=[('Imagens PNG/JPEG', '*.png *.jpg *.jpeg')])
        if not selected:
            return
        store = ReceiptStore(self.db.path.parent / 'receipts')
        receipt = None
        try:
            with open(selected, 'rb') as stream:
                receipt = store.save(stream, Path(selected).name)
            self.finance.attach_receipt(bill['id'], receipt['id'])
            self.render()
            self.feedback('Comprovante armazenado na pasta privada da agenda.')
        except Exception as exc:
            if receipt is not None:
                store.delete(receipt['id'])
            self.show_error(exc)

    def open_bill_receipt(self, bill):
        from .receipts import ReceiptStore
        try:
            path = ReceiptStore(self.db.path.parent / 'receipts').open(bill['receipt_id'])
            os.startfile(path)
        except Exception as exc:
            self.show_error(exc)
