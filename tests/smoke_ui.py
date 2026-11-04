r"""Verificação visual/integrada opcional: abre janelas usando somente dados fictícios.

Executar na raiz: .venv\Scripts\python.exe -m tests.smoke_ui
Requer uma sessão gráfica Windows; não faz parte de unittest discover.
"""
from datetime import date, timedelta
import ctypes
from pathlib import Path
import tempfile
import time
import traceback
from unittest.mock import patch

import customtkinter as ctk
from PIL import ImageGrab

from agenda.services import AgendaService, format_date
from agenda.storage import Database
from agenda.ui import AgendaApp, VIEWS


def set_field(field, value):
    if isinstance(field, ctk.CTkOptionMenu):
        field.set(value)
    elif isinstance(field, ctk.CTkTextbox):
        field.delete('1.0', 'end')
        field.insert('1.0', value)
    else:
        field.delete(0, 'end')
        field.insert(0, value)


def widget_text(root):
    values = []
    for child in root.winfo_children():
        try:
            values.append(str(child.cget('text')))
        except Exception:
            pass
        values.extend(widget_text(child))
    return values


def fail_ui(self, error, parent=None):
    raise AssertionError(f'Erro inesperado na interface: {error}') from error


def pump(app, duration=.18):
    until = time.monotonic() + duration
    while time.monotonic() < until:
        app.update()
        time.sleep(.015)
    if app.callback_errors:
        raise AssertionError('\n'.join(app.callback_errors))


def assert_horizontal_fit(app):
    left = app.main.winfo_rootx()
    right = left + app.main.winfo_width()
    assert app.content.winfo_width() <= app.main.winfo_width()
    pending = list(app.content.winfo_children())
    while pending:
        widget = pending.pop()
        pending.extend(widget.winfo_children())
        if isinstance(widget, (ctk.CTkFrame, ctk.CTkButton, ctk.CTkEntry,
                               ctk.CTkOptionMenu, ctk.CTkLabel, ctk.CTkCheckBox)) \
                and widget.winfo_ismapped():
            x, width = widget.winfo_rootx(), widget.winfo_width()
            assert x >= left - 2 and x + width <= right + 2, (
                f'Conteúdo excede a largura em {app.current_view}: '
                f'{type(widget).__name__} x={x} largura={width}, área={left}..{right}')


def capture(app, name):
    app.geometry('1240x820+20+20')
    app.lift()
    app.focus_force()
    pump(app, .35)
    destination = Path(__file__).resolve().parents[1] / 'docs' / 'screenshots' / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Captura somente a janela fictícia, mesmo que outro aplicativo esteja à frente.
    window_handle = ctypes.windll.user32.GetAncestor(app.winfo_id(), 2)
    ImageGrab.grab(window=window_handle).save(destination)
    return destination


def event_values(title, day, start, end, **extra):
    result = {'title': title, 'event_date': day.isoformat(), 'event_time': start,
              'end_time': end, 'category': 'Reunião', 'priority': 'Normal',
              'reminder_minutes': 15, 'location': 'Sala de reuniões',
              'description': '', 'notes': ''}
    result.update(extra)
    return result


