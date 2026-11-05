import re
import tempfile
import unittest
from datetime import date, datetime, time
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from icalendar import Calendar
from openpyxl import load_workbook
from pypdf import PdfReader
from werkzeug.security import generate_password_hash

from webapp import create_app
from webapp.models import Event, User, db


SAO_PAULO = ZoneInfo('America/Sao_Paulo')


class EventExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-export-test-')
        self.addCleanup(self.temp.cleanup)
        self.config = {
            'TESTING': True,
            'SECRET_KEY': 'export-tests-only',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (Path(self.temp.name) / 'exports.db').as_posix(),
            'ADMIN_USERNAME': 'admin@example.test',
            'ADMIN_PASSWORD_HASH': generate_password_hash('Export-Password-Only!'),
            'AGENDA_MANAGER_NAME': 'Gestora de Testes',
            'SESSION_COOKIE_SECURE': False,
            'WTF_CSRF_SSL_STRICT': False,
        }
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        result = self.app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if result.exception:
            raise result.exception
        token = self.csrf('/login')
        response = self.client.post('/login', data={
            'csrf_token': token,
            'username': 'admin@example.test',
            'password': 'Export-Password-Only!',
        })
        self.assertEqual(302, response.status_code)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def csrf(self, path):
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def add_event(self, title, *, day=date(2026, 11, 5), completed=0, hour=9,
                  category='Reunião', priority='Alta'):
        with self.app.app_context():
            owner = db.session.scalar(db.select(User).where(User.email == 'admin@example.test'))
            event = Event(
                title=title, event_date=day, event_time=time(hour, 5),
                end_time=time(hour, 45), category=category, location='Sala Árvore',
                priority=priority, completed=completed, owner_id=owner.id,
                notes='Campo privado não exportado', description='Descrição não exportada',
            )
            db.session.add(event)
            db.session.commit()
            return event.id

    @staticmethod
    def custom_query(file_format='pdf', **values):
        query = {
            'period': 'custom',
            'start_date': '2026-11-05',
            'end_date': '2026-11-05',
            'format': file_format,
        }
        query.update(values)
        from urllib.parse import urlencode
        return '/events/export?' + urlencode(query)

    def test_pdf_download_opens_and_contains_only_approved_data(self):
        self.add_event('Reunião de revisão — São Paulo')
        response = self.client.get(self.custom_query())
        self.assertEqual(200, response.status_code)
        self.assertEqual('private, no-store', response.headers['Cache-Control'])
        self.assertIn('agenda_executiva_2026-11.pdf', response.headers['Content-Disposition'])
        reader = PdfReader(BytesIO(response.data))
        self.assertGreaterEqual(len(reader.pages), 1)
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        self.assertIn('Gestora de Testes', text)
        self.assertIn('Reunião de revisão', text)
        self.assertNotIn('Campo privado', text)
        self.assertNotIn('Descrição não exportada', text)

    def test_xlsx_download_opens_with_dates_filters_and_formula_neutralization(self):
        self.add_event('=HYPERLINK("https://example.test","clique")')
        self.add_event('Compromisso concluído', completed=1)
        self.add_event('Fora do período', day=date(2026, 11, 6))
        response = self.client.get(self.custom_query('xlsx'))
        self.assertEqual(200, response.status_code)
        self.assertIn('agenda_executiva_2026-11.xlsx', response.headers['Content-Disposition'])
        workbook = load_workbook(BytesIO(response.data), data_only=False)
        sheet = workbook['Compromissos']
        self.assertEqual('Data', sheet['A1'].value)
        self.assertEqual('2026-11-05', sheet['A2'].value.strftime('%Y-%m-%d'))
        self.assertTrue(sheet['D2'].value.startswith("'="))
        self.assertEqual('s', sheet['D2'].data_type)
        self.assertEqual(2, sheet.max_row)
        response = self.client.get(self.custom_query('xlsx', include_completed='1'))
        self.assertEqual(200, response.status_code)
        workbook = load_workbook(BytesIO(response.data), data_only=False)
        self.assertEqual(3, workbook['Compromissos'].max_row)

    def test_ics_opens_with_stable_uid_sao_paulo_time_and_escaped_text(self):
        event_id = self.add_event('Reunião, equipe; São Paulo')
        response = self.client.get(self.custom_query('ics'))
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/calendar', response.mimetype)
        calendar = Calendar.from_ical(response.data)
        component = next(item for item in calendar.walk('VEVENT'))
        self.assertEqual(f'event-{event_id}@agenda-executiva', str(component['uid']))
        self.assertEqual(
            datetime(2026, 11, 5, 9, 5, tzinfo=SAO_PAULO),
            component.decoded('dtstart').astimezone(SAO_PAULO),
        )
        self.assertEqual('Reunião, equipe; São Paulo', str(component['summary']))
        self.assertEqual('PENDENTE', str(component['x-agenda-status']))
        self.assertEqual('America/Sao_Paulo', str(calendar['X-WR-TIMEZONE']))
        timezone_component = next(item for item in calendar.walk('VTIMEZONE'))
        self.assertEqual('America/Sao_Paulo', str(timezone_component['TZID']))
        repeated = Calendar.from_ical(self.client.get(self.custom_query('ics')).data)
        repeated_event = next(item for item in repeated.walk('VEVENT'))
        self.assertEqual(str(component['uid']), str(repeated_event['uid']))

    def test_empty_export_custom_period_and_invalid_parameters(self):
        response = self.client.get(self.custom_query('xlsx', category='Pessoal'))
        self.assertEqual(200, response.status_code)
        workbook = load_workbook(BytesIO(response.data))
        self.assertEqual(1, workbook['Compromissos'].max_row)
        empty_pdf = self.client.get(self.custom_query('pdf', category='Pessoal'))
        empty_text = ' '.join(
            ' '.join((page.extract_text() or '').split())
            for page in PdfReader(BytesIO(empty_pdf.data)).pages
        )
        self.assertIn('Nenhum compromisso', empty_text)
        empty_ics = Calendar.from_ical(
            self.client.get(self.custom_query('ics', category='Pessoal')).data
        )
        self.assertEqual([], empty_ics.walk('VEVENT'))
        self.add_event('Prioridade normal', priority='Normal')
        response = self.client.get(self.custom_query('xlsx', priority='Normal'))
        self.assertEqual(200, response.status_code)
        workbook = load_workbook(BytesIO(response.data))
        self.assertEqual(2, workbook['Compromissos'].max_row)
        self.assertEqual(400, self.client.get(
            '/events/export?period=custom&start_date=2026-11-06&end_date=2026-11-05&format=pdf'
        ).status_code)
        self.assertEqual(400, self.client.get('/events/export?format=invalid').status_code)

    def test_today_week_and_month_periods_use_sao_paulo_dates(self):
        self.add_event('Hoje')
        self.add_event('Dentro da semana', day=date(2026, 11, 6), hour=10)
        self.add_event('Fora do mês', day=date(2026, 12, 1), hour=11)
        fixed_now = datetime(2026, 11, 5, 8, 45, tzinfo=SAO_PAULO)
        with patch('webapp.routes.local_now', return_value=fixed_now):
            today = self.client.get('/events/export?period=today&format=xlsx')
            week = self.client.get('/events/export?period=week&format=xlsx')
            month = self.client.get('/events/export?period=month&format=xlsx')
        self.assertEqual(2, load_workbook(BytesIO(today.data))['Compromissos'].max_row)
        self.assertEqual(3, load_workbook(BytesIO(week.data))['Compromissos'].max_row)
        self.assertEqual(3, load_workbook(BytesIO(month.data))['Compromissos'].max_row)

    def test_export_requires_agenda_permission_and_pages_show_modal(self):
        self.assertIn(b'eventExportDialog', self.client.get('/calendar').data)
        self.assertIn(b'eventExportDialog', self.client.get('/events').data)
        with self.app.app_context():
            manager = db.session.scalar(db.select(User).where(User.email == 'admin@example.test'))
            manager.role = 'reader'
            manager.agenda_access = False
            db.session.commit()
        self.assertEqual(403, self.client.get(self.custom_query()).status_code)

    def test_pdf_paginates_long_exports(self):
        for index in range(75):
            self.add_event(
                f'Compromisso {index + 1}',
                day=date(2026, 11, 1 + (index % 28)),
                hour=8 + (index % 10),
            )
        response = self.client.get(
            '/events/export?period=custom&start_date=2026-11-01&end_date=2026-11-30&format=pdf'
        )
        self.assertEqual(200, response.status_code)
        self.assertGreater(len(PdfReader(BytesIO(response.data)).pages), 1)


if __name__ == '__main__':
    unittest.main()
