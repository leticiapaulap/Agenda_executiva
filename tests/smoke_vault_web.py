r"""Optional real-browser vault check; uses temporary SQLite and test-only keys.

Requires Playwright and Microsoft Edge; no browser service or production secrets.
Run: .venv\Scripts\python.exe -m tests.smoke_vault_web
"""
import base64
import json
from pathlib import Path
import secrets
import tempfile
import threading

from playwright.sync_api import expect, sync_playwright
from werkzeug.security import generate_password_hash
from werkzeug.serving import WSGIRequestHandler, make_server

from webapp import create_app
from webapp.models import User, db


EDGE = Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
MASK = '••••••••'


class QuietHandler(WSGIRequestHandler):
    def log(self, log_type, message, *args):
        pass


def run():
    assert EDGE.is_file(), 'Microsoft Edge is required for this optional test.'
    manager_password = 'Browser-test-manager-password'
    entry_password = 'Browser-test-sensitive-password'
    with tempfile.TemporaryDirectory(prefix='agenda-vault-browser-') as folder:
        config = {'TESTING': True, 'SECRET_KEY': secrets.token_hex(32),
                  'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (Path(folder) / 'web.db').as_posix(),
                  'ADMIN_USERNAME': 'browser-test', 'ADMIN_PASSWORD_HASH': generate_password_hash(manager_password),
                  'SESSION_COOKIE_SECURE': False, 'WTF_CSRF_SSL_STRICT': False,
                  'VAULT_ENABLED': True, 'VAULT_ACTIVE_KEY_ID': 'test-v1',
                  'VAULT_KEYS': json.dumps({'test-v1': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()})}
        app = create_app(config)
        migration = app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if migration.exception:
            raise migration.exception
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=str(EDGE), headless=True,
                    args=['--disable-background-networking', '--no-first-run'])
                try:
                    context = browser.new_context(viewport={'width': 1240, 'height': 900})
                    context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=origin)
                    page = context.new_page()
                    page.clock.install()
                    page.on('pageerror', lambda error: errors.append(type(error).__name__))
                    page.goto(origin + '/login')
                    page.locator('input[name="username"]').fill('browser-test')
                    page.locator('input[name="password"]').fill(manager_password)
                    page.locator('button[type="submit"]').click()
                    expect(page.get_by_role('heading', name='Bom dia', exact=True)).to_be_visible()
                    with app.app_context():
                        user = db.session.query(User).filter_by(email='browser-test').one()
                        user.vault_access = True
                        db.session.commit()
                    page.reload()
                    page.locator('nav').get_by_role('link', name='Senhas', exact=False).click()
                    expect(page.get_by_role('heading', name='Senhas', exact=True)).to_be_visible()
                    form = page.locator('form[action="/vault"][method="post"]')
                    expect(form.locator('input[name="password"]')).to_be_disabled()
                    form.locator('input[name="name"]').fill('Pessoal de teste no navegador')
                    form.locator('input[name="username"]').fill('browser-user@example.test')
                    form.locator('input[name="url"]').fill('https://example.org/login')
                    form.locator('select[name="is_corporate"]').select_option('0')
                    form.locator('input[name="password"]').fill(entry_password)
                    form.locator('input[name="reauth_password"]').fill(manager_password)
                    form.get_by_role('button', name='Salvar acesso').click()
                    record = page.locator('article.record').filter(has_text='Pessoal de teste no navegador')
                    expect(record).to_be_visible()
                    expect(record.locator('[data-vault-value]')).to_have_text(MASK)
                    assert entry_password not in page.content()
                    assert manager_password not in page.content()
                    record.get_by_role('button', name='Copiar usuário').click()
                    expect(page.locator('#vault-feedback')).to_have_text('Usuário copiado.')
                    assert page.evaluate('navigator.clipboard.readText()') == 'browser-user@example.test'
                    page.locator('form.filter-bar input[name="q"]').fill('browser-user@example.test')
                    page.locator('form.filter-bar button').click()
                    expect(page.locator('article.record')).to_have_count(1)
                    response = context.request.get(origin + '/vault')
                    assert response.status == 200 and entry_password not in response.text()
                    # Reveal: wrong reauthentication must never change the masked card.
                    record.get_by_role('button', name='Visualizar senha').click()
                    dialog = page.locator('#vault-auth')
                    expect(dialog).to_be_visible()
                    page.locator('#vault-reauth-password').fill('incorrect-password')
                    dialog.get_by_role('button', name='Confirmar', exact=True).click()
                    expect(page.locator('#vault-auth-error')).to_contain_text('incorreta')
                    expect(record.locator('[data-vault-value]')).to_have_text(MASK)
                    page.locator('#vault-reauth-password').fill(manager_password)
                    dialog.get_by_role('button', name='Confirmar', exact=True).click()
                    expect(record.locator('[data-vault-value]')).to_have_text(entry_password)
                    expect(dialog).not_to_be_visible()
                    page.clock.run_for(30001)
                    expect(record.locator('[data-vault-value]')).to_have_text(MASK)
                    record.get_by_role('button', name='Visualizar senha').click()
                    page.locator('#vault-reauth-password').fill(manager_password)
                    dialog.get_by_role('button', name='Confirmar', exact=True).click()
                    expect(record.locator('[data-vault-value]')).to_have_text(entry_password)
                    record.get_by_role('button', name='Ocultar senha').click()
                    expect(record.locator('[data-vault-value]')).to_have_text(MASK)
                    # A metadata-only edit must not prefill or erase the stored password.
                    record.get_by_role('link', name='Editar').click()
                    form = page.locator('form[action="/vault"][method="post"]')
                    expect(form.locator('input[name="password"]')).to_have_value('')
                    assert entry_password not in page.content()
                    form.locator('textarea[name="notes"]').fill('Metadados atualizados sem substituir a senha')
                    form.get_by_role('button', name='Salvar acesso').click()
                    record = page.locator('article.record').filter(has_text='Pessoal de teste no navegador')
                    expect(record.locator('[data-vault-value]')).to_have_text(MASK)
                    record.get_by_role('button', name='Copiar senha', exact=True).click()
                    dialog = page.locator('#vault-auth')
                    expect(dialog).to_be_visible()
                    page.locator('#vault-reauth-password').fill(manager_password)
                    dialog.get_by_role('button', name='Confirmar', exact=True).click()
                    expect(page.locator('#vault-feedback')).to_contain_text('Senha copiada')
                    assert page.evaluate('navigator.clipboard.readText()') == entry_password
                    expect(record.locator('[data-vault-value]')).to_have_text(MASK)
                    page.clock.run_for(30001)
                    page.wait_for_function('navigator.clipboard.readText().then(value => value === "")')
                    record.get_by_role('button', name='Copiar senha', exact=True).click()
                    page.locator('#vault-reauth-password').fill(manager_password)
                    dialog.get_by_role('button', name='Confirmar', exact=True).click()
                    expect(dialog).not_to_be_visible()
                    assert page.evaluate('navigator.clipboard.readText()') == entry_password
                    page.evaluate('navigator.clipboard.writeText("unrelated-test-clipboard-value")')
                    page.clock.run_for(30001)
                    assert page.evaluate('navigator.clipboard.readText()') == 'unrelated-test-clipboard-value'
                    # Narrow layout must stay within the viewport.
                    page.set_viewport_size({'width': 390, 'height': 844})
                    expect(record).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile overflow'
                    page.once('dialog', lambda dialog: dialog.dismiss())
                    record.locator('form[data-confirm] button').click()
                    expect(record).to_be_visible()
                    # Disabling the keys leaves metadata and the encrypted record intact.
                    app.config['VAULT_ENABLED'] = False
                    page.reload()
                    expect(page.locator('input[name="password"]')).to_be_disabled()
                    expect(record.get_by_role('button', name='Visualizar senha')).to_be_disabled()
                    expect(record.get_by_role('button', name='Copiar senha', exact=True)).to_be_disabled()
                    assert entry_password not in page.content()
                    app.config['VAULT_ENABLED'] = True
                    page.reload()
                    record = page.locator('article.record').filter(has_text='Pessoal de teste no navegador')
                    page.once('dialog', lambda dialog: dialog.accept())
                    record.locator('form[data-confirm] button').click()
                    expect(page.get_by_text('Nenhum acesso encontrado')).to_be_visible()
                    with app.app_context():
                        from webapp.catalog import AccessEntry
                        assert db.session.query(AccessEntry).count() == 0
                    assert not errors, 'The browser reported a JavaScript error.'
                    # Clear test data from the host clipboard before closing the context.
                    page.evaluate('navigator.clipboard.writeText("")')
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
    print('Browser vault smoke passed: authentication, masked HTML, reveal/hide/copy, preservation, disabled keys and mobile layout.')


if __name__ == '__main__':
    run()
