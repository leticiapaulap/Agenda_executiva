"""Real PostgreSQL integration in a unique schema of an explicitly disposable DB."""
import base64
from datetime import date, time
import json
import os
import re
import secrets
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from werkzeug.security import generate_password_hash

from agenda.vault import VaultCipher
from webapp import create_app
from webapp.catalog import AccessEntry, WebCatalogRepository
from webapp.models import Event, Task, User, db


ACCOUNT_PASSWORD = 'Postgres-test-account-password!'
STORED_PASSWORD = 'Postgres-test-vault-password!'
ORIGIN = 'https://localhost'


@unittest.skipUnless(os.environ.get('TEST_POSTGRES_DATABASE_URL'),
                     'Set TEST_POSTGRES_DATABASE_URL to a disposable PostgreSQL test database.')
class PasswordPostgresTests(unittest.TestCase):
    def setUp(self):
        database_url = make_url(os.environ['TEST_POSTGRES_DATABASE_URL'])
        if database_url.get_backend_name() != 'postgresql':
            self.fail('The integration database must use PostgreSQL.')
        if 'test' not in (database_url.database or '').casefold():
            self.fail('The disposable database name must contain "test".')
        database_url = database_url.set(drivername='postgresql+psycopg')
        self.schema = 'vault_test_' + uuid4().hex
        engine = create_engine(database_url, hide_parameters=True)
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))

        def remove_test_schema():
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))

        self.addCleanup(remove_test_schema)
        self.config = {
            'TESTING': True, 'SECRET_KEY': secrets.token_hex(32),
            'SQLALCHEMY_DATABASE_URI': database_url.update_query_dict({
                'options': f'-csearch_path={self.schema},public',
            }).render_as_string(hide_password=False),
            'SESSION_COOKIE_SECURE': True, 'WTF_CSRF_SSL_STRICT': True,
            'VAULT_ENABLED': True, 'VAULT_ACTIVE_KEY_ID': 'existing',
            'VAULT_KEYS': json.dumps({
                'existing': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
            }),
        }
        self.app = self.new_app()
        migration = self.app.test_cli_runner().invoke(args=['db', 'upgrade', '0007_admin_recovery'])
        if migration.exception:
            raise migration.exception
        self.original_ciphertext = VaultCipher(self.config).encrypt(STORED_PASSWORD, 'legacy-access')
        with self.app.app_context():
            db.session.add_all([
                User(id='legacy-admin', name='Administrador de teste', email='admin@example.test',
                     password_hash=generate_password_hash(ACCOUNT_PASSWORD), role='admin',
                     active=True, agenda_access=True, finance_access=True, vault_access=True),
                User(id='legacy-manager', name='Gestor sem cofre', email='manager@example.test',
                     password_hash=generate_password_hash(ACCOUNT_PASSWORD), role='manager',
                     active=True, agenda_access=True, vault_access=False),
            ])
            db.session.flush()
            db.session.add_all([
                Event(id=41, title='Compromisso preservado', event_date=date(2026, 11, 12),
                      event_time=time(10, 30), owner_id='legacy-admin'),
                Task(id=51, title='Tarefa preservada', description='Descrição original',
                     due_date=date(2026, 11, 15), priority='Alta', owner_id='legacy-admin'),
            ])
            # This fixture precedes the username column; preserve the old envelope exactly.
            db.session.execute(text('''INSERT INTO access_catalog
                (id,name,url,category,notes,updated_at,password_ciphertext,is_corporate)
                VALUES ('legacy-access','Acesso preservado','https://example.test/login',
                        'Pessoal','Nota original','2026-11-01T09:00:00+00:00',:secret,0)'''),
                {'secret': self.original_ciphertext})
            db.session.commit()

    def new_app(self):
        app = create_app(self.config)

        def dispose():
            with app.app_context():
                db.session.remove()
                db.engine.dispose()

        self.addCleanup(dispose)
        return app

    def snapshot(self):
        with self.app.app_context():
            tables = inspect(db.engine).get_table_names(schema=self.schema)
            return {table: [dict(row) for row in db.session.execute(
                text(f'SELECT * FROM "{self.schema}"."{table}" ORDER BY 1')).mappings()]
                for table in tables if table != 'alembic_version'}

    def get(self, client, path):
        return client.get(path, base_url=ORIGIN)

    def token(self, client, path='/vault'):
        response = self.get(client, path)
        self.assertEqual(200, response.status_code)
        return re.search(rb'name="csrf_token" value="([^"]+)"', response.data)[1].decode()

    def post(self, client, path, data):
        return client.post(path, base_url=ORIGIN, headers={'Referer': ORIGIN + '/vault'}, data=data)

    def login(self, client, email='admin@example.test'):
        response = self.post(client, '/login', {
            'csrf_token': self.token(client, '/login'), 'username': email,
            'password': ACCOUNT_PASSWORD,
        })
        self.assertEqual(302, response.status_code)

    def test_migration_encrypted_crud_and_https_security_preserve_legacy_data(self):
        before = self.snapshot()
        migration = self.app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if migration.exception:
            raise migration.exception
        after = self.snapshot()
        self.assertEqual('', after['access_catalog'][0].pop('username'))
        self.assertEqual(before, after)
        client = self.app.test_client()
        self.login(client)
        for path in ('/vault', '/vault?edit=legacy-access', '/vault/legacy-access/delete'):
            response = self.get(client, path)
            self.assertEqual(200, response.status_code)
            self.assertIn('no-store', response.headers['Cache-Control'])
            for sensitive in (ACCOUNT_PASSWORD, STORED_PASSWORD, self.original_ciphertext):
                self.assertNotIn(sensitive.encode(), response.data)
        token = self.token(client)
        path = '/vault/legacy-access/secret'
        payload = {'action': 'reveal', 'reauth_password': ACCOUNT_PASSWORD}
        for headers in ({'Referer': ORIGIN + '/vault'},
                        {'Referer': 'https://external.example.test/', 'X-CSRFToken': token}):
            response = client.post(path, base_url=ORIGIN, json=payload, headers=headers)
            self.assertEqual(400, response.status_code)
            self.assertNotIn(STORED_PASSWORD.encode(), response.data)
        for password, status in (('wrong-password', 401), (ACCOUNT_PASSWORD, 200), ('', 401)):
            response = client.post(path, base_url=ORIGIN,
                headers={'Referer': ORIGIN + '/vault', 'X-CSRFToken': token},
                json={'action': 'copy', 'reauth_password': password})
            self.assertEqual(status, response.status_code)
            if status == 200:
                self.assertEqual(STORED_PASSWORD, response.get_json()['password'])
            else:
                self.assertNotIn(STORED_PASSWORD.encode(), response.data)

        new_password = 'Postgres-new-secret-§-2026!'
        response = self.post(client, '/vault', {
            'csrf_token': token, 'name': 'Novo serviço', 'username': 'search-user@example.test',
            'url': 'https://example.test/service', 'category': 'Pessoal', 'notes': 'Nota de teste',
            'password': new_password, 'is_corporate': '1', 'reauth_password': ACCOUNT_PASSWORD,
        })
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            entry = db.session.query(AccessEntry).filter_by(username='search-user@example.test').one()
            entry_id, ciphertext = entry.id, entry.password_ciphertext
            self.assertTrue(ciphertext.startswith('v1:existing:'))
            self.assertEqual(1, entry.is_corporate)
            self.assertNotIn(new_password, ciphertext)
            self.assertEqual(new_password, VaultCipher(self.config).decrypt(ciphertext, entry_id))
            self.assertNotIn('password_ciphertext', WebCatalogRepository().get_entry(entry_id))

        # A second Flask process reads the same PostgreSQL data and original keyring.
        restarted = self.new_app()
        client = restarted.test_client()
        self.login(client)
        response = self.get(client, '/vault?q=search-user')
        self.assertIn(b'Novo servi', response.data)
        self.assertNotIn(new_password.encode(), response.data)
        self.assertNotIn(b'Acesso preservado', response.data)
        token = self.token(client)
        response = self.post(client, '/vault', {
            'csrf_token': token, 'entry_id': entry_id, 'name': 'Serviço editado',
            'username': 'updated@example.test', 'password': '', 'is_corporate': '1',
        })
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            self.assertEqual(ciphertext, db.session.get(AccessEntry, entry_id).password_ciphertext)
        response = self.post(client, f'/vault/{entry_id}/secret', {
            'csrf_token': token, 'action': 'copy', 'reauth_password': ACCOUNT_PASSWORD,
        })
        self.assertEqual(200, response.status_code)
        self.assertEqual(new_password, response.get_json()['password'])
        path = f'/vault/{entry_id}/delete'
        self.assertEqual(400, self.post(client, path, {'csrf_token': token}).status_code)
        self.assertEqual(200, self.get(client, path).status_code)
        self.assertEqual(302, self.post(client, path, {'csrf_token': token, 'confirm': '1'}).status_code)
        with self.app.app_context():
            self.assertIsNone(db.session.get(AccessEntry, entry_id))
            self.assertEqual(self.original_ciphertext,
                             db.session.get(AccessEntry, 'legacy-access').password_ciphertext)

        self.assertIn(b'Tarefa preservada', self.get(client, '/tasks/archive').data)
        self.assertEqual(410, self.post(client, '/tasks', {'csrf_token': token, 'title': 'Recusada'}).status_code)
        self.assertEqual(before['tasks'], self.snapshot()['tasks'])
        self.assertEqual(before['events'], self.snapshot()['events'])
        denied = restarted.test_client()
        self.login(denied, 'manager@example.test')
        denied_token = self.token(denied, '/events')
        for path in ('/vault', '/vault?edit=legacy-access', '/vault/legacy-access/delete'):
            self.assertEqual(403, self.get(denied, path).status_code)
        self.assertEqual(403, self.post(denied, '/vault/legacy-access/secret', {
            'csrf_token': denied_token, 'action': 'reveal', 'reauth_password': ACCOUNT_PASSWORD,
        }).status_code)
        self.assertEqual(403, self.post(denied, '/vault/legacy-access/delete', {
            'csrf_token': denied_token, 'confirm': '1',
        }).status_code)
        self.assertEqual(self.config['VAULT_KEYS'], restarted.config['VAULT_KEYS'])


if __name__ == '__main__':
    unittest.main()
