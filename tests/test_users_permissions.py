import os
import re
import tempfile
import unittest
from datetime import date, time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from werkzeug.security import check_password_hash, generate_password_hash

from webapp import create_app
from webapp.models import AuditLog, Event, SecurityState, Task, User, db


ADMIN_USERNAME = 'admin@example.test'
ADMIN_PASSWORD = 'Senha-admin-teste-2026!'


class UserPermissionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-users-test-')
        self.addCleanup(self.temp.cleanup)
        self.database_uri = 'sqlite:///' + (Path(self.temp.name) / 'users.db').as_posix()
        self.config = {
            'TESTING': True,
            'SECRET_KEY': 'users-tests-only',
            'SQLALCHEMY_DATABASE_URI': self.database_uri,
            'ADMIN_USERNAME': ADMIN_USERNAME,
            'ADMIN_PASSWORD_HASH': generate_password_hash(ADMIN_PASSWORD),
            'SESSION_COOKIE_SECURE': False,
            'WTF_CSRF_SSL_STRICT': False,
        }
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.upgrade()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def upgrade(self, revision=None):
        args = ['db', 'upgrade']
        if revision:
            args.append(revision)
        result = self.app.test_cli_runner().invoke(args=args)
        if result.exception:
            raise result.exception

    def csrf(self, path, client=None):
        client = client or self.client
        response = client.get(path)
        self.assertEqual(200, response.status_code, path)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, path)
        return match.group(1).decode()

    def login(self, username=ADMIN_USERNAME, password=ADMIN_PASSWORD, client=None):
        client = client or self.client
        return client.post('/login', data={
            'csrf_token': self.csrf('/login', client),
            'username': username,
            'password': password,
        })

    def admin(self):
        self.assertEqual(302, self.login().status_code)

    def create_user(self, *, name='Gestor de teste', email='gestor@example.test',
                    role='manager', password='Senha-gestor-teste-2026!',
                    agenda=True, finance=False, vault=False):
        form = {
            'csrf_token': self.csrf('/users/new'),
            'name': name,
            'email': email,
            'role': role,
            'password': password,
            'agenda_access': '1' if agenda else '',
            'finance_access': '1' if finance else '',
            'vault_access': '1' if vault else '',
            'reauth_password': ADMIN_PASSWORD,
            'confirm': '1',
        }
        return self.client.post('/users/new', data=form)

    def event_form(self, client, title='Evento compartilhado', csrf_path='/events/new'):
        csrf = self.csrf(csrf_path, client)
        return {
            'csrf_token': csrf,
            'title': title,
            'event_date': '2026-12-10',
            'event_time': '09:00',
            'end_time': '10:00',
            'category': 'Reunião',
            'priority': 'Normal',
            'location': '',
            'description': '',
            'notes': '',
            'reminder_minutes': '15',
        }

    def test_legacy_admin_is_imported_once_and_password_reset_persists(self):
        self.admin()
        with self.app.app_context():
            admin = db.session.query(User).one()
            admin_id = admin.id
            old_hash = admin.password_hash
            self.assertTrue(check_password_hash(old_hash, ADMIN_PASSWORD))
            self.assertEqual('admin', admin.role)
            self.assertTrue(admin.agenda_access)
            self.assertTrue(admin.finance_access)
            self.assertFalse(admin.vault_access)
            self.assertTrue(db.session.get(SecurityState, 1).bootstrap_complete)
            self.assertIsNotNone(admin.last_login)

        reset_url = f'/users/{admin_id}/password'
        response = self.client.post(reset_url, data={
            'csrf_token': self.csrf(reset_url),
            'password': 'Senha-admin-atualizada-2026!',
            'reauth_password': ADMIN_PASSWORD,
            'confirm': '1',
        })
        self.assertEqual(302, response.status_code)
        restarted_config = {**self.config, 'ADMIN_USERNAME': '', 'ADMIN_PASSWORD_HASH': ''}
        restarted = create_app(restarted_config)
        migration = restarted.test_cli_runner().invoke(args=['db', 'upgrade'])
        self.assertIsNone(migration.exception)
        old_client = restarted.test_client()
        self.assertNotEqual(302, self.login(client=old_client).status_code)
        self.assertEqual(302, self.login(
            username=ADMIN_USERNAME, password='Senha-admin-atualizada-2026!',
            client=old_client).status_code)
        with restarted.app_context():
            admin = db.session.get(User, admin_id)
            self.assertNotEqual(old_hash, admin.password_hash)
            self.assertTrue(check_password_hash(admin.password_hash, 'Senha-admin-atualizada-2026!'))
            self.assertEqual(1, db.session.query(User).count())
            db.session.remove()
            db.engine.dispose()

    def test_operator_recovery_requires_backup_and_account_confirmation(self):
        self.admin()
        with self.app.app_context():
            admin = db.session.query(User).filter_by(role='admin').one()
            admin_id, original_hash, original_version = admin.id, admin.password_hash, admin.session_version
        missing_backup = self.app.test_cli_runner().invoke(args=[
            'users-reset-admin-password', '--email', ADMIN_USERNAME])
        self.assertNotEqual(0, missing_backup.exit_code)
        with tempfile.TemporaryDirectory() as folder, patch(
                'webapp.recovery.verify_backup', return_value='a' * 64):
            backup = Path(folder) / 'verified.dump'
            backup.write_bytes(b'fake backup used with mocked verification')
            password = 'Senha-recuperada-2026!'
            result = self.app.test_cli_runner().invoke(
                args=['users-reset-admin-password', '--email', ADMIN_USERNAME,
                      '--backup-file', str(backup)],
                input=f'{admin_id}\n{password}\n{password}\n')
        self.assertEqual(0, result.exit_code, result.output)
        self.assertNotIn(password, result.output)
        with self.app.app_context():
            admin = db.session.get(User, admin_id)
            self.assertEqual(original_version + 1, admin.session_version)
            self.assertNotEqual(original_hash, admin.password_hash)
            self.assertTrue(check_password_hash(admin.password_hash, password))
            audit = db.session.query(AuditLog).one()
            self.assertEqual('admin_password_recovered', audit.action)
            self.assertIsNone(audit.actor_id)

    def test_create_duplicate_login_password_independence_reset_and_deactivation(self):
        self.admin()
        response = self.create_user()
        self.assertEqual(302, response.status_code)
        manager_client = self.app.test_client()
        self.assertEqual(302, self.login(
            username='gestor@example.test', password='Senha-gestor-teste-2026!',
            client=manager_client).status_code)
        with self.app.app_context():
            manager = db.session.query(User).filter_by(email='gestor@example.test').one()
            manager_id = manager.id
            original_hash = manager.password_hash
            self.assertNotEqual(db.session.query(User).filter_by(role='admin').one().password_hash,
                                manager.password_hash)

        duplicate = self.create_user(email='GESTOR@example.test')
        self.assertEqual(400, duplicate.status_code)
        self.assertIn('Já existe um usuário'.encode(), duplicate.data)

        reset_url = f'/users/{manager_id}/password'
        response = self.client.post(reset_url, data={
            'csrf_token': self.csrf(reset_url),
            'password': 'Senha-nova-gestor-2026!',
            'reauth_password': ADMIN_PASSWORD,
            'confirm': '1',
        })
        self.assertEqual(302, response.status_code)
        self.assertEqual(302, manager_client.get('/').status_code)
        self.assertEqual(302, self.login(
            username='gestor@example.test', password='Senha-nova-gestor-2026!',
            client=manager_client).status_code)

        status_url = f'/users/{manager_id}/status'
        response = self.client.post(status_url, data={
            'csrf_token': self.csrf(status_url), 'active': '0',
            'reauth_password': ADMIN_PASSWORD, 'confirm': '1',
        })
        self.assertEqual(302, response.status_code)
        self.assertEqual(302, manager_client.get('/').status_code)
        self.assertNotEqual(302, self.login(
            username='gestor@example.test', password='Senha-nova-gestor-2026!',
            client=manager_client).status_code)
        with self.app.app_context():
            manager = db.session.get(User, manager_id)
            self.assertFalse(manager.active)
            self.assertNotEqual(original_hash, manager.password_hash)

    def test_create_user_with_csrf_and_origin_validation_over_https_proxy(self):
        self.app.config.update(
            WTF_CSRF_SSL_STRICT=True,
            SESSION_COOKIE_SECURE=True,
        )
        self.assertTrue(self.app.config['WTF_CSRF_ENABLED'])
        self.assertTrue(self.app.config['WTF_CSRF_SSL_STRICT'])
        client = self.app.test_client()
        forwarded_https = {'X-Forwarded-Proto': 'https'}

        proxy_reports_http = client.get(
            '/login', base_url='https://localhost',
            headers={'X-Forwarded-Proto': 'http'})
        self.assertNotIn('Strict-Transport-Security', proxy_reports_http.headers)

        login_page = client.get('/login', base_url='https://localhost', headers=forwarded_https)
        self.assertEqual('strict-origin-when-cross-origin',
                         login_page.headers['Referrer-Policy'])
        login_token = re.search(rb'name="csrf_token" value="([^"]+)"', login_page.data).group(1).decode()
        login = client.post(
            '/login', base_url='https://localhost', headers={
                **forwarded_https, 'Referer': 'https://localhost/login',
            }, data={
                'csrf_token': login_token, 'username': ADMIN_USERNAME, 'password': ADMIN_PASSWORD,
            })
        self.assertEqual(302, login.status_code)
        self.assertIn('Secure', login.headers.get('Set-Cookie', ''))
        self.assertIn('max-age=31536000', login.headers.get('Strict-Transport-Security', ''))

        form_page = client.get(
            '/users/new', base_url='https://localhost', headers=forwarded_https)
        self.assertEqual(200, form_page.status_code)
        self.assertEqual('strict-origin-when-cross-origin',
                         form_page.headers['Referrer-Policy'])
        self.assertIn(b'action="/users/new"', form_page.data)
        registration_form = re.search(rb'<form\b[^>]*action="/users/new"[^>]*>(.*?)</form>',
                                      form_page.data, re.DOTALL)
        self.assertIsNotNone(registration_form)
        self.assertIn(b'referrerpolicy="strict-origin-when-cross-origin"',
                      registration_form.group(0))
        self.assertNotIn(b'no-referrer', registration_form.group(0))
        token_match = re.search(rb'name="csrf_token" value="([^"]+)"', registration_form.group(1))
        self.assertIsNotNone(token_match, 'Cadastro must include a CSRF token field.')
        token = token_match.group(1).decode()
        data = {
            'csrf_token': token, 'name': 'Gestora HTTPS', 'email': 'https@example.test',
            'role': 'manager', 'password': 'Senha-https-teste-2026!',
            'agenda_access': '1', 'reauth_password': ADMIN_PASSWORD, 'confirm': '1',
        }

        missing_referrer = client.post(
            '/users/new', base_url='https://localhost', headers=forwarded_https, data=data)
        self.assertEqual(400, missing_referrer.status_code)
        self.assertIn(b'The referrer header is missing', missing_referrer.data)

        missing_token = client.post(
            '/users/new', base_url='https://localhost', headers={
                **forwarded_https, 'Referer': 'https://localhost/users/new',
            }, data={**data, 'csrf_token': ''})
        self.assertEqual(400, missing_token.status_code)

        invalid_token = client.post(
            '/users/new', base_url='https://localhost', headers={
                **forwarded_https, 'Referer': 'https://localhost/users/new',
            }, data={**data, 'csrf_token': 'token-invalido'})
        self.assertEqual(400, invalid_token.status_code)

        external_origin = client.post(
            '/users/new', base_url='https://localhost', headers={
                **forwarded_https, 'Referer': 'https://site-externo.example/users/new',
            }, data=data)
        self.assertEqual(400, external_origin.status_code)
        self.assertIn(b'The referrer does not match the host', external_origin.data)

        response = client.post(
            '/users/new', base_url='https://localhost', headers={
                **forwarded_https, 'Referer': 'https://localhost/users/new',
            }, data=data)
        self.assertEqual(302, response.status_code)
        self.assertEqual('/users/', response.headers['Location'])

        with self.app.app_context():
            user = db.session.query(User).filter_by(email='https@example.test').one()
            user_id = user.id
            self.assertTrue(check_password_hash(user.password_hash, 'Senha-https-teste-2026!'))

        new_user_client = self.app.test_client()
        page = new_user_client.get(
            '/login', base_url='https://localhost', headers=forwarded_https)
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        login = new_user_client.post(
            '/login', base_url='https://localhost', headers={
                **forwarded_https, 'Referer': 'https://localhost/login',
            }, data={
                'csrf_token': token, 'username': 'https@example.test',
                'password': 'Senha-https-teste-2026!',
            })
        self.assertEqual(302, login.status_code)
        with self.app.app_context():
            self.assertTrue(db.session.get(User, user_id).active)

    @unittest.skipUnless(os.environ.get('TEST_POSTGRES_DATABASE_URL'),
                         'Set TEST_POSTGRES_DATABASE_URL to a disposable PostgreSQL test database.')
    def test_user_registration_persists_and_authenticates_on_postgres(self):
        database_url = make_url(os.environ['TEST_POSTGRES_DATABASE_URL'])
        if database_url.get_backend_name() != 'postgresql':
            self.fail('TEST_POSTGRES_DATABASE_URL must point to PostgreSQL.')
        if 'test' not in (database_url.database or '').casefold():
            self.fail('PostgreSQL integration tests require a database name containing "test".')

        schema = 'csrf_test_' + uuid4().hex
        admin_engine = create_engine(database_url)
        app = None
        try:
            with admin_engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            isolated_url = database_url.update_query_dict({
                'options': f'-csearch_path={schema},public',
            }).render_as_string(hide_password=False)
            app = create_app({**self.config, 'SQLALCHEMY_DATABASE_URI': isolated_url})
            migration = app.test_cli_runner().invoke(args=['db', 'upgrade'])
            if migration.exception:
                raise migration.exception

            admin_client = app.test_client()
            self.assertEqual(302, self.login(client=admin_client).status_code)
            create_form = {
                'csrf_token': self.csrf('/users/new', admin_client),
                'name': 'Gestor PostgreSQL',
                'email': 'gestor-postgresql@example.test',
                'role': 'manager',
                'password': 'Senha-postgresql-teste-2026!',
                'agenda_access': '1',
                'reauth_password': ADMIN_PASSWORD,
                'confirm': '1',
            }
            response = admin_client.post('/users/new', data=create_form)
            self.assertEqual(302, response.status_code)

            with app.app_context():
                user = db.session.query(User).filter_by(
                    email='gestor-postgresql@example.test').one()
                user_id = user.id
                self.assertTrue(check_password_hash(
                    user.password_hash, 'Senha-postgresql-teste-2026!'))

            user_client = app.test_client()
            self.assertEqual(302, self.login(
                username='gestor-postgresql@example.test',
                password='Senha-postgresql-teste-2026!',
                client=user_client,
            ).status_code)
            with app.app_context():
                self.assertTrue(db.session.get(User, user_id).active)
        finally:
            if app is not None:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            admin_engine.dispose()

    def test_backend_role_permissions_cover_agenda_tasks_finance_vault_and_admin(self):
        self.admin()
        with self.app.app_context():
            admin = db.session.query(User).filter_by(role='admin').one()
            self.assertFalse(admin.vault_access)
        self.assertEqual(403, self.client.get('/vault').status_code)
        self.assertEqual(200, self.client.get('/tasks/archive').status_code)
        self.assertEqual(302, self.create_user().status_code)
        self.assertEqual(302, self.create_user(
            name='Leitor de teste', email='leitor@example.test', role='reader',
            password='Senha-leitor-teste-2026!', agenda=True).status_code)
        self.assertEqual(302, self.create_user(
            name='Gestor financeiro', email='finance@example.test', role='manager',
            password='Senha-financeiro-2026!', agenda=False, finance=True).status_code)
        self.assertEqual(302, self.create_user(
            name='Gestor do cofre', email='vault@example.test', role='manager',
            password='Senha-cofre-teste-2026!', agenda=False, vault=True).status_code)

        manager = self.app.test_client()
        self.assertEqual(302, self.login(
            'gestor@example.test', 'Senha-gestor-teste-2026!', manager).status_code)
        with self.app.app_context():
            event = Event(title='Compromisso legado', event_date=date(2026, 12, 10),
                          event_time=time(9), category='Reunião',
                          location='', notes='', reminder_minutes=15, completed=0,
                          description='', priority='Normal')
            task = Task(title='Tarefa organizacional', description='', priority='Normal', completed=0)
            db.session.add_all([event, task])
            db.session.commit()
            event_id, task_id = event.id, task.id

        self.assertEqual(200, manager.get('/events').status_code)
        self.assertEqual(403, manager.get('/users').status_code)
        self.assertEqual(403, manager.get('/bills').status_code)
        self.assertEqual(403, manager.get('/vault').status_code)
        self.assertEqual(403, manager.get(f'/events/{event_id}/edit').status_code)
        self.assertEqual(403, manager.post(
            f'/events/{event_id}/delete',
            data={'csrf_token': self.csrf('/events', manager)}).status_code)

        response = manager.post('/events/new', data=self.event_form(manager, 'Gestor cria compromisso'))
        self.assertEqual(302, response.status_code)
        task_form = {
            'csrf_token': self.csrf('/events', manager), 'title': 'Tarefa editada pelo gestor',
            'description': '', 'priority': 'Alta', 'due_date': '',
        }
        self.assertEqual(410, manager.post('/tasks', data=task_form).status_code)
        task_form = {
            'csrf_token': self.csrf('/events', manager), 'task_id': str(task_id),
            'title': 'Atualização de tarefa existente', 'description': '',
            'priority': 'Alta', 'due_date': '',
        }
        self.assertEqual(410, manager.post('/tasks', data=task_form).status_code)
        self.assertEqual(410, manager.post(
            f'/tasks/{task_id}/toggle', data={'csrf_token': self.csrf('/events', manager)}).status_code)
        self.assertEqual(403, manager.post(
            f'/tasks/{task_id}/delete', data={'csrf_token': self.csrf('/events', manager)}).status_code)
        with self.app.app_context():
            owned_event = db.session.query(Event).filter_by(title='Gestor cria compromisso').one()
            self.assertEqual('gestor@example.test', db.session.get(User, owned_event.owner_id).email)
            self.assertEqual('Tarefa organizacional', db.session.get(Task, task_id).title)
            self.assertEqual(0, db.session.get(Task, task_id).completed)
            self.assertEqual(1, db.session.query(Task).count())

        reader = self.app.test_client()
        self.assertEqual(302, self.login(
            'leitor@example.test', 'Senha-leitor-teste-2026!', reader).status_code)
        self.assertEqual(200, reader.get('/events').status_code)
        redirected = reader.get('/tasks')
        self.assertEqual(302, redirected.status_code)
        self.assertTrue(redirected.headers['Location'].endswith('/vault'))
        self.assertEqual(200, reader.get(f'/tasks/{task_id}').status_code)
        self.assertEqual(403, reader.get('/tasks/archive').status_code)
        self.assertEqual(403, reader.get('/users').status_code)
        self.assertEqual(403, reader.get('/bills').status_code)
        self.assertEqual(403, reader.get('/vault').status_code)
        self.assertEqual(403, reader.post(
            '/events/new', data=self.event_form(
                reader, 'Não permitido', csrf_path='/events')).status_code)
        self.assertEqual(403, reader.post(
            '/events', data=self.event_form(reader, 'Também não permitido', csrf_path='/events')).status_code)
        self.assertEqual(403, reader.post('/tasks', data={
            'csrf_token': self.csrf('/events', reader), 'title': 'Não permitido',
        }).status_code)
        with self.app.app_context():
            self.assertIsNone(db.session.query(Event).filter_by(title='Não permitido').one_or_none())

        finance_manager = self.app.test_client()
        finance_login = self.login('finance@example.test', 'Senha-financeiro-2026!', finance_manager)
        self.assertEqual(302, finance_login.status_code)
        self.assertTrue(finance_login.headers['Location'].endswith('/bills'))
        self.assertEqual(200, finance_manager.get('/bills').status_code)
        self.assertEqual(302, finance_manager.post('/bills/new', data={
            'csrf_token': self.csrf('/bills/new', finance_manager),
            'title': 'Conta autorizada', 'category': 'Operacional', 'amount': '10,00',
            'due_date': '2026-12-10', 'responsible': 'Gestor', 'recurrence': 'Única',
            'notes': '',
        }).status_code)

        vault_manager = self.app.test_client()
        vault_login = self.login('vault@example.test', 'Senha-cofre-teste-2026!', vault_manager)
        self.assertEqual(302, vault_login.status_code)
        self.assertTrue(vault_login.headers['Location'].endswith('/vault'))
        self.assertEqual(200, vault_manager.get('/vault').status_code)

    def test_last_active_administrator_cannot_be_disabled_or_demoted(self):
        self.admin()
        with self.app.app_context():
            admin_id = db.session.query(User).filter_by(role='admin').one().id
        status_url = f'/users/{admin_id}/status'
        response = self.client.post(status_url, data={
            'csrf_token': self.csrf(status_url), 'active': '0',
            'reauth_password': ADMIN_PASSWORD, 'confirm': '1',
        })
        self.assertEqual(400, response.status_code)
        self.assertIn('último administrador ativo'.encode(), response.data)

        edit_url = f'/users/{admin_id}/edit'
        response = self.client.post(edit_url, data={
            'csrf_token': self.csrf(edit_url), 'name': 'Administrador',
            'email': ADMIN_USERNAME, 'role': 'manager', 'agenda_access': '1',
            'reauth_password': ADMIN_PASSWORD, 'confirm': '1',
        })
        self.assertEqual(400, response.status_code)
        with self.app.app_context():
            admin = db.session.get(User, admin_id)
            self.assertTrue(admin.active)
            self.assertEqual('admin', admin.role)

    def test_upgrade_preserves_events_tasks_bills_and_catalog_rows(self):
        self.upgrade('0004_vault_passwords')
        with self.app.app_context():
            db.session.execute(text(
                "INSERT INTO events (title,event_date,event_time) VALUES "
                "('Evento preservado','2026-12-10','09:00:00')"))
            db.session.execute(text(
                "INSERT INTO tasks (title) VALUES ('Tarefa preservada')"))
            db.session.execute(text(
                "INSERT INTO bills (id,title,category,amount_cents,responsible,notes,due_date,"
                "recurrence,series_id,occurrence_month,anchor_day,paid_on,receipt_id,deleted) "
                "VALUES ('bill-1','Conta preservada','Operacional',1234,'Gestor','',"
                "'2026-12-10','Única',NULL,NULL,10,NULL,'',0)"))
            db.session.execute(text(
                "INSERT INTO access_catalog (id,name,url,category,notes,updated_at) "
                "VALUES ('access-1','Acesso preservado','','','','2026-01-01')"))
            db.session.commit()
        self.upgrade()
        with self.app.app_context():
            self.assertEqual(1, db.session.query(Event).count())
            self.assertEqual(1, db.session.query(Task).count())
            self.assertEqual(1, db.session.execute(text('SELECT count(*) FROM bills')).scalar())
            self.assertEqual(1, db.session.execute(text('SELECT count(*) FROM access_catalog')).scalar())
            self.assertEqual('', db.session.execute(text(
                "SELECT username FROM access_catalog WHERE id='access-1'")).scalar())
            event = db.session.query(Event).one()
            self.assertIsNone(event.owner_id)
            self.assertEqual('Evento preservado', event.title)
            self.assertIsNone(db.session.query(Task).one().owner_id)
            db.session.remove()
        self.admin()
        archive = self.client.get('/tasks/archive')
        self.assertEqual(200, archive.status_code)
        self.assertIn('Tarefa preservada'.encode(), archive.data)
        self.assertIn('Consulta somente leitura'.encode(), archive.data)
        self.assertNotIn('Nova tarefa'.encode(), archive.data)


if __name__ == '__main__':
    unittest.main()
