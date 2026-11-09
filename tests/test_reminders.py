import re
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from flask import render_template
from werkzeug.security import generate_password_hash

from webapp import create_app
from webapp.models import Event, ManualReminder, ReminderState, Task, User, db
from webapp.reminders import bill_alert, event_alert, from_utc_naive, task_alert, to_utc_naive


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
        self.assertIsNone(event_alert(event, now.replace(hour=9, minute=0, second=1)))

    def test_zero_minute_reminder_is_visible_during_scheduled_minute(self):
        event = {'event_date': '2026-11-05', 'event_time': '09:00',
                 'reminder_minutes': 0, 'completed': 0}
        now = datetime(2026, 11, 5, 9, 0, 30, 123456, tzinfo=SAO_PAULO)
        self.assertIsNotNone(event_alert(event, now))
        self.assertIsNotNone(event_alert(event, now.astimezone(timezone.utc)))
        self.assertIsNone(event_alert(event, now.replace(hour=8, minute=59)))
        self.assertIsNone(event_alert(event, now.replace(minute=1, second=0, microsecond=0)))

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
            with self.app.app_context():
                db.session.add(Task(title=title, description='', due_date=date.fromisoformat(due),
                                    priority='Urgente', completed=int(completed)))
                db.session.commit()
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
        with patch('webapp.routes.local_now', return_value=fixed_now):
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
            self.assertIsNone(db.session.get(ManualReminder, manual_id))
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
        with self.app.app_context():
            db.session.add(Task(title='Tarefa no calendário', description='',
                                due_date=date(2026, 11, 5), priority='Alta', completed=0))
            db.session.commit()
        calendar = self.client.get('/calendar?month=2026-11-01')
        self.assertIn(b'+ 2 mais', calendar.data)
        daily = self.client.get('/calendar/date/2026-11-05')
        for title in titles + ['Tarefa no calendário']:
            self.assertIn(title.encode(), daily.data)
        stylesheet = (Path(__file__).parents[1] / 'webapp' / 'static' / 'style.css').read_text()
        self.assertIn('grid-template-columns:repeat(7,minmax(0,1fr))', stylesheet)
        self.assertIn('.calendar-day{min-height:84px', stylesheet)
        self.assertIn('.calendar-scroll{overflow-x:auto}', stylesheet)

    def test_manual_reminder_modal_opens_and_supports_edit_with_list_refresh(self):
        fixed_now = datetime(2026, 11, 5, 8, 45, tzinfo=SAO_PAULO)
        default_page = self.client.get('/reminders')
        self.assertIn(b'id="reminderDialog"', default_page.data)
        self.assertIn(b'data-open="false"', default_page.data)
        self.assertNotIn(b'Criar alerta independente', default_page.data)
        open_page = self.client.get('/reminders?new=1')
        self.assertIn(b'data-open="true"', open_page.data)
        with self.app.app_context():
            self.assertEqual(0, db.session.query(ManualReminder).count())

        created = self.client.post('/reminders/manual', data={
            'csrf_token': self.csrf('/reminders'),
            'title': 'Lembrete editável',
            'notes': 'Observações iniciais',
            'due_at': '2026-11-06T11:30',
            'priority': 'Alta',
        })
        self.assertEqual(302, created.status_code)
        with self.app.app_context():
            reminder = db.session.query(ManualReminder).filter_by(
                title='Lembrete editável').one()
            reminder_id = reminder.id

        with patch('webapp.routes.local_now', return_value=fixed_now):
            edit_page = self.client.get(f'/reminders?edit={reminder_id}')
        self.assertIn(b'data-open="true"', edit_page.data)
        self.assertIn(b'value="Lembrete edit\xc3\xa1vel"', edit_page.data)
        changed = self.client.post('/reminders/manual', data={
            'csrf_token': self.csrf('/reminders'),
            'reminder_id': reminder_id,
            'title': 'Lembrete atualizado',
            'notes': 'Observações alteradas',
            'due_at': '2026-11-07T12:15',
            'priority': 'Urgente',
        })
        self.assertEqual(302, changed.status_code)
        with self.app.app_context():
            reminder = db.session.get(ManualReminder, reminder_id)
            self.assertEqual('Lembrete atualizado', reminder.title)
            self.assertEqual('Observações alteradas', reminder.notes)
            self.assertEqual('Urgente', reminder.priority)
            self.assertEqual(datetime(2026, 11, 7, 12, 15, tzinfo=SAO_PAULO),
                             from_utc_naive(reminder.due_at))
        with patch('webapp.routes.local_now', return_value=fixed_now):
            refreshed = self.client.get('/reminders')
        self.assertIn(b'Lembrete atualizado', refreshed.data)

    def test_counts_are_global_and_manual_overdue_uses_time_in_sao_paulo(self):
        now = datetime(2026, 11, 5, 0, 30, tzinfo=SAO_PAULO)
        with self.app.app_context():
            owner = db.session.query(User).one()
            for title, due_at, completed, snoozed in (
                    ('Vencido há meia hora', now.replace(minute=0), False, None),
                    ('Ainda hoje', now.replace(hour=12), False, None),
                    ('Limite sete dias', now + timedelta(days=7), False, None),
                    ('Distante oito dias', now + timedelta(days=8), False, None),
                    ('Adiado temporariamente', now, False, now + timedelta(hours=1)),
                    ('Já concluído', now - timedelta(days=1), True, None)):
                db.session.add(ManualReminder(
                    owner_id=owner.id, title=title, notes='', priority='Normal',
                    due_at=to_utc_naive(due_at), completed=completed,
                    snoozed_until=to_utc_naive(snoozed) if snoozed else None))
            db.session.commit()
        for filter_name in ('all', 'today', 'overdue', 'upcoming', 'completed'):
            with self.subTest(filter=filter_name), patch(
                    'webapp.routes.local_now', return_value=now), patch(
                    'webapp.routes.render_template', wraps=render_template) as renderer:
                response = self.client.get('/reminders?filter=' + filter_name)
                self.assertEqual(200, response.status_code)
                context = renderer.call_args.kwargs
                self.assertEqual(3, context['active_count'])
                self.assertNotIn('Distante oito dias'.encode(), response.data)
                self.assertNotIn(b'Adiado temporariamente', response.data)
                if filter_name == 'overdue':
                    self.assertEqual(['Vencido há meia hora'],
                                     [alert['title'] for alert in context['alerts']])
                    self.assertEqual('overdue', context['alerts'][0]['bucket'])
                if filter_name == 'completed':
                    self.assertEqual(['Já concluído'],
                                     [alert['title'] for alert in context['alerts']])
        # An expired snooze reappears without a restart or a write.
        with patch('webapp.routes.local_now', return_value=now + timedelta(hours=1)):
            self.assertIn(b'Adiado temporariamente', self.client.get('/reminders').data)

    def test_completed_task_keeps_original_deadline(self):
        with self.app.app_context():
            db.session.add(Task(title='Prazo original', due_date=date(2026, 10, 15),
                                priority='Normal', completed=1))
            db.session.commit()
        with patch('webapp.routes.render_template', wraps=render_template) as renderer:
            self.client.get('/reminders?filter=completed')
        alert = renderer.call_args.kwargs['alerts'][0]
        self.assertEqual(date(2026, 10, 15), alert['due_date'])
        self.assertEqual(date(2026, 10, 15), alert['due_at'].date())

    def test_manual_validation_reopens_modal_keeps_values_and_does_not_write(self):
        token = self.csrf('/reminders')
        self.assertEqual(302, self.app.test_client().get('/reminders').status_code)
        defaults = {'csrf_token': token, 'title': 'Manter dados',
                    'notes': 'Observações preservadas', 'priority': 'Alta',
                    'due_at': '2026-11-06T11:30'}
        cases = [({'title': '   '}, 'title'), ({'title': 'x' * 201}, 'title'),
                 ({'notes': 'x' * 5001}, 'notes'), ({'priority': 'Inválida'}, 'priority')]
        cases.extend(({'due_at': value}, 'due_at') for value in (
            '', '2026-11-06', '2026-02-30T11:30', '2026-11-06T25:00',
            '1899-12-31T12:00', '2200-01-01T12:00'))
        for changes, error_key in cases:
            with self.subTest(changes=changes), patch(
                    'webapp.routes.render_template', wraps=render_template) as renderer:
                response = self.client.post('/reminders/manual', data=defaults | changes)
                self.assertEqual(400, response.status_code)
                context = renderer.call_args.kwargs
                self.assertTrue(context['show_reminder_modal'])
                self.assertIn(error_key, context['manual_errors'])
                self.assertEqual((defaults | changes)['notes'], context['manual_form']['notes'])
        with self.app.app_context():
            self.assertEqual(0, db.session.query(ManualReminder).count())

    def test_saving_distant_manual_explains_when_it_will_be_visible(self):
        with patch('webapp.routes.local_now', return_value=datetime(
                2026, 11, 5, 12, 0, tzinfo=SAO_PAULO)):
            response = self.client.post('/reminders/manual', data={
                'csrf_token': self.csrf('/reminders'), 'title': 'Planejamento distante',
                'notes': '', 'priority': 'Normal', 'due_at': '2026-11-13T11:30'},
                follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertIn('Lembrete criado com sucesso.'.encode(), response.data)
        self.assertIn('a partir de 06/11/2026.'.encode(), response.data)
        self.assertNotIn(b'Planejamento distante', response.data)
        with self.app.app_context():
            self.assertEqual(1, db.session.query(ManualReminder).filter_by(
                title='Planejamento distante').count())

    def test_manual_save_failure_rolls_back_and_keeps_modal_open(self):
        token = self.csrf('/reminders')
        with patch('webapp.routes.db.session.commit', side_effect=[RuntimeError('test failure'), None]), patch(
                'webapp.routes.render_template', wraps=render_template) as renderer:
            response = self.client.post('/reminders/manual', data={
                'csrf_token': token, 'title': 'Não gravar', 'notes': 'Preservar',
                'priority': 'Normal', 'due_at': '2026-11-06T11:30'})
        self.assertEqual(500, response.status_code)
        self.assertTrue(renderer.call_args.kwargs['show_reminder_modal'])
        self.assertIn('form', renderer.call_args.kwargs['manual_errors'])
        with self.app.app_context():
            self.assertEqual(0, db.session.query(ManualReminder).count())

    def test_manual_mutations_require_csrf_ownership_and_current_agenda_permission(self):
        with self.app.app_context():
            foreign = User(name='Outro gestor', email='other@example.test', role='manager',
                           password_hash=generate_password_hash('Other-test-password'),
                           agenda_access=True, finance_access=True)
            db.session.add(foreign)
            db.session.flush()
            reminder = ManualReminder(owner_id=foreign.id, title='Privado de outro gestor',
                                      notes='', priority='Normal', due_at=datetime(2026, 11, 6, 14))
            db.session.add(reminder)
            db.session.commit()
            reminder_id, owner_id = reminder.id, foreign.id
        token = self.csrf('/reminders')
        for action in ('read', 'snooze', 'complete', 'delete'):
            path = f'/reminders/manual/{reminder_id}/{action}'
            with self.subTest(action=action):
                self.assertEqual(400, self.client.post(path).status_code)
                self.assertEqual(404, self.client.post(path, data={'csrf_token': token}).status_code)
                self.assertEqual(400, self.app.test_client().post(path,
                                 data={'csrf_token': token}).status_code)
        self.assertEqual(404, self.client.get('/reminders?edit=' + reminder_id).status_code)
        foreign_client = self.app.test_client()
        login_token = re.search(rb'name="csrf_token" value="([^"]+)"',
                                foreign_client.get('/login').data).group(1).decode()
        self.assertEqual(302, foreign_client.post('/login', data={
            'csrf_token': login_token, 'username': 'other@example.test',
            'password': 'Other-test-password'}).status_code)
        foreign_token = re.search(rb'name="csrf_token" value="([^"]+)"',
                                  foreign_client.get('/reminders').data).group(1).decode()
        with self.app.app_context():
            db.session.get(User, owner_id).agenda_access = False
            db.session.commit()
        self.assertNotIn(b'Privado de outro gestor', foreign_client.get('/reminders').data)
        self.assertEqual(403, foreign_client.get('/reminders?edit=' + reminder_id).status_code)
        for action in ('read', 'snooze', 'complete', 'delete'):
            self.assertEqual(403, foreign_client.post(
                f'/reminders/manual/{reminder_id}/{action}',
                data={'csrf_token': foreign_token}).status_code)
        self.assertEqual(403, foreign_client.post('/reminders/manual', data={
            'csrf_token': foreign_token, 'reminder_id': reminder_id,
            'title': 'Tentativa', 'notes': '', 'priority': 'Normal',
            'due_at': '2026-11-06T11:30'}).status_code)
        with self.app.app_context():
            reminder = db.session.get(ManualReminder, reminder_id)
            self.assertEqual('Privado de outro gestor', reminder.title)
            self.assertFalse(reminder.completed)
            self.assertFalse(reminder.is_read)
            self.assertIsNone(reminder.snoozed_until)


if __name__ == '__main__':
    unittest.main()
