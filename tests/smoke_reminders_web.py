r"""Real-browser reminder checks, with an isolated temporary database.

Requires Playwright and Microsoft Edge. Run:
.venv\Scripts\python.exe -m tests.smoke_reminders_web
"""
from datetime import datetime, timedelta
from pathlib import Path
import secrets
import tempfile
import threading
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from werkzeug.security import generate_password_hash
from werkzeug.serving import WSGIRequestHandler, make_server

from webapp import create_app
from webapp.models import ManualReminder, db
from webapp.reminders import SAO_PAULO, from_utc_naive


EDGE = Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')


class QuietHandler(WSGIRequestHandler):
    def log(self, log_type, message, *args):
        pass


def assert_no_overflow(page):
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), \
        'The reminder page overflows the viewport.'


def run():
    assert EDGE.is_file(), 'Microsoft Edge is required for this optional test.'
    fixed_now = datetime(2026, 11, 6, 8, 45, tzinfo=SAO_PAULO)
    first_due = (fixed_now + timedelta(days=1)).replace(hour=11, minute=30)
    edited_due = (fixed_now + timedelta(days=2)).replace(hour=12, minute=15)
    password = 'Browser-reminder-test-password'
    title = 'Lembrete de teste do navegador'
    edited_title = (
        'Lembrete atualizado no navegador: revisão de documentos e relatório para o conselho. '
        + 'ProtocoloSemEspacos' * 4)
    previews = Path(tempfile.mkdtemp(prefix='agenda-reminders-previews-'))
    with tempfile.TemporaryDirectory(prefix='agenda-reminders-browser-') as folder:
        app = create_app({
            'TESTING': True, 'SECRET_KEY': secrets.token_hex(32),
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (Path(folder) / 'web.db').as_posix(),
            'ADMIN_USERNAME': 'reminders-browser-test',
            'ADMIN_PASSWORD_HASH': generate_password_hash(password),
            'SESSION_COOKIE_SECURE': False, 'WTF_CSRF_SSL_STRICT': False,
        })
        migration = app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if migration.exception:
            raise migration.exception
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        errors = []
        try:
            with patch('webapp.routes.local_now', return_value=fixed_now) as now_mock, \
                    sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=str(EDGE), headless=True,
                    args=['--disable-background-networking', '--no-first-run'])
                try:
                    context = browser.new_context(viewport={'width': 1440, 'height': 900})
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(origin + '/login')
                    page.locator('input[name="username"]').fill('reminders-browser-test')
                    page.locator('input[name="password"]').fill(password)
                    page.locator('button[type="submit"]').click()
                    page.goto(origin + '/reminders')
                    modal = page.locator('#reminderDialog')
                    empty = page.locator('.reminder-empty')
                    expect(modal).not_to_be_visible()
                    expect(empty).to_be_visible()
                    assert empty.bounding_box()['height'] < 180, 'The empty state is too tall.'
                    assert page.locator('.reminder-panel').bounding_box()['height'] < 280
                    assert_no_overflow(page)
                    page.screenshot(path=str(previews / 'desktop-empty.png'), full_page=True)

                    # Opening, focus trapping and closing need a real browser.
                    new_button = page.locator('[data-dialog-open="#reminderDialog"]')
                    for close_method in ('x', 'escape', 'cancel'):
                        new_button.click()
                        expect(modal).to_be_visible()
                        assert modal.evaluate('(dialog) => dialog.matches(":modal")')
                        assert modal.evaluate('(dialog) => dialog.contains(document.activeElement)')
                        if close_method == 'x':
                            modal.get_by_role('button', name='Fechar', exact=True).click()
                        elif close_method == 'escape':
                            page.keyboard.press('Escape')
                        else:
                            modal.get_by_role('button', name='Cancelar', exact=True).click()
                        expect(modal).not_to_be_visible()
                        expect(new_button).to_be_focused()

                    new_button.click()
                    modal.locator('[type="submit"]').click()
                    expect(modal).to_be_visible()
                    assert not modal.locator('form').evaluate('(form) => form.checkValidity()')
                    expect(page.locator('.reminder-item')).to_have_count(0)
                    # Native required does not catch whitespace; server validation must.
                    modal.locator('input[name="title"]').fill('   ')
                    modal.locator('input[name="due_at"]').fill(first_due.strftime('%Y-%m-%dT%H:%M'))
                    modal.locator('textarea[name="notes"]').fill('Observações preservadas após erro')
                    modal.locator('[type="submit"]').click()
                    expect(modal).to_be_visible()
                    expect(modal.locator('textarea[name="notes"]')).to_have_value(
                        'Observações preservadas após erro')
                    expect(modal.locator('.reminder-form-errors')).to_be_visible()
                    expect(modal.locator('.field-error')).to_contain_text('título')
                    expect(page.locator('.reminder-item')).to_have_count(0)

                    modal.locator('input[name="title"]').fill(title)
                    modal.locator('select[name="priority"]').select_option('Alta')
                    modal.locator('textarea[name="notes"]').fill('Observações iniciais do navegador')
                    modal.locator('[type="submit"]').click()
                    expect(modal).not_to_be_visible()
                    record = page.locator('article.reminder-item').filter(has_text=title)
                    expect(record).to_be_visible()
                    expect(record.locator('.priority')).to_have_text('Alta')
                    expect(record).to_contain_text(first_due.strftime('%d/%m/%Y'))
                    expect(record).to_contain_text('11:30')
                    expect(record.locator('.status')).to_have_text('Próximo')
                    with app.app_context():
                        reminder = db.session.query(ManualReminder).one()
                        reminder_id = reminder.id
                        assert from_utc_naive(reminder.due_at) == first_due

                    record.get_by_text('Editar', exact=True).click()
                    expect(modal).to_be_visible()
                    expect(modal.locator('input[name="title"]')).to_have_value(title)
                    expect(modal.locator('textarea[name="notes"]')).to_have_value(
                        'Observações iniciais do navegador')
                    modal.get_by_role('button', name='Cancelar', exact=True).click()
                    new_button.click()
                    expect(modal.locator('input[name="reminder_id"]')).to_have_value('')
                    expect(modal.locator('input[name="title"]')).to_have_value('')
                    expect(modal.locator('textarea[name="notes"]')).to_have_value('')
                    expect(modal.locator('#reminderDialogTitle')).to_have_text('Novo lembrete')
                    modal.get_by_role('button', name='Cancelar', exact=True).click()
                    record.get_by_text('Editar', exact=True).click()
                    modal.locator('input[name="title"]').fill(edited_title)
                    modal.locator('input[name="due_at"]').fill(edited_due.strftime('%Y-%m-%dT%H:%M'))
                    modal.locator('select[name="priority"]').select_option('Urgente')
                    modal.locator('textarea[name="notes"]').fill('Observações editadas no navegador')
                    modal.locator('[type="submit"]').click()
                    expect(modal).not_to_be_visible()
                    record = page.locator('article.reminder-item').filter(has_text=edited_title)
                    expect(record).to_be_visible()
                    expect(record.locator('.priority')).to_have_text('Urgente')
                    expect(record).to_contain_text('12:15')
                    page.screenshot(path=str(previews / 'desktop-list.png'), full_page=True)
                    with app.app_context():
                        reminder = db.session.get(ManualReminder, reminder_id)
                        assert reminder.title == edited_title
                        assert reminder.notes == 'Observações editadas no navegador'
                        assert from_utc_naive(reminder.due_at) == edited_due
                        assert db.session.query(ManualReminder).count() == 1

                    # Test both the card and the modal at narrow desktop/mobile widths.
                    for width, height in ((1024, 768), (390, 844), (320, 568)):
                        page.set_viewport_size({'width': width, 'height': height})
                        if width <= 700:
                            # Resizing animates the shared sidebar offscreen. Wait for
                            # its real position before inspecting or capturing the page.
                            page.wait_for_function('''() => {
                                const sidebar = document.querySelector('.sidebar');
                                return sidebar.getBoundingClientRect().right <= 0.1;
                            }''')
                        expect(record).to_be_visible()
                        assert_no_overflow(page)
                        assert record.bounding_box()['height'] < (360 if width <= 700 else 200), \
                            'A long reminder title makes its card disproportionately tall.'
                        if width == 390:
                            page.screenshot(path=str(previews / 'mobile-list.png'), full_page=True)
                        record.get_by_text('Editar', exact=True).click()
                        expect(modal).to_be_visible()
                        box = modal.bounding_box()
                        assert box['x'] >= 0 and box['x'] + box['width'] <= width
                        assert box['y'] >= 0 and box['y'] + box['height'] <= height
                        assert modal.evaluate('(dialog) => dialog.scrollWidth <= dialog.clientWidth')
                        if width == 390:
                            page.screenshot(path=str(previews / 'mobile-modal.png'), full_page=True)
                        modal.get_by_role('button', name='Cancelar', exact=True).click()
                        expect(modal).not_to_be_visible()
                    page.set_viewport_size({'width': 1440, 'height': 900})

                    record.get_by_role('button', name='Adiar', exact=False).click()
                    expect(record).to_have_count(0)
                    expect(empty).to_be_visible()
                    with app.app_context():
                        reminder = db.session.get(ManualReminder, reminder_id)
                        assert from_utc_naive(reminder.snoozed_until) == fixed_now + timedelta(days=1)
                        assert not reminder.completed
                    now_mock.return_value = fixed_now + timedelta(days=1, minutes=1)
                    page.reload()
                    expect(record).to_be_visible()
                    record.get_by_role('button', name='Concluir', exact=True).click()
                    expect(record).to_have_count(0)
                    page.get_by_role('link', name='Concluídos', exact=True).click()
                    expect(record).to_be_visible()
                    expect(record.locator('.status')).to_have_text('Concluído')
                    with app.app_context():
                        assert db.session.get(ManualReminder, reminder_id).completed

                    page.once('dialog', lambda dialog: dialog.dismiss())
                    record.get_by_role('button', name='Excluir', exact=True).click()
                    expect(record).to_be_visible()
                    with app.app_context():
                        assert db.session.get(ManualReminder, reminder_id) is not None
                    page.once('dialog', lambda dialog: dialog.accept())
                    record.get_by_role('button', name='Excluir', exact=True).click()
                    expect(record).to_have_count(0)
                    expect(empty).to_be_visible()
                    page.reload()
                    expect(record).to_have_count(0)
                    expect(modal).not_to_be_visible()
                    assert not errors, f'Browser JavaScript errors: {errors}'
                    with app.app_context():
                        assert db.session.query(ManualReminder).count() == 0
                    context.close()
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    print('Browser reminders smoke passed: modal, validation, CRUD, snooze, completion, '
          'empty states, persistence and desktop/mobile layouts (1440/1024/390/320px).')
    print(f'Temporary screenshots: {previews}')


if __name__ == '__main__':
    run()
