r"""Integração das ações de compromissos em sessão gráfica Windows.

Executar na raiz: .venv\Scripts\python.exe -m tests.smoke_actions
Usa somente um SQLite temporário; não acessa o banco pessoal.
"""
from datetime import date, timedelta
import io
import logging
from pathlib import Path
import tempfile
import traceback
from unittest.mock import patch

import customtkinter as ctk

from agenda.storage import Database
from agenda.ui import AgendaApp
from tests.smoke_ui import set_field, widget_text, fail_ui, pump, assert_horizontal_fit


def descendants(root):
    for child in root.winfo_children():
        yield child
        yield from descendants(child)


def text(widget):
    try:
        return str(widget.cget('text'))
    except Exception:
        return ''


def click_button(app, button):
    button.update_idletasks()
    target = button._text_label or button._canvas
    target.event_generate('<ButtonPress-1>', x=max(1, target.winfo_width() // 2),
                          y=max(1, target.winfo_height() // 2))
    pump(app)
    if target.winfo_exists():
        target.event_generate('<ButtonRelease-1>', x=max(1, target.winfo_width() // 2),
                              y=max(1, target.winfo_height() // 2))
        pump(app)


def row_for(app, event_id):
    title = app.db.get_event(event_id)['title']
    rows = [widget for widget in descendants(app.content)
            if isinstance(widget, ctk.CTkFrame) and getattr(widget, 'event_id', None) == event_id]
    if rows:
        return rows[0]
    for widget in descendants(app.content):
        if isinstance(widget, ctk.CTkLabel) and text(widget) == title:
            candidate = widget.master.master
            if any(isinstance(w, ctk.CTkButton) and text(w) == '⋯' for w in descendants(candidate)):
                return candidate
    raise AssertionError(f'Linha de compromisso não encontrada: id {event_id}')


def open_menu(app, event_id):
    row = row_for(app, event_id)
    buttons = [w for w in descendants(row) if isinstance(w, ctk.CTkButton)]
    assert len(buttons) == 1 and text(buttons[0]) == '⋯', 'A linha deve exibir somente o botão ⋯.'
    click_button(app, buttons[0])
    pump(app)
    menu = app._event_menu
    assert menu is not None and menu.winfo_exists(), 'Menu não abriu.'
    assert not (app._dialog and app._dialog.winfo_exists()), 'Abrir o menu não pode abrir a edição.'
    commands = [text(w) for w in descendants(menu) if isinstance(w, ctk.CTkButton)]
    assert all(any(name in command for command in commands)
               for name in ('Editar', 'Reagendar', 'Duplicar', 'Excluir'))
    toggle = 'Reabrir' if app.db.get_event(event_id)['completed'] else 'Concluir'
    assert any(toggle in command for command in commands), commands
    return menu


def menu_action(app, event_id, action):
    menu = open_menu(app, event_id)
    matches = [w for w in descendants(menu) if isinstance(w, ctk.CTkButton) and action in text(w)]
    assert len(matches) == 1, (action, [text(w) for w in descendants(menu)])
    matches[0].invoke()
    pump(app)


def click_menu_action(app, event_id, action):
    menu = open_menu(app, event_id)
    button = menu.buttons[action]
    click_button(app, button)


def filters(app, *, status='Todos', period='Todos'):
    app._filters['events'].update(search='', start='', end='', status=status,
                                  period=period, priority='Todas', category='Todas')
    app.navigate('events')
    pump(app)


def assert_result(app, count):
    assert f'{count} compromisso(s)' in widget_text(app.results), widget_text(app.results)


def assert_persisted(path, expected):
    assert Database(path).list_events() == expected, 'A alteração não foi persistida imediatamente.'


def stat(app, caption):
    labels = [w for w in descendants(app.content)
              if isinstance(w, ctk.CTkLabel) and text(w) == caption]
    assert len(labels) == 1, caption
    return next(int(text(w)) for w in descendants(labels[0].master)
                if isinstance(w, ctk.CTkLabel) and text(w).isdigit())


def save_dialog(app):
    with patch('agenda.dialogs.messagebox.askyesno', return_value=True):
        app._dialog.save()
    pump(app)
    assert app._dialog.saved, app._dialog.error.cget('text')


def run():
    with tempfile.TemporaryDirectory(prefix='agenda-actions-') as temporary:
        path = Path(temporary) / 'agenda.db'
        db = Database(path)
        app = None
        log_capture = io.StringIO()
        log_handler = logging.StreamHandler(log_capture)
        logger = logging.getLogger()
        original_log_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(log_handler)
        with patch.object(AgendaApp, 'show_error', new=fail_ui):
            try:
                app = AgendaApp(db, enable_reminders=False)
                app.callback_errors = []
                app.report_callback_exception = lambda *exc: app.callback_errors.append(''.join(traceback.format_exception(*exc)))
                app.geometry('1240x820+20+20')
                pump(app)
                filters(app)
                app.open_dialog()
                pump(app)
                values = {'title': 'Conselho executivo · revisão', 'event_time': '14:00',
                          'end_time': '15:00', 'description': 'Pauta completa da diretoria.',
                          'location': 'Sala São Paulo', 'notes': 'Levar indicadores consolidados.',
                          'priority': 'Alta', 'category': 'Reunião', 'reminder_minutes': '10 minutos antes'}
                for key, value in values.items():
                    set_field(app._dialog.inputs[key], value)
                save_dialog(app)
                source_id = db.list_events()[0]['id']
                assert_result(app, 1)
                assert_persisted(path, db.list_events())
                menu = open_menu(app, source_id)
                with patch.object(app, 'perform_event_action') as dispatch:
                    click_button(app, menu.buttons['delete'])
                    dispatch.assert_called_once_with('delete', source_id)
                assert db.get_event(source_id) is not None
                assert not (app._dialog and app._dialog.winfo_exists())
                for surface in ('dashboard', 'calendar', 'events', 'pending'):
                    if surface == 'dashboard':
                        app.navigate('dashboard')
                    elif surface == 'calendar':
                        app.navigate('calendar')
                        app.select_day(date.today())
                    else:
                        filters(app, status='Pendentes' if surface == 'pending' else 'Todos')
                    pump(app)
                    click_menu_action(app, source_id, 'edit')
                    assert app._dialog.winfo_viewable(), 'O clique Editar não exibiu a janela.'
                    assert str(app._dialog.transient()) == str(app), 'Edição não está associada à janela principal.'
                    assert app._dialog.grab_current() is not None, 'A edição não recebeu o foco modal.'
                    assert app._dialog.record['id'] == source_id
                    app._dialog.destroy()
                    pump(app)
                    click_menu_action(app, source_id, 'delete')
                    assert app._delete_dialog.event_id == source_id
                    assert app._delete_dialog.winfo_viewable(), 'O clique Excluir não exibiu a confirmação.'
                    assert str(app._delete_dialog.transient()) == str(app), 'Confirmação não está associada à janela principal.'
                    assert app._delete_dialog.grab_current() is not None, 'A confirmação não recebeu o foco modal.'
                    assert values['title'] in widget_text(app._delete_dialog)
                    click_button(app, app._delete_dialog.buttons['cancel'])
                    pump(app)
                    assert db.get_event(source_id) is not None
                filters(app)
                print('OK: cadastro CRUD e persistência imediata.')

                row = row_for(app, source_id)
                title_label = next(w for w in descendants(row)
                                   if isinstance(w, ctk.CTkLabel) and text(w) == values['title'])
                before = db.list_events()
                native_label = title_label._label
                native_label.event_generate('<ButtonPress-1>', x=5, y=5, time=1000)
                native_label.event_generate('<ButtonRelease-1>', x=5, y=5, time=1010)
                pump(app)
                assert db.list_events() == before
                assert not (app._dialog and app._dialog.winfo_exists())
                native_label.event_generate('<ButtonPress-1>', x=5, y=5, time=2000)
                native_label.event_generate('<ButtonRelease-1>', x=5, y=5, time=2010)
                native_label.event_generate('<ButtonPress-1>', x=5, y=5, time=2100)
                native_label.event_generate('<ButtonRelease-1>', x=5, y=5, time=2110)
                pump(app)
                assert app._dialog and app._dialog.winfo_exists(), 'Duplo clique não abriu detalhes.'
                details = widget_text(app._dialog)
                for content in (values['title'], values['description'], values['location'], values['notes']):
                    assert content in details, (content, details)
                assert db.list_events() == before, 'Abrir detalhes alterou o banco.'
                app._dialog.destroy()
                pump(app)
                print('OK: clique simples não altera dados; duplo clique abre detalhes completos.')

                click_menu_action(app, source_id, 'edit')
                assert app._dialog.winfo_viewable() and app._dialog.grab_current() is not None
                set_field(app._dialog.inputs['title'], 'Conselho executivo atualizado')
                set_field(app._dialog.inputs['description'], 'Pauta revisada.')
                set_field(app._dialog.inputs['event_date'], date.today().strftime('%d/%m/%Y'))
                set_field(app._dialog.inputs['event_time'], '14:30')
                set_field(app._dialog.inputs['end_time'], '15:30')
                set_field(app._dialog.inputs['category'], 'Reunião')
                set_field(app._dialog.inputs['priority'], 'Urgente')
                set_field(app._dialog.inputs['location'], 'Sala do conselho')
                set_field(app._dialog.inputs['notes'], 'Levar nova versão.')
                save_dialog(app)
                updated_source = db.get_event(source_id)
                assert updated_source['id'] == source_id
                assert len(db.list_events()) == 1, 'Editar criou um novo compromisso.'
                for key, expected in {
                    'title': 'Conselho executivo atualizado',
                    'description': 'Pauta revisada.',
                    'event_date': date.today().isoformat(),
                    'event_time': '14:30',
                    'end_time': '15:30',
                    'category': 'Reunião',
                    'priority': 'Urgente',
                    'location': 'Sala do conselho',
                    'notes': 'Levar nova versão.',
                }.items():
                    assert updated_source[key] == expected, key
                assert 'Compromisso salvo.' in app.status.cget('text')
                assert_persisted(path, db.list_events())
                app.navigate('dashboard')
                pump(app)
                assert 'Conselho executivo atualizado' in widget_text(app.content)
                app.navigate('calendar')
                app.select_day(date.today())
                pump(app)
                assert 'Conselho executivo atualizado' in widget_text(app.content)
                filters(app, status='Pendentes')
                menu_action(app, source_id, 'Concluir')
                assert_result(app, 0)
                assert db.get_event(source_id)['completed'] == 1
                assert_persisted(path, db.list_events())
                filters(app, status='Concluídos')
                menu_action(app, source_id, 'Reabrir')
                assert_result(app, 0)
                assert db.get_event(source_id)['completed'] == 0
                filters(app, status='Pendentes', period='Próximos 7 dias')
                assert_result(app, 1)
                menu_action(app, source_id, 'Concluir')
                assert_result(app, 0)
                filters(app, period='Próximos 7 dias')
                menu_action(app, source_id, 'Editar')
                set_field(app._dialog.inputs['description'], 'Pauta revisada após conclusão.')
                save_dialog(app)
                assert db.get_event(source_id)['completed'] == 1
                print('OK: editar/concluir/reabrir nos filtros pendentes e próximos sete dias.')

                db.claim_reminder(db.get_event(source_id))
                source_snapshot = db.get_event(source_id)
                count = len(db.list_events())
                menu_action(app, source_id, 'Duplicar')
                assert len(db.list_events()) == count, 'Abrir duplicação já inseriu registro.'
                app._dialog.destroy()
                pump(app)
                assert db.get_event(source_id) == source_snapshot
                menu_action(app, source_id, 'Duplicar')
                assert app._dialog.inputs['title'].get() == source_snapshot['title'] + ' (cópia)'
                save_dialog(app)
                copies = [e for e in db.list_events() if e['id'] != source_id]
                assert len(copies) == 1
                clone = copies[0]
                clone_id = clone['id']
                assert clone_id != source_id and clone['completed'] == 0
                assert not db.reminder_claimed(clone), 'Cópia herdou histórico de alerta.'
                assert db.get_event(source_id) == source_snapshot
                for key in ('description', 'location', 'notes', 'priority', 'category',
                            'event_date', 'event_time', 'end_time', 'reminder_minutes'):
                    assert clone[key] == source_snapshot[key], key
                assert_result(app, 2)
                assert_persisted(path, db.list_events())
                print('OK: duplicação cria outro ID pendente e preserva conteúdo/original/histórico.')

                filters(app, status='Pendentes', period='Próximos 7 dias')
                assert_result(app, 1)
                clone_snapshot = db.get_event(clone_id)
                menu_action(app, clone_id, 'Reagendar')
                assert set(app._dialog.inputs) == {'event_date', 'event_time', 'end_time'}
                app._dialog.destroy()
                pump(app)
                assert db.get_event(clone_id) == clone_snapshot
                menu_action(app, clone_id, 'Reagendar')
                set_field(app._dialog.inputs['event_date'], (date.today() + timedelta(days=9)).strftime('%d/%m/%Y'))
                set_field(app._dialog.inputs['event_time'], '16:00')
                set_field(app._dialog.inputs['end_time'], '15:00')
                app._dialog.save()
                assert not app._dialog.saved
                assert db.get_event(clone_id) == clone_snapshot
                set_field(app._dialog.inputs['end_time'], '17:00')
                save_dialog(app)
                assert_result(app, 0)
                updated = db.get_event(clone_id)
                for key, old in clone_snapshot.items():
                    if key not in ('event_date', 'event_time', 'end_time'):
                        assert updated[key] == old, key
                assert_persisted(path, db.list_events())
                filters(app, status='Pendentes')
                assert_result(app, 1)
                menu_action(app, clone_id, 'Reagendar')
                tomorrow = date.today() + timedelta(days=1)
                set_field(app._dialog.inputs['event_date'], tomorrow.strftime('%d/%m/%Y'))
                save_dialog(app)
                filters(app, status='Pendentes', period='Próximos 7 dias')
                assert_result(app, 1)
                print('OK: reagendamento valida horários, preserva campos e atualiza filtros imediatamente.')

                app.navigate('dashboard')
                pump(app)
                assert stat(app, 'Compromissos hoje') == 1
                assert stat(app, 'Concluídos hoje') == 1
                assert stat(app, 'Agenda pendente') == 1
                app.navigate('calendar')
                app.calendar_month = tomorrow.replace(day=1)
                app.select_day(tomorrow)
                pump(app)
                assert db.get_event(clone_id)['title'] in widget_text(app.content)
                assert any(isinstance(w, ctk.CTkButton) and text(w) == f'{tomorrow.day}\n● 1'
                           for w in descendants(app.content)), 'Calendário não atualizou o indicador de data.'
                app.select_day(date.today())
                pump(app)
                assert source_snapshot['title'] in widget_text(app.content)
                assert db.get_event(clone_id)['title'] not in widget_text(app.content)
                print('OK: calendário e indicadores refletem conclusão, cópia e reagendamento sem reiniciar.')

                filters(app, status='Pendentes', period='Próximos 7 dias')
                clone_title = db.get_event(clone_id)['title']
                before_cancel = db.get_event(clone_id)
                click_menu_action(app, clone_id, 'delete')
                confirmation = app._delete_dialog
                assert confirmation is not None and confirmation.winfo_exists()
                assert clone_title in widget_text(confirmation)
                click_button(app, confirmation.buttons['cancel'])
                pump(app)
                assert db.get_event(clone_id) is not None
                assert db.get_event(clone_id) == before_cancel
                assert Database(path).get_event(clone_id) == before_cancel
                assert_result(app, 1)
                click_menu_action(app, clone_id, 'delete')
                confirmation = app._delete_dialog
                assert confirmation is not None and confirmation.winfo_exists()
                click_button(app, confirmation.buttons['delete'])
                pump(app)
                assert db.get_event(clone_id) is None
                assert_result(app, 0)
                assert_persisted(path, db.list_events())
                assert db.get_event(source_id) == source_snapshot
                app.navigate('dashboard')
                pump(app)
                assert stat(app, 'Agenda pendente') == 0
                app.navigate('calendar')
                app.select_day(tomorrow)
                pump(app)
                assert clone_title not in widget_text(app.content)
                assert any(isinstance(w, ctk.CTkButton) and text(w) == str(tomorrow.day)
                           for w in descendants(app.content)), 'O calendário manteve o indicador do compromisso excluído.'
                filters(app, status='Pendentes')
                assert_result(app, 0)
                print('OK: confirmação Cancelar/Excluir atualiza painel, calendário e pendências.')

                repeated_title = db.get_event(source_id)['title']
                duplicate_id = app.service.save_event({
                    **db.get_event(source_id),
                    'event_time': '18:00',
                    'end_time': '19:00',
                    'completed': 0,
                })
                filters(app, status='Pendentes')
                assert db.get_event(duplicate_id)['title'] == repeated_title
                click_menu_action(app, duplicate_id, 'edit')
                assert app._dialog.winfo_viewable() and app._dialog.grab_current() is not None
                set_field(app._dialog.inputs['title'], 'Título alterado somente no ID selecionado')
                save_dialog(app)
                assert db.get_event(duplicate_id)['title'] == 'Título alterado somente no ID selecionado'
                assert db.get_event(source_id)['title'] == repeated_title
                assert len(db.list_events()) == 2
                assert_persisted(path, db.list_events())
                filters(app, status='Pendentes')
                click_menu_action(app, duplicate_id, 'delete')
                assert 'Título alterado somente no ID selecionado' in widget_text(app._delete_dialog)
                click_button(app, app._delete_dialog.buttons['delete'])
                pump(app)
                assert db.get_event(duplicate_id) is None
                assert db.get_event(source_id)['title'] == repeated_title
                reopened = Database(path)
                reopened.initialize()
                assert reopened.get_event(duplicate_id) is None
                assert reopened.get_event(source_id)['title'] == repeated_title
                print('OK: edição e exclusão por ID correto com títulos repetidos, inclusive após reabrir o banco.')

                filters(app)
                for expected in (0, 1):
                    menu = open_menu(app, source_id)
                    menu.event_generate('<Down>')
                    pump(app)
                    assert menu.focused_button() == menu.buttons['toggle']
                    menu.event_generate('<Up>')
                    pump(app)
                    assert menu.focused_button() == menu.buttons['edit']
                    menu.event_generate('<Down>')
                    pump(app)
                    assert menu.focused_button() == menu.buttons['toggle']
                    menu.event_generate('<Return>')
                    pump(app)
                    assert db.get_event(source_id)['completed'] == expected
                assert db.get_event(source_id) == source_snapshot
                menu = open_menu(app, source_id)
                app.focus_force()
                pump(app)
                assert app._event_menu is None or not app._event_menu.winfo_exists()
                print('OK: menu por teclado e fechamento ao mudar o foco.')

                app.navigate('settings')
                app.settings_fields['scaling'].set('110')
                app.save_settings()
                pump(app)
                filters(app)
                menu = open_menu(app, source_id)
                assert menu.buttons['delete'].winfo_ismapped()
                menu.event_generate('<Escape>')
                pump(app)
                app.navigate('settings')
                app.settings_fields['scaling'].set('100')
                app.save_settings()
                pump(app)
                filters(app)
                print('OK: menu com escala 110% e retorno a 100%.')

                for size in ('1240x820', '1000x650'):
                    app.geometry(size + '+20+20')
                    pump(app)
                    assert_horizontal_fit(app)
                    menu = open_menu(app, source_id)
                    assert menu.winfo_rootx() >= 0 and menu.winfo_rooty() >= 0
                    menu.event_generate('<Escape>')
                    pump(app)
                    assert app._event_menu is None or not app._event_menu.winfo_exists()
                expected = db.list_events()
                app.close()
                app = None
                reopened = Database(path)
                reopened.initialize()
                assert reopened.list_events() == expected
                assert reopened.get_event(clone_id) is None
                assert reopened.reminder_claimed(reopened.get_event(source_id))
                print('OK: menus nas duas dimensões, Escape e persistência após fechar.')
            finally:
                if app is not None:
                    app.close()
                log_handler.flush()
                logger.removeHandler(log_handler)
                logger.setLevel(original_log_level)
        diagnostic_log = log_capture.getvalue()
        for marker in ('Menu click action=edit event_id=', 'Menu click action=delete event_id=',
                       'Opening record dialog mode=edit', 'SQLite UPDATE event succeeded',
                       'Delete dialog opened event_id=', 'DELETE confirm event_id=',
                       'SQLite DELETE event succeeded', 'Interface refreshed after DELETE'):
            assert marker in diagnostic_log, f'Diagnostic log omitted: {marker}'
    print('Ações de compromissos aprovadas; nenhum dado pessoal foi utilizado.')


if __name__ == '__main__':
    run()
