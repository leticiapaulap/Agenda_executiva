"""Password-section regressions using disposable databases and generated keys."""
import base64
from io import StringIO
import json
import logging
from pathlib import Path
import re
import secrets
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from agenda.vault import VaultCipher
from webapp import create_app
from webapp.models import db


LOGIN_PASSWORD = 'Migration-account-test-2026!'
VAULT_PASSWORD = 'Migration-vault-test-secret-2026!'


class PasswordMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-password-migration-')
        self.addCleanup(self.temp.cleanup)
        self.config = {
            'TESTING': True,
            'SECRET_KEY': 'migration-test-session-key',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (Path(self.temp.name) / 'web.db').as_posix(),
            'SESSION_COOKIE_SECURE': False,
            'WTF_CSRF_SSL_STRICT': False,
            'VAULT_ENABLED': True,
            'VAULT_KEYS': json.dumps({'existing': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}),
            'VAULT_ACTIVE_KEY_ID': 'existing',
        }
        self.app = create_app(self.config)
        self.addCleanup(self.dispose)
        self.upgrade('0007_admin_recovery')
        self.encrypted = VaultCipher(self.config).encrypt(VAULT_PASSWORD, 'preserved-access')
        with self.app.app_context():
            db.session.execute(text('''INSERT INTO users
                (id,name,email,password_hash,role,active,session_version,agenda_access,
                 finance_access,vault_access,created_at,last_login)
                VALUES ('preserved-user','Administrador','migration@example.test',:hash,
                        'admin',1,3,1,1,1,'2026-11-01 09:00:00',NULL)'''),
                {'hash': generate_password_hash(LOGIN_PASSWORD)})
            db.session.execute(text('''INSERT INTO events
                (id,title,event_date,event_time,notes,owner_id)
                VALUES (41,'Compromisso original','2026-11-12','10:30:00',
                        'Observação original','preserved-user')'''))
            db.session.execute(text('''INSERT INTO tasks
                (id,title,description,due_date,priority,completed,owner_id)
                VALUES (51,'Tarefa original','Descrição original','2026-11-15',
                        'Alta',1,'preserved-user')'''))
            db.session.execute(text('''INSERT INTO access_catalog
                (id,name,url,category,notes,updated_at,password_ciphertext,is_corporate)
                VALUES ('preserved-access','Acesso original','https://example.test/login',
                        'Pessoal','Observação pública','2026-11-01T09:00:00+00:00',:secret,0)'''),
                {'secret': self.encrypted})
            db.session.commit()

    def dispose(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def upgrade(self, revision='head'):
        result = self.app.test_cli_runner().invoke(args=['db', 'upgrade', revision])
        if result.exception:
            raise result.exception
        self.assertEqual(0, result.exit_code)

    def snapshot(self):
        with self.app.app_context():
            tables = inspect(db.engine).get_table_names()
            return {table: [dict(row) for row in db.session.execute(
                text(f'SELECT * FROM "{table}"')).mappings()]
                for table in tables if table != 'alembic_version'}

    def csrf(self, client, path):
        response = client.get(path)
        self.assertEqual(200, response.status_code)
        return re.search(rb'name="csrf_token" value="([^"]+)"', response.data)[1].decode()

    def login(self):
        client = self.app.test_client()
        response = client.post('/login', data={
            'username': 'migration@example.test', 'password': LOGIN_PASSWORD,
            'csrf_token': self.csrf(client, '/login'),
        })
        self.assertEqual(302, response.status_code)
        return client

    def test_upgrade_preserves_every_existing_row_and_uses_original_vault_key(self):
        before = self.snapshot()
        self.upgrade()
        after = self.snapshot()
        self.assertEqual(set(before), set(after))
        self.assertEqual('', after['access_catalog'][0].pop('username'))
        self.assertEqual(before, after)
        self.assertEqual(self.config['VAULT_KEYS'], self.app.config['VAULT_KEYS'])
        client = self.login()
        for path in ('/vault', '/vault?edit=preserved-access'):
            response = client.get(path)
            self.assertEqual(200, response.status_code)
            self.assertNotIn(VAULT_PASSWORD.encode(), response.data)
            self.assertNotIn(self.encrypted.encode(), response.data)
        response = client.post('/vault/preserved-access/secret', data={
            'action': 'reveal', 'reauth_password': LOGIN_PASSWORD,
            'csrf_token': self.csrf(client, '/vault'),
        })
        self.assertEqual(200, response.status_code)
        self.assertEqual(VAULT_PASSWORD, response.get_json()['password'])
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_upgrade_changes_only_username_column_and_preserves_constraints(self):
        def schema():
            with self.app.app_context():
                inspector = inspect(db.engine)
                return {table: {
                    'columns': [{**column, 'type': str(column['type'])}
                                for column in inspector.get_columns(table)],
                    # SQLite's reflection can append an ADD COLUMN clause to
                    # reflected CHECK text. Compare names and exercise the
                    # security constraints below instead of trusting that text.
                    'checks': sorted(check['name'] or ''
                                     for check in inspector.get_check_constraints(table)),
                    'foreign_keys': inspector.get_foreign_keys(table),
                    'primary_key': inspector.get_pk_constraint(table),
                } for table in inspector.get_table_names()}
        before = schema()
        self.upgrade('0008_access_username')
        after = schema()
        username = after['access_catalog']['columns'].pop()
        self.assertEqual('username', username['name'])
        self.assertEqual('VARCHAR(254)', username['type'])
        self.assertFalse(username['nullable'])
        self.assertEqual("''", username['default'])
        self.assertEqual(before, after)
        rejected_updates = (
            'UPDATE access_catalog SET is_corporate=1',
            'UPDATE access_catalog SET is_corporate=2, password_ciphertext=NULL',
            'UPDATE tasks SET completed=2',
            'UPDATE events SET completed=2',
            "UPDATE users SET role='reader'",
            'UPDATE users SET session_version=0',
        )
        with self.app.app_context():
            for statement in rejected_updates:
                with self.subTest(statement=statement), self.assertRaises(IntegrityError):
                    with db.session.begin_nested():
                        db.session.execute(text(statement))

    def test_corporate_policy_migration_preserves_all_rows_columns_and_original_key(self):
        self.upgrade('0008_access_username')
        with self.app.app_context():
            db.session.execute(text('''INSERT INTO access_catalog
                (id,name,url,username,category,notes,updated_at,is_corporate,password_ciphertext)
                VALUES ('legacy-corporate','Corporativo original','https://example.test',
                        'corporate@example.test','Trabalho','Nota original',
                        '2026-11-01T09:00:00+00:00',1,NULL)'''))
            db.session.commit()
            before_columns = [(column['name'], str(column['type']), column['nullable'], column['default'])
                              for column in inspect(db.engine).get_columns('access_catalog')]
        before = self.snapshot()
        self.upgrade()
        self.assertEqual(before, self.snapshot())
        with self.app.app_context():
            after_columns = [(column['name'], str(column['type']), column['nullable'], column['default'])
                             for column in inspect(db.engine).get_columns('access_catalog')]
            self.assertEqual(before_columns, after_columns)
            self.assertEqual(['ck_access_catalog_is_corporate'], [
                check['name'] for check in inspect(db.engine).get_check_constraints('access_catalog')])
            with self.assertRaises(IntegrityError):
                with db.session.begin_nested():
                    db.session.execute(text('UPDATE access_catalog SET is_corporate=2'))
        client = self.login()
        response = client.post('/vault', data={
            'csrf_token': self.csrf(client, '/vault'), 'entry_id': 'legacy-corporate',
            'name': 'Corporativo original', 'username': 'corporate@example.test',
            'is_corporate': '1', 'password': VAULT_PASSWORD, 'reauth_password': LOGIN_PASSWORD,
        })
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            row = db.session.execute(text(
                "SELECT is_corporate,password_ciphertext FROM access_catalog WHERE id='legacy-corporate'"
            )).mappings().one()
            self.assertEqual(1, row['is_corporate'])
            self.assertEqual(VAULT_PASSWORD, VaultCipher(self.config).decrypt(
                row['password_ciphertext'], 'legacy-corporate'))
        self.assertEqual(self.config['VAULT_KEYS'], self.app.config['VAULT_KEYS'])

    def test_authentication_and_storage_failures_do_not_echo_or_log_secrets(self):
        self.upgrade()
        client = self.login()
        captured = StringIO()
        handler = logging.StreamHandler(captured)
        self.app.logger.addHandler(handler)
        self.addCleanup(self.app.logger.removeHandler, handler)
        response = client.post('/vault/preserved-access/secret', data={
            'action': 'copy', 'reauth_password': VAULT_PASSWORD,
            'csrf_token': self.csrf(client, '/vault'),
        })
        self.assertEqual(401, response.status_code)
        self.assertNotIn(VAULT_PASSWORD.encode(), response.data)
        with patch('webapp.catalog.AccessCatalog.save_entry', side_effect=RuntimeError(
                f'Failure containing {VAULT_PASSWORD} and {self.encrypted}')):
            response = client.post('/vault', data={
                'entry_id': 'preserved-access', 'name': 'Alteração recusada',
                'is_corporate': '0', 'password': VAULT_PASSWORD,
                'reauth_password': LOGIN_PASSWORD,
                'csrf_token': self.csrf(client, '/vault'),
            })
        self.assertEqual(400, response.status_code)
        for value in (LOGIN_PASSWORD, VAULT_PASSWORD, self.encrypted,
                      self.config['VAULT_KEYS']):
            self.assertNotIn(value, captured.getvalue())
            self.assertNotIn(value.encode(), response.data)
        with self.app.app_context():
            self.assertEqual(self.encrypted, db.session.execute(text(
                "SELECT password_ciphertext FROM access_catalog WHERE id='preserved-access'"
            )).scalar_one())


if __name__ == '__main__':
    unittest.main()
