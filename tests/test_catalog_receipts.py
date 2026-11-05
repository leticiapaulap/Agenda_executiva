from io import BytesIO
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest

from PIL import Image
from werkzeug.security import generate_password_hash

from agenda.catalog import AccessCatalog, SQLiteCatalogRepository
from agenda.receipts import MAX_RECEIPT_BYTES, ReceiptStore
from agenda.storage import Database
from webapp import create_app
from webapp.catalog import AccessEntry
from webapp.models import db


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-catalog-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'agenda.db'
        self.database = Database(self.path)
        self.database.initialize()
        self.service = AccessCatalog(SQLiteCatalogRepository(self.database))

    def test_catalog_crud_restart_and_existing_data_preserved(self):
        event_id = self.database.save_event({'title': 'Preservado', 'event_date': '2026-12-10',
                                             'event_time': '09:00'})
        entry_id = self.service.save_entry({'name': 'Portal', 'url': 'https://example.org/login',
                                           'category': 'Trabalho', 'notes': 'Solicitar autorização'})
        self.assertEqual(1, len(self.service.list_entries('trabalho')))
        self.service.save_entry({'name': 'Portal atualizado', 'category': 'Pessoal'}, entry_id)
        reopened = Database(self.path)
        reopened.initialize()
        service = AccessCatalog(SQLiteCatalogRepository(reopened))
        self.assertEqual('Portal atualizado', service.get_entry(entry_id)['name'])
        self.assertEqual('Preservado', reopened.get_event(event_id)['title'])
        service.delete_entry(entry_id)
        self.assertIsNone(service.get_entry(entry_id))

    def test_credential_fields_and_unsafe_urls_rejected(self):
        for field in ('password', 'encrypted_password', 'username', 'email', 'token'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.service.save_entry({'name': 'Portal', field: 'never-store-me'})
        for url in ('javascript:alert(1)', 'https://user:pass@example.org',
                    'https://example.org?token=secret', 'https://example.org/#secret',
                    'https://example.org/\n', 'https://example.org\\@evil.org'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.service.save_entry({'name': 'Portal', 'url': url})
        self.assertEqual([], self.service.list_entries())
        with closing(sqlite3.connect(self.path)) as connection:
            columns = {row[1] for row in connection.execute('PRAGMA table_info(access_catalog)')}
        self.assertEqual({'id', 'name', 'url', 'category', 'notes', 'updated_at',
                          'password_ciphertext', 'is_corporate'}, columns)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-receipt-test-')
        self.addCleanup(self.temp.cleanup)
        self.store = ReceiptStore(Path(self.temp.name) / 'private')

    def png(self):
        buffer = BytesIO()
        Image.new('RGB', (20, 10), 'white').save(buffer, 'PNG')
        return buffer.getvalue()

    def test_validated_opaque_file_persists_and_path_traversal_rejected(self):
        receipt = self.store.save(BytesIO(self.png()), '../../nome-privado.png')
        self.assertRegex(receipt['id'], r'^[0-9a-f]{32}\.png$')
        self.assertEqual('comprovante.png', receipt['name'])
        reopened = ReceiptStore(self.store.root)
        with Image.open(reopened.open(receipt['id'])) as image:
            self.assertEqual((20, 10), image.size)
            self.assertEqual({}, image.info)
        for value in ('../secret.png', '/etc/passwd', 'x.png', 'a' * 32 + '.pdf'):
            with self.assertRaises(ValueError):
                reopened.open(value)
        reopened.delete(receipt['id'])
        with self.assertRaises(FileNotFoundError):
            reopened.open(receipt['id'])

    def test_rejects_executable_disguised_truncated_oversize_and_wrong_format(self):
        for body, name in ((b'<script>alert(1)</script>', 'x.png'),
                           (b'%PDF-1.7', 'x.pdf'), (self.png(), 'x.html'),
                           (self.png(), 'x.jpg'), (self.png()[:25], 'x.png'),
                           (b'x' * (MAX_RECEIPT_BYTES + 1), 'x.png')):
            with self.subTest(name=name, size=len(body)), self.assertRaises(ValueError):
                self.store.save(BytesIO(body), name)
        self.assertFalse(self.store.root.exists())


class WebCatalogSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-vault-web-test-')
        self.addCleanup(self.temp.cleanup)
        self.config = {'TESTING': True, 'SECRET_KEY': 'only-tests',
                       'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (Path(self.temp.name) / 'web.db').as_posix(),
                       'ADMIN_USERNAME': 'test', 'ADMIN_PASSWORD_HASH': generate_password_hash('test-password'),
                       'SESSION_COOKIE_SECURE': False, 'WTF_CSRF_SSL_STRICT': False}
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
        return re.search(rb'name="csrf_token" value="([^"]+)"', response.data)[1].decode()

    def login(self, client=None):
        client = client or self.client
        client.post('/login', data={'username': 'test', 'password': 'test-password',
                                    'csrf_token': self.token(client=client)})
        with self.app.app_context():
            from webapp.models import User
            user = db.session.query(User).filter_by(email='test').one()
            user.vault_access = True
            db.session.commit()

    def test_routes_auth_csrf_and_no_reveal_api(self):
        self.assertEqual(302, self.client.get('/vault').status_code)
        token = self.token()
        self.assertEqual(302, self.client.post('/vault', data={'csrf_token': token, 'name': 'Blocked'}).status_code)
        self.login()
        self.assertEqual(400, self.client.post('/vault', data={'name': 'Blocked'}).status_code)
        for endpoint in ('/vault/anything/reveal', '/vault/anything/copy', '/api/vault'):
            self.assertEqual(404, self.client.get(endpoint).status_code)
        with self.app.app_context():
            self.assertEqual(0, db.session.query(AccessEntry).count())

    def test_credential_submission_not_stored_echoed_or_logged(self):
        self.login()
        secret = 'sentinel-sensitive-should-never-appear'
        with self.assertNoLogs(self.app.logger, level='WARNING'):
            response = self.client.post('/vault', data={'name': 'Portal', 'password': secret,
                                                        'csrf_token': self.token('/vault')})
        self.assertEqual(400, response.status_code)
        self.assertNotIn(secret.encode(), response.data)
        self.assertEqual('no-store', response.headers['Cache-Control'])
        with self.app.app_context():
            self.assertEqual(0, db.session.query(AccessEntry).count())

    def test_crud_escaped_content_restart_delete(self):
        self.login()
        response = self.client.post('/vault', data={'csrf_token': self.token('/vault'),
                                    'name': '<script>alert(1)</script>', 'category': 'Pessoal',
                                    'url': 'https://example.org/login', 'notes': 'Sem informações sensíveis'})
        self.assertEqual(302, response.status_code)
        response = self.client.get('/vault?q=pessoal')
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertNotIn(b'<script>alert(1)</script>', response.data)
        with self.app.app_context():
            entry_id = db.session.query(AccessEntry).one().id
        self.client.post('/vault', data={'csrf_token': self.token('/vault'), 'entry_id': entry_id,
                                         'name': 'Atualizado', 'category': 'Trabalho'})
        restarted = create_app(self.config)
        try:
            client = restarted.test_client()
            self.login(client)
            self.assertIn(b'Atualizado', client.get('/vault').data)
            self.assertEqual(400, client.post(f'/vault/{entry_id}/delete').status_code)
            client.post(f'/vault/{entry_id}/delete', data={'csrf_token': self.token('/vault', client)})
            with restarted.app_context():
                self.assertEqual(0, db.session.query(AccessEntry).count())
        finally:
            with restarted.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_private_receipt_configuration_refuses_public_or_relative_roots(self):
        for root in ('relative-folder', str(Path(self.app.root_path) / 'static' / 'receipts')):
            with self.subTest(root=root), self.assertRaises(RuntimeError):
                create_app({**self.config, 'RECEIPT_STORAGE_DIR': root})


class AdditiveWebMigrationTests(unittest.TestCase):
    def test_postgresql_migrations_compile_without_connecting(self):
        app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only',
                          'SQLALCHEMY_DATABASE_URI': 'postgresql+psycopg://unused:unused@localhost/unused'})
        try:
            result = app.test_cli_runner().invoke(args=['db', 'upgrade', '--sql'])
            if result.exception:
                raise result.exception
            self.assertIn('CREATE TABLE bills', result.output)
            self.assertIn('CREATE TABLE access_catalog', result.output)
            self.assertIn('0003_access_catalog', result.output)
            self.assertIn('CREATE TABLE users', result.output)
            self.assertIn('ALTER TABLE events', result.output)
            self.assertNotIn('DROP TABLE', result.output)
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_upgrade_existing_agenda_preserves_every_event_column(self):
        with tempfile.TemporaryDirectory(prefix='agenda-upgrade-test-') as folder:
            app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only',
                              'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (Path(folder) / 'legacy.db').as_posix()})
            try:
                result = app.test_cli_runner().invoke(args=['db', 'upgrade', '0001_create_agenda_tables'])
                if result.exception:
                    raise result.exception
                from sqlalchemy import text
                with app.app_context():
                    db.session.execute(text('''INSERT INTO events
                        (id,title,event_date,event_time,end_time,category,location,notes,reminder_minutes,
                         completed,description,priority)
                        VALUES (77,'Original','2026-11-10','09:00:00','10:00:00','Reunião','Sala','Nota',
                                15,0,'Descrição','Alta')'''))
                    db.session.commit()
                    before = dict(db.session.execute(text('''SELECT id,title,event_date,event_time,end_time,
                        category,location,notes,reminder_minutes,completed,description,priority
                        FROM events WHERE id=77''')).mappings().one())
                result = app.test_cli_runner().invoke(args=['db', 'upgrade'])
                if result.exception:
                    raise result.exception
                with app.app_context():
                    after = dict(db.session.execute(text('''SELECT id,title,event_date,event_time,end_time,
                        category,location,notes,reminder_minutes,completed,description,priority
                        FROM events WHERE id=77''')).mappings().one())
                    self.assertEqual(before, after)
                    self.assertIsNone(db.session.execute(
                        text('SELECT owner_id FROM events WHERE id=77')).scalar_one())
                    from sqlalchemy import inspect
                    tables = inspect(db.engine).get_table_names()
                    self.assertTrue({'access_catalog', 'bills', 'bill_series', 'bill_payments'}.issubset(tables))
                    result = app.test_cli_runner().invoke(args=['db', 'check'])
                    if result.exception:
                        raise result.exception
            finally:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()


if __name__ == '__main__':
    unittest.main()