def run():
    callbacks = []
    captures = []
    with tempfile.TemporaryDirectory(prefix='agenda-ui-') as folder:
        path = Path(folder) / 'agenda.db'
        db = Database(path)
        app = None
        with patch.object(AgendaApp, 'show_error', new=fail_ui):
            try:
                app = AgendaApp(db, enable_reminders=False)
                app.callback_errors = callbacks
                app.report_callback_exception = lambda *exc: callbacks.append(''.join(traceback.format_exception(*exc)))
                app.geometry('1240x820+20+20')
                pump(app)
                for size in ('1240x820', '1000x650'):
                    app.geometry(size + '+20+20')
                    for view in VIEWS:
                        app.navigate(view)
                        pump(app, .06)
                        assert app.current_view == view
                        assert_horizontal_fit(app)
                app.navigate('events')
                assert 'Nenhum item por aqui.' in widget_text(app.content)
                print('OK: todas as seções vazias em 1240x820 e 1000x650.')

                app.open_dialog()
                pump(app)
                dialog = app._dialog
                dialog.save()
                assert not dialog.saved and 'título' in dialog.error.cget('text').lower()
                set_field(dialog.inputs['title'], 'Planejamento estratégico')
                set_field(dialog.inputs['end_time'], '08:00')
                dialog.save()
                assert not dialog.saved and 'término' in dialog.error.cget('text').lower()
                set_field(dialog.inputs['end_time'], '10:00')
                set_field(dialog.inputs['description'], 'Alinhar prioridades e entregas da semana.')
                set_field(dialog.inputs['priority'], 'Alta')
                dialog.save()
                pump(app)
                assert dialog.saved and len(db.list_events()) == 1
                event_id = db.list_events()[0]['id']
                app.open_dialog(record=db.get_event(event_id))
                pump(app)
                dialog = app._dialog
                set_field(dialog.inputs['location'], 'Sala São Paulo · 3º andar')
                dialog.save()
                pump(app)
                assert dialog.saved and db.get_event(event_id)['location'] == 'Sala São Paulo · 3º andar'

                app.open_dialog()
                pump(app)
                dialog = app._dialog
                set_field(dialog.inputs['title'], 'Reunião sobreposta')
                with patch('agenda.dialogs.messagebox.askyesno', return_value=False) as confirm:
                    dialog.save()
                    assert confirm.call_count == 1 and not dialog.saved
                with patch('agenda.dialogs.messagebox.askyesno', return_value=True):
                    dialog.save()
                pump(app)
                assert len(db.list_events()) == 2
                disposable = next(e for e in db.list_events() if e['id'] != event_id)
                app.delete('event', disposable)
                assert app._delete_dialog.event_id == disposable['id']
                assert disposable['title'] in widget_text(app._delete_dialog)
                app._delete_dialog.buttons['cancel'].invoke()
                assert db.get_event(disposable['id']) is not None
                app.delete('event', disposable)
                app._delete_dialog.buttons['delete'].invoke()
                pump(app)
                assert db.get_event(disposable['id']) is None
                print('OK: formulário, validações, edição e conflito com opção de cancelar/salvar.')

                app.navigate('tasks')
                app.open_dialog()
                pump(app)
                dialog = app._dialog
                dialog.save()
                assert not dialog.saved
                set_field(dialog.inputs['title'], 'Revisar apresentação do conselho')
                set_field(dialog.inputs['due_date'], format_date(date.today() + timedelta(days=1)))
                set_field(dialog.inputs['priority'], 'Urgente')
                dialog.save()
                pump(app)
                task_id = db.list_tasks()[0]['id']
                app.toggle('task', db.get_task(task_id))
                assert db.get_task(task_id)['completed'] == 1
                app.toggle('task', db.get_task(task_id))
                assert db.get_task(task_id)['completed'] == 0
                app.open_dialog(kind='task', record=db.get_task(task_id))
                pump(app)
                dialog = app._dialog
                set_field(dialog.inputs['description'], 'Consolidar indicadores financeiros e operacionais.')
                dialog.save()
                pump(app)
                app.toggle('event', db.get_event(event_id))
                assert db.get_event(event_id)['completed'] == 1
                app.toggle('event', db.get_event(event_id))
                assert db.get_event(event_id)['completed'] == 0
                print('OK: tarefas, edição, conclusão e reabertura de eventos e tarefas.')

                app.navigate('events')
                app._filters['events'].update(period='Personalizado', start='31/02/2026', end='')
                app.update_results()
                assert any('data válida' in text for text in widget_text(app.results))
                today = format_date(date.today())
                app._filters['events'].update(start=today, end=today, search='Planejamento', priority='Alta')
                app.update_results()
                assert '1 compromisso(s)' in widget_text(app.results)
                app._filters['events'].update(period='Todos', start='', end='', search='', priority='Todas')
                app.update_results()
                app.change_filter('events', 'search', 'Pesquisa sem resultado')
                pump(app, .4)
                assert 'Nenhum item por aqui.' in widget_text(app.results)
                app._filters['events']['search'] = ''
                app.navigate('settings')
                fields = app.settings_fields
                set_field(fields['manager_name'], 'Letícia Pereira')
                fields['default_reminder'].set('10 minutos antes')
                fields['appearance'].set('Escuro')
                fields['scaling'].set('110')
                app.save_settings()
                pump(app)
                assert db.get_settings()['manager_name'] == 'Letícia Pereira'
                assert db.get_settings()['default_reminder'] == '10'
                assert ctk.get_appearance_mode() == 'Dark'
                app.settings_fields['appearance'].set('Claro')
                app.settings_fields['scaling'].set('100')
                app.save_settings()
                pump(app)
                print('OK: pesquisa, filtros personalizados, tema escuro/claro e escala.')

                snapshot = Path(folder) / 'backup-interface.db'
                with patch('agenda.ui.filedialog.asksaveasfilename', return_value=str(snapshot)):
                    app.backup()
                assert snapshot.is_file()
                extra_id = app.service.save_task({'title': 'Tarefa temporária após backup'})
                with patch('agenda.ui.filedialog.askopenfilename', return_value=str(snapshot)), \
                        patch('agenda.ui.messagebox.askyesno', return_value=True), \
                        patch('agenda.ui.messagebox.showinfo'):
                    app.restore()
                pump(app)
                assert db.get_task(extra_id) is None
                assert db.get_task(task_id) is not None
                assert list((Path(folder) / 'backups').glob('*antes_restauracao*'))
                print('OK: backup e restauração pela interface, com cópia anterior preservada.')

                app.show_reminder(db.get_event(event_id))
                pump(app)
                panels = [widget for widget in app.winfo_children() if isinstance(widget, ctk.CTkToplevel)]
                assert panels and any('É HORA DE SE PREPARAR' in widget_text(panel) for panel in panels)
                for panel in panels:
                    panel.destroy()
                print('OK: lembrete visual local, sem enviar notificações nativas de teste.')

                service = AgendaService(db)
                # Dados de demonstração independentes do horário da execução.
                service.save_event(event_values('Alinhamento com a diretoria', date.today() + timedelta(days=1),
                    '10:30', '11:30', priority='Alta', description='Planejamento do próximo trimestre.'))
                service.save_event(event_values('Revisão de indicadores', date.today(), '14:00', '15:00',
                    location='Sala de reuniões · 2º andar', priority='Normal'))
                service.save_task({'title': 'Aprovar proposta comercial', 'priority': 'Alta',
                    'due_date': date.today().isoformat(), 'description': 'Revisão final do orçamento.'})
                for size in ('1240x820', '1000x650'):
                    app.geometry(size + '+20+20')
                    for view in VIEWS:
                        app.navigate(view)
                        pump(app, .06)
                        assert_horizontal_fit(app)
                app.navigate('dashboard')
                captures.append(capture(app, 'painel.png'))
                app.navigate('calendar')
                app.change_month(-1)
                app.change_month(1)
                app.calendar_today()
                captures.append(capture(app, 'calendario.png'))
                print('OK: todas as seções populadas, calendário e capturas de tela.')

                event_count = len(db.list_events())
                task_count = len(db.list_tasks(status='Todas'))
                app.close()
                app = None
                reopened = Database(path)
                reopened.initialize()
                assert event_count == len(reopened.list_events())
                assert task_count == len(reopened.list_tasks(status='Todas'))
                assert reopened.get_settings()['manager_name'] == 'Letícia Pereira'
                assert not callbacks
                print(f'OK: persistência após fechar ({event_count} compromissos e {task_count} tarefas).')
            finally:
                if app is not None:
                    app.close()
    print('Interface aprovada. Capturas:', *(str(path) for path in captures), sep='\n')


if __name__ == '__main__':
    run()
