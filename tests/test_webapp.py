import os
import re
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from webapp import create_app
from webapp.models import Event, LoginAttempt, Task, db


class WebAgendaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-web-test-')
        self.addCleanup(self.temp.cleanup)
        database_path = Path(self.temp.name) / 'agenda-test.db'
        self.database_uri = f'sqlite:///{database_path.as_posix()}'
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'unit-test-secret',
            'SQLALCHEMY_DATABASE_URI': self.database_uri,
            'ADMIN_USERNAME': 'gestor-teste',
            'ADMIN_PASSWORD_HASH': generate_password_hash('Senha-para-teste-123!'),
            'SESSION_COOKIE_SECURE': False,
            'WTF_CSRF_SSL_STRICT': False,
        })
        self.client = self.app.test_client()
        result = self.app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if result.exception:
            raise result.exception

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def _csrf(self, path='/login', client=None):
        client = client or self.client
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, 'Form must render a CSRF token.')
        return match.group(1).decode()

    def _login(self, username='gestor-teste', password='Senha-para-teste-123!', client=None):
        client = client or self.client
        token = self._csrf(client=client)
        return client.post('/login', data={
            'csrf_token': token, 'username': username, 'password': password,
        }, follow_redirects=False)

    def _event_form(self, **overrides):
        values = {
            'title': 'Reunião de planejamento', 'event_date': '2026-12-10',
            'event_time': '09:00', 'end_time': '10:00', 'category': 'Reunião',
            'location': 'Sala principal', 'description': 'Pauta trimestral',
            'notes': 'Levar material', 'priority': 'Alta', 'reminder_minutes': '15',
        }
        values.update(overrides)
        values['csrf_token'] = self._csrf('/events/new')
        return values

    def test_authentication_protects_agenda_and_rejects_bad_credentials(self):
        self.assertEqual(302, self.client.get('/').status_code)
        self.assertIn('/login', self.client.get('/events').headers['Location'])
        response = self._login(username='intruso')
        self.assertEqual(200, response.status_code)
        self.assertIn('Usuário ou senha inválidos'.encode(), response.data)
        response = self._login()
        self.assertEqual(302, response.status_code)
        self.assertEqual(200, self.client.get('/').status_code)

    def test_login_throttles_repeated_invalid_passwords(self):
        token = self._csrf()
        for _ in range(4):
            response = self.client.post('/login', data={
                'csrf_token': token, 'username': 'gestor-teste', 'password': 'incorreta',
            })
            self.assertEqual(200, response.status_code)
        response = self.client.post('/login', data={
            'csrf_token': token, 'username': 'gestor-teste', 'password': 'incorreta',
        })
        self.assertEqual(429, response.status_code)
        self.assertIn('Muitas tentativas'.encode(), response.data)
        self.assertEqual(429, self._login().status_code)
        with self.app.app_context():
            attempt = db.session.query(LoginAttempt).one()
            self.assertEqual(5, attempt.failures)

    def test_csrf_blocks_mutation_without_valid_token(self):
        self._login()
        response = self.client.post('/events/new', data={
            'title': 'Sem token', 'event_date': '2026-12-10',
            'event_time': '09:00', 'priority': 'Normal',
        })
        self.assertEqual(400, response.status_code)
        with self.app.app_context():
            self.assertEqual(0, db.session.query(Event).count())

    def test_create_edit_complete_duplicate_delete_and_persist(self):
        self._login()
        response = self.client.post('/events/new', data=self._event_form(), follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertIn('Compromisso salvo com sucesso'.encode(), response.data)
        with self.app.app_context():
            event = db.session.query(Event).one()
            event_id = event.id
            self.assertEqual('Reunião de planejamento', event.title)
            self.assertEqual('Alta', event.priority)

        edit_form = self._event_form(title='Reunião atualizada', event_time='11:00', end_time='12:00',
                                     priority='Urgente', location='Sala nova')
        response = self.client.post(f'/events/{event_id}/edit', data=edit_form, follow_redirects=True)
        self.assertEqual(200, response.status_code)
        with self.app.app_context():
            event = db.session.get(Event, event_id)
            self.assertEqual('Reunião atualizada', event.title)
            self.assertEqual('11:00', event.event_time.strftime('%H:%M'))
            self.assertEqual('Urgente', event.priority)
            self.assertEqual('Sala nova', event.location)

        self.client.post(f'/events/{event_id}/toggle', data={'csrf_token': self._csrf('/events')})
        with self.app.app_context():
            self.assertEqual(1, db.session.get(Event, event_id).completed)
        self.client.post(f'/events/{event_id}/duplicate', data={'csrf_token': self._csrf('/events')})
        with self.app.app_context():
            self.assertEqual(2, db.session.query(Event).count())
            copied = db.session.query(Event).filter(Event.id != event_id).one()
            self.assertEqual('Reunião atualizada (cópia)', copied.title)
            self.assertEqual(0, copied.completed)

        self.client.post(f'/events/{event_id}/reschedule', data={
            'csrf_token': self._csrf('/events'), 'event_date': '2026-12-11',
            'event_time': '13:00', 'end_time': '14:00',
        })
        with self.app.app_context():
            moved = db.session.get(Event, event_id)
            self.assertEqual('2026-12-11', moved.event_date.isoformat())
            self.assertEqual('13:00', moved.event_time.strftime('%H:%M'))

        self.client.post(f'/events/{event_id}/delete', data={'csrf_token': self._csrf('/events')})
        with self.app.app_context():
            db.session.expire_all()
            self.assertIsNone(db.session.get(Event, event_id))

        restarted_app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'unit-test-secret',
            'SQLALCHEMY_DATABASE_URI': self.database_uri,
            'ADMIN_USERNAME': 'gestor-teste',
            'ADMIN_PASSWORD_HASH': generate_password_hash('Senha-para-teste-123!'),
            'SESSION_COOKIE_SECURE': False,
            'WTF_CSRF_SSL_STRICT': False,
        })
        migration = restarted_app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if migration.exception:
            raise migration.exception
        restarted_client = restarted_app.test_client()
        self._login(client=restarted_client)
        response = restarted_client.get('/events')
        self.assertIn('Reunião atualizada (cópia)'.encode(), response.data)
        self.assertNotIn('Reunião atualizada</strong>'.encode(), response.data)
        with restarted_app.app_context():
            db.session.remove()
            db.engine.dispose()

    def test_tasks_calendar_reminders_search_and_validation(self):
        self._login()
        response = self.client.post('/events/new', data=self._event_form(), follow_redirects=True)
        self.assertEqual(200, response.status_code)
        task_form = {
            'csrf_token': self._csrf('/tasks'), 'title': 'Preparar pauta',
            'description': 'Revisar pontos', 'due_date': '2026-12-10', 'priority': 'Urgente',
        }
        self.client.post('/tasks', data=task_form, follow_redirects=True)
        with self.app.app_context():
            task = db.session.query(Task).one()
            task_id = task.id
        self.assertEqual(200, self.client.get('/calendar?month=2026-12-01').status_code)
        self.assertEqual(200, self.client.get('/reminders').status_code)
        self.assertIn('Preparar pauta'.encode(), self.client.get('/search?q=pauta').data)
        self.client.post(f'/tasks/{task_id}/toggle', data={'csrf_token': self._csrf('/tasks')})
        with self.app.app_context():
            self.assertEqual(1, db.session.get(Task, task_id).completed)
        self.client.post('/tasks', data={
            'csrf_token': self._csrf('/tasks'), 'task_id': str(task_id),
            'title': 'Preparar pauta revista', 'description': 'Atualizada',
            'due_date': '2026-12-12', 'priority': 'Alta',
        })
        with self.app.app_context():
            task = db.session.get(Task, task_id)
            self.assertEqual('Preparar pauta revista', task.title)
            self.assertEqual('Alta', task.priority)
            self.assertEqual(1, task.completed)
        self.client.post(f'/tasks/{task_id}/delete', data={'csrf_token': self._csrf('/tasks')})
        with self.app.app_context():
            self.assertIsNone(db.session.get(Task, task_id))
        invalid = self._event_form(priority='super')
        response = self.client.post('/events/new', data=invalid)
        self.assertEqual(400, response.status_code)
        with self.app.app_context():
            self.assertEqual(1, db.session.query(Event).count())

    def test_hosted_app_requires_postgresql_database_url(self):
        old_url = os.environ.pop('DATABASE_URL', None)
        try:
            with self.assertRaises(RuntimeError):
                create_app()
        finally:
            if old_url is not None:
                os.environ['DATABASE_URL'] = old_url
        with self.assertRaisesRegex(RuntimeError, 'SQLite não é aceito'):
            create_app({
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///should-not-run.db',
                'SECRET_KEY': 'not-used', 'ADMIN_USERNAME': 'gestor',
                'ADMIN_PASSWORD_HASH': 'fake-hash',
            })

    def test_same_title_events_are_changed_by_id_only(self):
        self._login()
        first = self._event_form(event_time='09:00', end_time='10:00')
        second = self._event_form(event_time='11:00', end_time='12:00')
        self.client.post('/events/new', data=first)
        self.client.post('/events/new', data=second)
        with self.app.app_context():
            events = db.session.query(Event).order_by(Event.id).all()
            first_id, second_id = events[0].id, events[1].id
        self.client.post(f'/events/{second_id}/edit', data=self._event_form(
            event_time='11:00', end_time='12:00', title='Somente segundo atualizado'))
        self.client.post(f'/events/{first_id}/delete', data={'csrf_token': self._csrf('/events')})
        with self.app.app_context():
            self.assertIsNone(db.session.get(Event, first_id))
            remaining = db.session.get(Event, second_id)
            self.assertEqual('Somente segundo atualizado', remaining.title)


if __name__ == '__main__':
    unittest.main()
