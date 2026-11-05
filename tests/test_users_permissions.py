import re
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from sqlalchemy import text
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

    def test_operator_recovery_resets_existing_admin_without_exposing_password(self):
        self.admin()
        with self.app.app_context():
            admin = db.session.query(User).filter_by(role='admin').one()
            admin_id, original_version = admin.id, admin.session_version
        password = 'Senha-recuperada-2026!'
        result = self.app.test_cli_runner().invoke(
            args=['users-reset-admin-password', '--email', ADMIN_USERNAME],
            input=f'{password}\n{password}\n')
        self.assertEqual(0, result.exit_code, result.output)
        self.assertNotIn(password, result.output)
        with self.app.app_context():
            admin = db.session.get(User, admin_id)
            self.assertEqual(original_version + 1, admin.session_version)
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

    def test_backend_role_permissions_cover_agenda_tasks_finance_vault_and_admin(self):
        self.admin()
        with self.app.app_context():
            admin = db.session.query(User).filter_by(role='admin').one()
            self.assertFalse(admin.vault_access)
        self.assertEqual(403, self.client.get('/vault').status_code)
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
            'csrf_token': self.csrf('/tasks', manager), 'title': 'Tarefa editada pelo gestor',
            'description': '', 'priority': 'Alta', 'due_date': '',
        }
        self.assertEqual(302, manager.post('/tasks', data=task_form).status_code)
        task_form = {
            'csrf_token': self.csrf(f'/tasks?edit={task_id}', manager), 'task_id': str(task_id),
            'title': 'Atualização de tarefa existente', 'description': '',
            'priority': 'Alta', 'due_date': '',
        }
        self.assertEqual(302, manager.post('/tasks', data=task_form).status_code)
        self.assertEqual(403, manager.post(
            f'/tasks/{task_id}/delete', data={'csrf_token': self.csrf('/tasks', manager)}).status_code)
        with self.app.app_context():
            owned_event = db.session.query(Event).filter_by(title='Gestor cria compromisso').one()
            self.assertEqual('gestor@example.test', db.session.get(User, owned_event.owner_id).email)
            self.assertEqual('Atualização de tarefa existente', db.session.get(Task, task_id).title)

        reader = self.app.test_client()
        self.assertEqual(302, self.login(
            'leitor@example.test', 'Senha-leitor-teste-2026!', reader).status_code)
        self.assertEqual(200, reader.get('/events').status_code)
        self.assertEqual(200, reader.get('/tasks').status_code)
        self.assertEqual(403, reader.get('/users').status_code)
        self.assertEqual(403, reader.get('/bills').status_code)
        self.assertEqual(403, reader.get('/vault').status_code)
        self.assertEqual(403, reader.post(
            '/events/new', data=self.event_form(
                reader, 'Não permitido', csrf_path='/events')).status_code)
        self.assertEqual(403, reader.post(
            '/events', data=self.event_form(reader, 'Também não permitido', csrf_path='/events')).status_code)
        self.assertEqual(403, reader.post('/tasks', data={
            'csrf_token': self.csrf('/tasks', reader), 'title': 'Não permitido',
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
            event = db.session.query(Event).one()
            self.assertIsNone(event.owner_id)
            self.assertEqual('Evento preservado', event.title)
            self.assertIsNone(db.session.query(Task).one().owner_id)
            db.session.remove()


if __name__ == '__main__':
    unittest.main()
