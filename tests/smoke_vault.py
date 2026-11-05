"""Desktop vault integration with generated test keys and temporary SQLite only."""
import base64
import json
import os
from pathlib import Path
import secrets
import tempfile
import tkinter as tk
import traceback
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from agenda.feature_views import CORPORATE_SCOPE, PERSONAL_SCOPE, PASSWORD_MASK
from agenda.storage import Database
from agenda.ui import AgendaApp
from tests.smoke_ui import fail_ui, pump, set_field, widget_text, assert_horizontal_fit


def authenticate(app, password):
    dialog = app._vault_auth_dialog
    assert dialog is not None and dialog.winfo_exists()
    set_field(dialog.password, password)
    dialog.confirm()
    pump(app)


def run():
    local_password = 'test-local-reauthentication-only'
    first_password = '  test-sentinel-password-with-spaces  '
    second_password = 'test-updated-password'
    test_config = {'VAULT_ENABLED': 'true', 'VAULT_ACTIVE_KEY_ID': 'test-v1',
                   'VAULT_KEYS': json.dumps({'test-v1': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}),
                   'VAULT_DESKTOP_PASSWORD_HASH': generate_password_hash(local_password)}
    with tempfile.TemporaryDirectory(prefix='agenda-vault-desktop-') as folder, patch.dict(os.environ, test_config):
        database = Database(Path(folder) / 'agenda.db')
        app = AgendaApp(database, enable_reminders=False)
        app.callback_errors = []
        app.report_callback_exception = lambda *exc: app.callback_errors.append(''.join(traceback.format_exception(*exc)))
        try:
            with patch.object(AgendaApp, 'show_error', fail_ui):
                pump(app)
                assert app.desktop_vault_available
                app.navigate('catalog')
                app.open_feature_dialog('access')
                pump(app)
                form = app._dialog
                assert form.inputs['password'].cget('show') == '•'
                assert form.inputs['is_corporate'].get() == CORPORATE_SCOPE
                set_field(form.inputs['name'], 'Acesso pessoal de teste')
                form.inputs['is_corporate'].set(PERSONAL_SCOPE)
                form.update_password_state()
                set_field(form.inputs['password'], first_password)
                form.save()
                pump(app)
                assert not form.saved and app.catalog.list_entries() == []
                authenticate(app, 'incorrect-local-password')
                assert not form.saved
                authenticate(app, local_password)
                assert form.saved
                entry_id = form.saved_id
                entry = app.catalog.get_entry(entry_id)
                assert entry['has_password'] and not entry['is_corporate']
                assert 'password_ciphertext' not in entry
                assert first_password not in widget_text(app.content)
                assert app._vault_labels[entry_id].cget('text') == PASSWORD_MASK
                app.toggle_vault_password(entry, app._vault_labels[entry_id])
                pump(app)
                assert app._vault_labels[entry_id].cget('text') == PASSWORD_MASK
                authenticate(app, local_password)
                assert app._vault_labels[entry_id].cget('text') == first_password
                app.toggle_vault_password(entry, app._vault_labels[entry_id])
                assert app._vault_labels[entry_id].cget('text') == PASSWORD_MASK
                app.copy_vault_password(entry)
                pump(app)
                authenticate(app, local_password)
                assert app.clipboard_get() == first_password
                app.clear_vault_clipboard()
                try:
                    assert app.clipboard_get() != first_password
                except tk.TclError:
                    pass
                app.copy_vault_password(entry)
                pump(app)
                authenticate(app, local_password)
                app.clipboard_clear()
                app.clipboard_append('unrelated-copied-value')
                app.clear_vault_clipboard()
                assert app.clipboard_get() == 'unrelated-copied-value'
                app.open_feature_dialog('access', entry)
                pump(app)
                form = app._dialog
                assert form.inputs['password'].get() == ''
                set_field(form.inputs['notes'], 'Edit without replacing stored password')
                form.save()
                pump(app)
                assert form.saved
                assert app.catalog.reveal_password(entry_id) == first_password
                app.open_feature_dialog('access', app.catalog.get_entry(entry_id))
                pump(app)
                form = app._dialog
                set_field(form.inputs['password'], second_password)
                form.save()
                pump(app)
                authenticate(app, local_password)
                assert form.saved
                assert app.catalog.reveal_password(entry_id) == second_password
                entry = app.catalog.get_entry(entry_id)
                app.toggle_vault_password(entry, app._vault_labels[entry_id])
                pump(app)
                authenticate(app, local_password)
                app.navigate('events')
                assert app._vault_visible == {}
                app.navigate('catalog')
                assert second_password not in widget_text(app.content)
                app.geometry('1000x650')
                pump(app)
                assert_horizontal_fit(app)
                for _ in range(5):
                    assert not app.check_desktop_vault_password('incorrect')[0]
                assert not app.check_desktop_vault_password(local_password)[0]
                app.clear_vault_transients()
                # Reloading the repository and local vault does not reset the ledger.
                app.db = Database(database.path)
                app.db.initialize()
                app.initialize_desktop_vault()
                assert not app.check_desktop_vault_password(local_password)[0]
                # Missing local authentication never enables plaintext operations.
                with patch.dict(os.environ, {'VAULT_DESKTOP_PASSWORD_HASH': ''}):
                    app.initialize_desktop_vault()
                    assert not app.desktop_vault_available
                    app.catalog.save_entry({'name': 'Acesso pessoal de teste', 'notes': 'Preserved while disabled',
                                           'password': ''}, entry_id)
                    assert app.catalog.get_entry(entry_id)['has_password']
                    with database._connect() as connection:
                        ciphertext = connection.execute('SELECT password_ciphertext FROM access_catalog WHERE id=?',
                                                         (entry_id,)).fetchone()[0]
                    assert second_password not in ciphertext
        finally:
            app.close()
    print('Desktop vault smoke passed: masked forms, reauthentication, encryption, preservation, clipboard and persistent throttle.')


if __name__ == '__main__':
    run()
