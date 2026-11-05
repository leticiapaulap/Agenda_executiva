"""Vault security checks use generated test keys and disposable databases only."""
import base64
from contextlib import closing
import json
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from agenda.catalog import AccessCatalog, SQLiteCatalogRepository
from agenda.storage import Database
from agenda.vault import VaultCipher, VaultError
from webapp import create_app
from webapp.catalog import AccessEntry, WebCatalogRepository
from webapp.models import db


SECRET = 'Teste-sensível-único-!274§'
ADMIN_PASSWORD = 'admin-test-credential-!41'


def test_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')


def vault_config(keys=None, active='v1', enabled=True):
    return {'VAULT_ENABLED': enabled, 'VAULT_KEYS': json.dumps(keys or {'v1': test_key()}),
            'VAULT_ACTIVE_KEY_ID': active}


class VaultCipherTests(unittest.TestCase):
    def test_authenticated_randomized_encryption_and_record_binding(self):
        config = vault_config()
        cipher = VaultCipher(config)
        self.assertTrue(cipher.available)
        encrypted = cipher.encrypt(SECRET, 'record-a')
        self.assertNotEqual(encrypted, cipher.encrypt(SECRET, 'record-a'))
        self.assertNotIn(SECRET, encrypted)
        self.assertNotEqual(base64.urlsafe_b64encode(SECRET.encode()).decode(), encrypted)
        self.assertEqual(SECRET, cipher.decrypt(encrypted, 'record-a'))
        for other, entry_id in ((encrypted, 'record-b'),
                                (encrypted[:-5] + ('A' if encrypted[-5] != 'A' else 'B') + encrypted[-4:], 'record-a')):
            with self.subTest(entry_id=entry_id), self.assertRaises(VaultError) as caught:
                cipher.decrypt(other, entry_id)
            self.assertNotIn(SECRET, str(caught.exception))
            self.assertNotIn(encrypted, str(caught.exception))

    def test_missing_malformed_or_disabled_configuration_fails_closed(self):
        cases = ({}, {'VAULT_ENABLED': False},
                 {'VAULT_ENABLED': True, 'VAULT_KEYS': '{invalid-sensitive-config', 'VAULT_ACTIVE_KEY_ID': 'v1'},
                 {'VAULT_ENABLED': True, 'VAULT_KEYS': json.dumps({'v1': 'short-secret'}), 'VAULT_ACTIVE_KEY_ID': 'v1'},
                 vault_config(active='missing'))
        for config in cases:
            with self.subTest(config_keys=list(config)):
                cipher = VaultCipher(config)
                self.assertFalse(cipher.available)
                with self.assertRaises(VaultError) as caught:
                    cipher.encrypt(SECRET, 'record')
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertNotIn('short-secret', str(caught.exception))

    def test_wrong_key_cannot_decrypt_and_rotation_preserves_old_data(self):
        original_key, replacement_key = test_key(), test_key()
        original = VaultCipher(vault_config({'v1': original_key}))
        encrypted = original.encrypt(SECRET, 'record')
        wrong = VaultCipher(vault_config({'v1': replacement_key}))
        with self.assertRaises(VaultError):
            wrong.decrypt(encrypted, 'record')
        rotated = VaultCipher(vault_config({'v1': original_key, 'v2': replacement_key}, active='v2'))
        self.assertEqual(SECRET, rotated.decrypt(encrypted, 'record'))
        reencrypted = rotated.rotate(encrypted, 'record')
        newest_only = VaultCipher(vault_config({'v2': replacement_key}, active='v2'))
        self.assertEqual(SECRET, newest_only.decrypt(reencrypted, 'record'))
        with self.assertRaises(VaultError):
            newest_only.decrypt(encrypted, 'record')

    def test_flask_secret_key_is_never_an_implicit_vault_key(self):
        cipher = VaultCipher({'SECRET_KEY': test_key(), 'VAULT_ENABLED': True})
        self.assertFalse(cipher.available)

    def test_malformed_envelopes_and_password_byte_limits_fail_without_details(self):
        cipher = VaultCipher(vault_config())
        for payload in ('', 'v1:unknown:AAAA', 'v2:v1:AAAA', 'sensitive-malformed', 'x' * 7000):
            with self.subTest(length=len(payload)), self.assertRaises(VaultError) as caught:
                cipher.decrypt(payload, 'record')
            self.assertNotIn(payload or 'unmatchable-empty-sentinel', str(caught.exception))
        with self.assertRaises(VaultError):
            cipher.encrypt('ç' * 4096, 'record')


class SQLiteVaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-vault-sqlite-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'agenda.db'
        self.database = Database(self.path)
        self.database.initialize()
        self.config = vault_config()
        self.cipher = VaultCipher(self.config)
        self.repo = SQLiteCatalogRepository(self.database)
        self.service = AccessCatalog(self.repo, cipher=self.cipher)

    def entry(self, **extra):
        values = {'name': 'Serviço pessoal', 'url': 'https://example.org/login',
                  'category': 'Pessoal', 'notes': 'Informação pública',
                  'is_corporate': False, 'password': SECRET}
        values.update(extra)
        return self.service.save_entry(values)

    def test_crud_encrypts_storage_and_never_lists_secrets(self):
        entry_id = self.entry()
        self.assertEqual(SECRET, self.service.reveal_password(entry_id))
        encrypted = self.repo.get_secret(entry_id)
        self.assertTrue(encrypted)
        for entry in [self.service.get_entry(entry_id), *self.service.list_entries('pessoal')]:
            self.assertTrue(entry['has_password'])
            self.assertFalse(entry['is_corporate'])
            self.assertNotIn('password_ciphertext', entry)
            self.assertNotIn('password', entry)
            self.assertNotIn(SECRET, json.dumps(entry))
            self.assertNotIn(encrypted, json.dumps(entry))
        self.assertNotIn(SECRET.encode(), self.path.read_bytes())
        self.service.save_entry({'name': 'Nome atualizado', 'password': ''}, entry_id)
        self.assertEqual(encrypted, self.repo.get_secret(entry_id))
        self.assertEqual(SECRET, self.service.reveal_password(entry_id))
        self.service.save_entry({'name': 'Nome atualizado', 'password': 'Nova-!93'}, entry_id)
        self.assertEqual('Nova-!93', self.service.reveal_password(entry_id))
        self.service.delete_entry(entry_id)
        self.assertIsNone(self.service.get_entry(entry_id))
        self.assertIsNone(self.repo.get_secret(entry_id))

    def test_corporate_and_unclassified_entries_remain_catalog_only(self):
        for values in ({'name': 'Corporativo', 'password': SECRET},
                       {'name': 'Corporativo', 'is_corporate': True, 'password': SECRET}):
            with self.assertRaises(ValueError):
                self.service.save_entry(values)
        entry_id = self.service.save_entry({'name': 'Corporativo'})
        self.assertTrue(self.service.get_entry(entry_id)['is_corporate'])
        self.assertFalse(self.service.get_entry(entry_id)['has_password'])
        self.assertIsNone(self.repo.get_secret(entry_id))

    def test_password_preserves_unicode_and_whitespace_exactly(self):
        password = '  Árvore-! senha\tcom-espaços  '
        entry_id = self.entry(password=password)
        self.assertEqual(password, self.service.reveal_password(entry_id))

    def test_database_enforces_corporate_catalog_rule_atomically(self):
        entry_id = self.entry()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.database._connect() as connection:
                connection.execute('UPDATE access_catalog SET is_corporate=1 WHERE id=?', (entry_id,))
        self.assertFalse(self.service.get_entry(entry_id)['is_corporate'])
        self.assertEqual(SECRET, self.service.reveal_password(entry_id))

    def test_key_loss_keeps_ciphertext_and_metadata_edit_does_not_erase_it(self):
        entry_id = self.entry()
        before = self.repo.get_secret(entry_id)
        disabled = AccessCatalog(self.repo, cipher=VaultCipher({}))
        disabled.save_entry({'name': 'Metadados atualizados', 'password': ''}, entry_id)
        self.assertEqual(before, self.repo.get_secret(entry_id))
        self.assertTrue(disabled.get_entry(entry_id)['has_password'])
        with self.assertRaises(VaultError):
            disabled.reveal_password(entry_id)
        with self.assertRaises(ValueError):
            disabled.save_entry({'name': 'Falha segura', 'password': 'new-sensitive'}, entry_id)
        self.assertEqual(before, self.repo.get_secret(entry_id))
        self.assertEqual(SECRET, self.service.reveal_password(entry_id))

    def test_copied_ciphertext_is_bound_to_its_record(self):
        first = self.entry()
        second = self.entry(name='Outro serviço')
        with self.database._connect() as connection:
            connection.execute('UPDATE access_catalog SET password_ciphertext=? WHERE id=?',
                               (self.repo.get_secret(first), second))
        with self.assertRaises(VaultError):
            self.service.reveal_password(second)
        self.assertEqual(SECRET, self.service.reveal_password(first))

    def test_restart_persists_and_explicit_clear_is_distinct_from_empty_edit(self):
        entry_id = self.entry()
        reopened = Database(self.path)
        reopened.initialize()
        service = AccessCatalog(SQLiteCatalogRepository(reopened), cipher=VaultCipher(self.config))
        self.assertEqual(SECRET, service.reveal_password(entry_id))
        service.save_entry({'name': 'Sem senha', 'clear_password': True}, entry_id)
        self.assertFalse(service.get_entry(entry_id)['has_password'])
        self.assertIsNone(SQLiteCatalogRepository(reopened).get_secret(entry_id))

    def test_sqlite_version_three_migration_preserves_catalog_and_creates_backup(self):
        entry_id = self.service.save_entry({'name': 'Acesso legado', 'url': 'https://example.org'})
        original = self.service.get_entry(entry_id)
        with self.database._connect() as connection:
            connection.execute('ALTER TABLE access_catalog RENAME TO access_catalog_new')
            connection.execute('''CREATE TABLE access_catalog(id TEXT PRIMARY KEY,name TEXT NOT NULL,
                               url TEXT NOT NULL,category TEXT NOT NULL,notes TEXT NOT NULL,updated_at TEXT NOT NULL)''')
            connection.execute('''INSERT INTO access_catalog SELECT id,name,url,category,notes,updated_at
                                  FROM access_catalog_new''')
            connection.execute('DROP TABLE access_catalog_new')
            connection.execute('PRAGMA user_version=3')
        Database(self.path).initialize()
        migrated = self.service.get_entry(entry_id)
        for field in ('id', 'name', 'url', 'category', 'notes', 'updated_at'):
            self.assertEqual(original[field], migrated[field])
        self.assertTrue(migrated['is_corporate'])
        self.assertFalse(migrated['has_password'])
        self.assertTrue(list((self.path.parent / 'backups').glob('*antes_migracao*.db')))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(4, connection.execute('PRAGMA user_version').fetchone()[0])


class WebVaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-vault-web-test-')
        self.addCleanup(self.temp.cleanup)
        self.config = {'TESTING': True, 'SECRET_KEY': 'unit-test-session-key',
                       'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (Path(self.temp.name) / 'web.db').as_posix(),
                       'ADMIN_USERNAME': 'test', 'ADMIN_PASSWORD_HASH': generate_password_hash(ADMIN_PASSWORD),
                       'SESSION_COOKIE_SECURE': False, 'WTF_CSRF_SSL_STRICT': False,
                       **vault_config()}
        self.app = create_app(self.config)
        self.addCleanup(self.dispose)
        result = self.app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if result.exception:
            raise result.exception
        self.client = self.app.test_client()

    def dispose(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def token(self, path='/login', client=None):
        response = (client or self.client).get(path)
        self.assertEqual(200, response.status_code)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match[1].decode()

    def login(self, client=None):
        client = client or self.client
        response = client.post('/login', data={'username': 'test', 'password': ADMIN_PASSWORD,
                                               'csrf_token': self.token(client=client)})
        self.assertEqual(302, response.status_code)

    def seed(self, **extra):
        with self.app.app_context():
            values = {'name': 'Serviço pessoal', 'password': SECRET, 'is_corporate': False}
            values.update(extra)
            return AccessCatalog(WebCatalogRepository(), cipher=VaultCipher(self.config)).save_entry(values)

    def secret(self, entry_id, action='reveal', password=ADMIN_PASSWORD, client=None, csrf=True):
        client = client or self.client
        values = {'action': action, 'reauth_password': password}
        if csrf:
            values['csrf_token'] = self.token('/vault', client)
        return client.post(f'/vault/{entry_id}/secret', data=values)

    def test_create_update_blank_preserves_delete_and_no_initial_exposure(self):
        self.login()
        values = {'csrf_token': self.token('/vault'), 'name': 'Meu serviço', 'password': SECRET,
                  'is_corporate': '0', 'reauth_password': ADMIN_PASSWORD, 'category': 'Pessoal'}
        response = self.client.post('/vault', data=values)
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            row = db.session.query(AccessEntry).one()
            entry_id, encrypted = row.id, row.password_ciphertext
            self.assertNotEqual(SECRET, encrypted)
        for path in ('/vault', f'/vault?edit={entry_id}', '/vault?q=pessoal'):
            response = self.client.get(path)
            self.assertEqual(200, response.status_code)
            self.assertNotIn(SECRET.encode(), response.data)
            self.assertNotIn(encrypted.encode(), response.data)
            self.assertIn('••••••••'.encode(), response.data)
        self.client.post('/vault', data={'csrf_token': self.token('/vault'), 'entry_id': entry_id,
                                         'name': 'Editado', 'password': '', 'is_corporate': '0'})
        self.assertEqual(SECRET, self.secret(entry_id).get_json()['password'])
        self.client.post('/vault', data={'csrf_token': self.token('/vault'), 'entry_id': entry_id,
                                         'name': 'Editado', 'password': 'Atualizada-!26',
                                         'is_corporate': '0', 'reauth_password': ADMIN_PASSWORD})
        self.assertEqual('Atualizada-!26', self.secret(entry_id, action='copy').get_json()['password'])
        self.client.post(f'/vault/{entry_id}/delete', data={'csrf_token': self.token('/vault')})
        with self.app.app_context():
            self.assertIsNone(db.session.get(AccessEntry, entry_id))

    def test_login_csrf_freshness_and_reauthentication_required_for_each_secret(self):
        entry_id = self.seed()
        anonymous = self.app.test_client()
        token = self.token(client=anonymous)
        response = anonymous.post(f'/vault/{entry_id}/secret', data={
            'csrf_token': token, 'action': 'reveal', 'reauth_password': ADMIN_PASSWORD})
        self.assertEqual(302, response.status_code)
        self.assertNotIn(SECRET.encode(), response.data)
        self.login()
        self.assertEqual(405, self.client.get(f'/vault/{entry_id}/secret').status_code)
        self.assertEqual(400, self.secret(entry_id, csrf=False).status_code)
        for password in ('', 'wrong-password'):
            response = self.secret(entry_id, password=password)
            self.assertEqual(401, response.status_code)
            self.assertNotIn(SECRET.encode(), response.data)
        response = self.secret(entry_id)
        self.assertEqual(200, response.status_code)
        self.assertEqual(SECRET, response.get_json()['password'])
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(401, self.secret(entry_id, action='copy', password='').status_code)
        with self.client.session_transaction() as session:
            session['_fresh'] = False
        response = self.secret(entry_id)
        self.assertEqual(401, response.status_code)
        self.assertNotIn(SECRET.encode(), response.data)

    def test_reauthentication_rate_limit_persists_across_sessions(self):
        entry_id = self.seed()
        self.login()
        for _ in range(4):
            self.assertEqual(401, self.secret(entry_id, password='wrong').status_code)
        self.assertEqual(429, self.secret(entry_id, password='wrong').status_code)
        self.assertEqual(429, self.secret(entry_id).status_code)
        other = self.app.test_client()
        self.login(other)
        self.assertEqual(429, self.secret(entry_id, client=other).status_code)

    def test_create_sensitive_data_requires_reauth_and_does_not_echo_on_error(self):
        self.login()
        response = self.client.post('/vault', data={'csrf_token': self.token('/vault'),
            'name': 'Blocked', 'password': SECRET, 'is_corporate': '0', 'reauth_password': 'wrong'})
        self.assertEqual(401, response.status_code)
        self.assertNotIn(SECRET.encode(), response.data)
        with self.app.app_context():
            self.assertEqual(0, db.session.query(AccessEntry).count())
        response = self.client.post('/vault', data={'csrf_token': self.token('/vault'),
            'name': '', 'password': SECRET, 'is_corporate': '0', 'reauth_password': ADMIN_PASSWORD})
        self.assertEqual(400, response.status_code)
        self.assertNotIn(SECRET.encode(), response.data)

    def test_clear_password_alias_cannot_bypass_reauthentication(self):
        entry_id = self.seed()
        self.login()
        for flag in ('1', 'true'):
            response = self.client.post('/vault', data={'csrf_token': self.token('/vault'),
                'entry_id': entry_id, 'name': 'Serviço pessoal', 'is_corporate': '0',
                'password': '', 'clear_password': flag})
            self.assertEqual(401, response.status_code)
            with self.app.app_context():
                self.assertIsNotNone(db.session.get(AccessEntry, entry_id).password_ciphertext)
        response = self.client.post('/vault', data={'csrf_token': self.token('/vault'),
            'entry_id': entry_id, 'name': 'Serviço pessoal', 'is_corporate': '0',
            'clear_password': '1', 'reauth_password': ADMIN_PASSWORD})
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            self.assertIsNone(db.session.get(AccessEntry, entry_id).password_ciphertext)

    def test_key_loss_or_corruption_fails_without_logging_or_rewriting_secret(self):
        entry_id = self.seed()
        self.login()
        with self.app.app_context():
            original = db.session.get(AccessEntry, entry_id).password_ciphertext
        self.app.config['VAULT_KEYS'] = json.dumps({'v1': test_key()})
        with self.assertNoLogs(self.app.logger, level='WARNING'):
            response = self.secret(entry_id)
        self.assertEqual(503, response.status_code)
        self.assertNotIn(SECRET.encode(), response.data)
        self.assertNotIn(original.encode(), response.data)
        with self.app.app_context():
            self.assertEqual(original, db.session.get(AccessEntry, entry_id).password_ciphertext)
        self.app.config['VAULT_KEYS'] = self.config['VAULT_KEYS']
        self.assertEqual(SECRET, self.secret(entry_id).get_json()['password'])

    def test_corporate_storage_denied_and_disabled_vault_remains_catalog(self):
        self.login()
        response = self.client.post('/vault', data={'csrf_token': self.token('/vault'),
            'name': 'Corporativo', 'password': SECRET, 'is_corporate': '1', 'reauth_password': ADMIN_PASSWORD})
        self.assertEqual(400, response.status_code)
        with self.app.app_context():
            self.assertEqual(0, db.session.query(AccessEntry).count())
        self.app.config['VAULT_ENABLED'] = False
        response = self.client.post('/vault', data={'csrf_token': self.token('/vault'),
            'name': 'Pessoal', 'password': SECRET, 'is_corporate': '0', 'reauth_password': ADMIN_PASSWORD})
        self.assertEqual(400, response.status_code)
        self.assertNotIn(SECRET.encode(), response.data)
        response = self.client.post('/vault', data={'csrf_token': self.token('/vault'), 'name': 'Só catálogo'})
        self.assertEqual(302, response.status_code)

    def test_restart_preserves_ciphertext_and_specific_operation_decrypts(self):
        entry_id = self.seed()
        restarted = create_app(self.config)
        try:
            client = restarted.test_client()
            self.login(client)
            response = self.secret(entry_id, client=client)
            self.assertEqual(200, response.status_code)
            self.assertEqual(SECRET, response.get_json()['password'])
        finally:
            with restarted.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_version_three_web_migration_preserves_existing_access_metadata(self):
        old_uri = 'sqlite:///' + (Path(self.temp.name) / 'legacy-access.db').as_posix()
        old_app = create_app({**self.config, 'SQLALCHEMY_DATABASE_URI': old_uri})
        try:
            result = old_app.test_cli_runner().invoke(args=['db', 'upgrade', '0003_access_catalog'])
            if result.exception:
                raise result.exception
            from sqlalchemy import text
            with old_app.app_context():
                db.session.execute(text('''INSERT INTO access_catalog(id,name,url,category,notes,updated_at)
                    VALUES (:id,:name,:url,:category,:notes,:updated_at)'''),
                    {'id': 'existing-access', 'name': 'Serviço legado', 'url': 'https://example.org',
                     'category': 'Geral', 'notes': 'Nota original', 'updated_at': '2026-11-05T12:00:00+00:00'})
                db.session.commit()
            result = old_app.test_cli_runner().invoke(args=['db', 'upgrade'])
            if result.exception:
                raise result.exception
            with old_app.app_context():
                row = db.session.get(AccessEntry, 'existing-access')
                self.assertEqual('Serviço legado', row.name)
                self.assertEqual('https://example.org', row.url)
                self.assertEqual('Geral', row.category)
                self.assertEqual('Nota original', row.notes)
                self.assertEqual('2026-11-05T12:00:00+00:00', row.updated_at)
                self.assertIsNone(row.password_ciphertext)
                self.assertEqual(1, row.is_corporate)
        finally:
            with old_app.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_rotation_cli_validates_without_changes_then_reencrypts_atomically(self):
        entry_id = self.seed()
        old_keys = json.loads(self.config['VAULT_KEYS'])
        with self.app.app_context():
            original = db.session.get(AccessEntry, entry_id).password_ciphertext
        self.app.config.update(vault_config({**old_keys, 'v2': test_key()}, active='v2'))
        result = self.app.test_cli_runner().invoke(args=['vault-rotate'])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertNotIn(SECRET, result.output)
        self.assertNotIn(original, result.output)
        with self.app.app_context():
            self.assertEqual(original, db.session.get(AccessEntry, entry_id).password_ciphertext)
        result = self.app.test_cli_runner().invoke(args=['vault-rotate', '--apply'])
        self.assertEqual(0, result.exit_code, result.output)
        with self.app.app_context():
            replacement = db.session.get(AccessEntry, entry_id).password_ciphertext
        self.assertNotEqual(original, replacement)
        latest_key = json.loads(self.app.config['VAULT_KEYS'])['v2']
        self.assertEqual(SECRET, VaultCipher(vault_config({'v2': latest_key}, active='v2')).decrypt(replacement, entry_id))

    def test_rotation_cli_rolls_back_all_records_on_corrupt_ciphertext(self):
        first, second = self.seed(name='Primeiro'), self.seed(name='Segundo')
        with self.app.app_context():
            row = db.session.get(AccessEntry, second)
            row.password_ciphertext = 'corrupt-sentinel-sensitive-ciphertext'
            db.session.commit()
            before = {entry_id: db.session.get(AccessEntry, entry_id).password_ciphertext for entry_id in (first, second)}
        result = self.app.test_cli_runner().invoke(args=['vault-rotate', '--apply'])
        self.assertNotEqual(0, result.exit_code)
        self.assertNotIn(SECRET, result.output)
        self.assertNotIn('corrupt-sentinel-sensitive-ciphertext', result.output)
        with self.app.app_context():
            after = {entry_id: db.session.get(AccessEntry, entry_id).password_ciphertext for entry_id in (first, second)}
            self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
