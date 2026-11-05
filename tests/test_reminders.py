import re
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from werkzeug.security import generate_password_hash

from webapp import create_app
from webapp.models import Event, ManualReminder, ReminderState, Task, db
from webapp.reminders import bill_alert, event_alert, from_utc_naive, task_alert


SAO_PAULO = ZoneInfo('America/Sao_Paulo')


class ReminderRulesTests(unittest.TestCase):
    def test_event_alert_window_uses_sao_paulo_and_respects_disabled_reminders(self):
        now = datetime(2026, 11, 5, 8, 45, tzinfo=SAO_PAULO)
        event = {
            'event_date': '2026-11-05', 'event_time': '09:00',
            'reminder_minutes': 15, 'completed': 0,
        }
        self.assertEqual(datetime(2026, 11, 5, 8, 45, tzinfo=SAO_PAULO),
                         event_alert(event, now))
        self.assertIsNone(event_alert(event, now.replace(minute=44, second=59)))
        self.assertIsNone(event_alert({**event, 'reminder_minutes': -1}, now))
        self.assertIsNone(event_alert({**event, 'completed': 1}, now))

    def test_task_and_bill_alerts_ignore_completed_or_paid_items(self):
        today = date(2026, 11, 5)
        self.assertEqual(date(2026, 11, 4), task_alert(
            {'due_date': '2026-11-04', 'completed': 0}, today))
        self.assertIsNone(task_alert({'due_date': '2026-11-04', 'completed': 1}, today))
        self.assertIsNone(task_alert({'due_date': '2026-11-13', 'completed': 0}, today))
        self.assertEqual(date(2026, 11, 12), bill_alert(
            {'due_date': '2026-11-12', 'status': 'Pendente'}, today))
        self.assertEqual(date(2026, 11, 1), bill_alert(
            {'due_date': '2026-11-01', 'status': 'Vencida'}, today))
        self.assertIsNone(bill_alert({'due_date': '2026-11-01', 'status': 'Paga'}, today))


class ReminderCenterWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-reminders-test-')
        self.addCleanup(self.temp.cleanup)
        self.database_uri = 'sqlite:///' + (Path(self.temp.name) / 'reminders.db').as_posix()
        self.config = {
            'TESTING': True, 'SECRET_KEY': 'reminders-tests-only',
            'SQLALCHEMY_DATABASE_URI': self.database_uri,
            'ADMIN_USERNAME': 'admin@example.test',
            'ADMIN_PASSWORD_HASH': generate_password_hash('Senha-para-teste-2026!'),
            'SESSION_COOKIE_SECURE': False, 'WTF_CSRF_SSL_STRICT': False,
        }
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        result = self.app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if result.exception:
            raise result.exception
        token = self.csrf('/login')
        self.client.post('/login', data={
            'csrf_token': token, 'username': 'admin@example.test',
            'password': 'Senha-para-teste-2026!',
        })

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    @staticmethod
    def dispose_app(app):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()

    def csrf(self, path):
        response = self.client.get(path)
        self.assertEqual(200, response.status_code, path)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, path)
        return match.group(1).decode()

    def test_central_alerts_window_manual_crud_and_session_persistence(self):
        fixed_now = datetime(2026, 11, 5, 8, 45, tzinfo=SAO_PAULO)
        for title, hour in (('Aviso no horário', '09:00'), ('Aviso ainda distante', '10:00')):
            response = self.client.post('/events/new', data={
                'csrf_token': self.csrf('/events/new'), 'title': title,
                'event_date': '2026-11-05', 'event_time': hour,
                'end_time': '', 'category': 'Compromisso', 'priority': 'Alta',
                'location': '', 'description': '', 'notes': '',
                'reminder_minutes': '15',
            })
            self.assertEqual(302, response.status_code)
        for title, due, completed in (
                ('Tarefa vencida', '2026-11-04', '0'),
                ('Tarefa hoje', '2026-11-05', '0'),
                ('Tarefa próxima', '2026-11-07', '0'),
                ('Tarefa concluída', '2026-11-04', '1')):
            response = self.client.post('/tasks', data={
                'csrf_token': self.csrf('/tasks'), 'title': title, 'description': '',
                'due_date': due, 'priority': 'Urgente', 'completed': completed,
            })
            self.assertEqual(302, response.status_code)
        response = self.client.post('/reminders/manual', data={
            'csrf_token': self.csrf('/reminders'), 'title': 'Lembrete independente',
            'notes': 'Persistente', 'due_at': '2026-11-06T11:30', 'priority': 'Normal',
        })
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            manual = db.session.query(ManualReminder).one()
            manual_id = manual.id
            self.assertEqual(datetime(2026, 11, 6, 11, 30, tzinfo=SAO_PAULO),
                             from_utc_naive(manual.due_at))
        with patch('webapp.routes.local_now', return_value=fixed_now):
            response = self.client.get('/reminders')
        self.assertEqual(200, response.status_code)
        self.assertIn('Aviso no horário'.encode(), response.data)
        self.assertNotIn('Aviso ainda distante'.encode(), response.data)
        self.assertIn('Tarefa vencida'.encode(), response.data)
        self.assertNotIn('Tarefa concluída'.encode(), response.data)
        self.assertIn('Lembrete independente'.encode(), response.data)
        self.assertEqual(1, response.data.count('Aviso no horário'.encode()))
        with patch('webapp.routes.local_now', return_value=fixed_now):
            today = self.client.get('/reminders?filter=today')
            upcoming = self.client.get('/reminders?filter=upcoming')
            overdue = self.client.get('/reminders?filter=overdue')
            completed = self.client.get('/reminders?filter=completed')
        self.assertIn('Tarefa hoje'.encode(), today.data)
        self.assertIn('Tarefa próxima'.encode(), upcoming.data)
        self.assertIn('Tarefa vencida'.encode(), overdue.data)
        self.assertNotIn('Tarefa vencida'.encode(), today.data)
        self.assertIn('Tarefa concluída'.encode(), completed.data)

        with self.app.app_context():
            event = db.session.query(Event).filter_by(title='Aviso no horário').one()
            event_key = f'{event.id}:2026-11-05T09:00'
        read_event = self.client.post(
            '/reminders/event/' + event_key + '/read',
            data={'csrf_token': self.csrf('/reminders')})
        self.assertEqual(302, read_event.status_code)
        with self.app.app_context():
            self.assertTrue(db.session.query(ReminderState).filter_by(
                item_type='event', item_id=event_key).one().is_read)

        read = self.client.post(
            '/reminders/manual/' + manual_id + '/read',
            data={'csrf_token': self.csrf('/reminders')})
        self.assertEqual(302, read.status_code)
        with self.app.app_context():
            self.assertTrue(db.session.get(ManualReminder, manual_id).is_read)
        with patch('webapp.routes.local_now', return_value=fixed_now):
            snooze = self.client.post(
                '/reminders/manual/' + manual_id + '/snooze',
                data={'csrf_token': self.csrf('/reminders')})
        self.assertEqual(302, snooze.status_code)
        self.assertNotIn('Lembrete independente'.encode(), self.client.get('/reminders').data)
        self.client.post('/reminders/manual/' + manual_id + '/complete',
                         data={'csrf_token': self.csrf('/reminders')})
        with patch('webapp.routes.local_now', return_value=fixed_now):
            completed = self.client.get('/reminders?filter=completed')
        self.assertIn('Lembrete independente'.encode(), completed.data)
        deleted = self.client.post(
            '/reminders/manual/' + manual_id + '/delete',
            data={'csrf_token': self.csrf('/reminders')})
        self.assertEqual(302, deleted.status_code)
        with self.app.app_context():
            self.assertIsNone(db.session.get(ManualReminder, manual_id))

        restarted = create_app(self.config)
        self.addCleanup(self.dispose_app, restarted)
        result = restarted.test_cli_runner().invoke(args=['db', 'upgrade'])
        self.assertIsNone(result.exception)
        with restarted.app_context():
            self.assertEqual(4, db.session.query(Task).count())
            db.session.remove()
            db.engine.dispose()

    def test_calendar_combines_items_and_opens_full_day(self):
        titles = ['Calendário 1', 'Calendário 2', 'Calendário 3', 'Calendário 4']
        for hour, title in enumerate(titles, 9):
            response = self.client.post('/events/new', data={
                'csrf_token': self.csrf('/events/new'), 'title': title,
                'event_date': '2026-11-05', 'event_time': f'{hour:02d}:00',
                'end_time': '', 'category': 'Reunião', 'priority': 'Normal',
                'location': '', 'description': '', 'notes': '',
                'reminder_minutes': '-1',
            })
            self.assertEqual(302, response.status_code)
        response = self.client.post('/tasks', data={
            'csrf_token': self.csrf('/tasks'), 'title': 'Tarefa no calendário',
            'description': '', 'due_date': '2026-11-05', 'priority': 'Alta',
        })
        self.assertEqual(302, response.status_code)
        calendar = self.client.get('/calendar?month=2026-11-01')
        self.assertIn(b'+ 2 mais', calendar.data)
        daily = self.client.get('/calendar/date/2026-11-05')
        for title in titles + ['Tarefa no calendário']:
            self.assertIn(title.encode(), daily.data)
        stylesheet = (Path(__file__).parents[1] / 'webapp' / 'static' / 'style.css').read_text()
        self.assertIn('grid-template-columns:repeat(7,minmax(0,1fr))', stylesheet)
        self.assertIn('.calendar-day{min-height:84px', stylesheet)
        self.assertIn('.calendar-scroll{overflow-x:auto}', stylesheet)


if __name__ == '__main__':
    unittest.main()
