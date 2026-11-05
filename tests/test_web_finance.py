from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from werkzeug.security import generate_password_hash

from agenda.finance import FinanceService
from webapp import create_app
from webapp.finance_repository import WebFinanceRepository
from webapp.models import db


class WebFinanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-finance-web-')
        self.addCleanup(self.temp.cleanup)
        self.config = {
            'TESTING': True, 'SECRET_KEY': 'finance-tests-only',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (Path(self.temp.name) / 'finance.db').as_posix(),
            'ADMIN_USERNAME': 'test-manager',
            'ADMIN_PASSWORD_HASH': generate_password_hash('Test-Password-Only!'),
            'SESSION_COOKIE_SECURE': False, 'WTF_CSRF_SSL_STRICT': False,
            'RECEIPT_STORAGE_DIR': str(Path(self.temp.name) / 'private-receipts'),
        }
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        result = self.app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if result.exception:
            raise result.exception

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def csrf(self, path='/login', client=None):
        response = (client or self.client).get(path)
        self.assertEqual(200, response.status_code)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def login(self, client=None):
        client = client or self.client
        response = client.post('/login', data={
            'csrf_token': self.csrf(client=client), 'username': 'test-manager',
            'password': 'Test-Password-Only!',
        })
        self.assertEqual(302, response.status_code)

    def form(self, **overrides):
        values = {'csrf_token': self.csrf('/bills/new'), 'title': 'Licença de teste',
                  'category': 'Operacional', 'amount': '1.234,56',
                  'due_date': date.today().isoformat(), 'responsible': 'Gestora',
                  'recurrence': 'Única', 'notes': 'Teste isolado'}
        values.update(overrides)
        return values

    def create_bill(self, **overrides):
        response = self.client.post('/bills/new', data=self.form(**overrides))
        self.assertEqual(302, response.status_code)
        self.assertRegex(response.headers['Location'], r'/bills/[^/]+/edit$')
        return response.headers['Location'].split('/')[-2]

    def get_bill(self, bill_id):
        with self.app.app_context():
            return FinanceService(WebFinanceRepository()).get_bill(bill_id)

    def test_auth_and_csrf_protect_finance(self):
        for path in ('/bills', '/bills/new', '/bills/history', '/bills/unknown/edit',
                     '/bills/unknown/receipt'):
            self.assertEqual(302, self.client.get(path).status_code, path)
        self.login()
        self.assertEqual(400, self.client.post('/bills/new', data={'title': 'Blocked'}).status_code)
        bill_id = self.create_bill()
        for suffix in ('pay', 'delete', 'receipt', 'edit'):
            self.assertEqual(400, self.client.post(f'/bills/{bill_id}/{suffix}').status_code)
        self.assertIsNotNone(self.get_bill(bill_id))

    def test_crud_payment_history_and_restart(self):
        self.login()
        bill_id = self.create_bill()
        bill = self.get_bill(bill_id)
        self.assertEqual(123456, bill['amount_cents'])
        edit_path = f'/bills/{bill_id}/edit'
        values = self.form(title='Conta revisada', amount='25,75')
        response = self.client.post(edit_path, data=values)
        self.assertEqual(302, response.status_code)
        self.assertEqual('Conta revisada', self.get_bill(bill_id)['title'])
        token = self.csrf(edit_path)
        for _ in range(2):
            response = self.client.post(f'/bills/{bill_id}/pay', data={
                'csrf_token': token, 'paid_on': date.today().isoformat(),
            })
            self.assertEqual(302, response.status_code)
        self.assertEqual('Paga', self.get_bill(bill_id)['status'])
        with self.app.app_context():
            self.assertEqual(1, len(FinanceService(WebFinanceRepository()).payment_history(bill_id)))
        self.assertIn(b'25,75', self.client.get('/bills/history').data)
        restarted = create_app(self.config)
        restarted_client = restarted.test_client()
        self.login(restarted_client)
        self.assertIn('Conta revisada'.encode(), restarted_client.get(edit_path).data)
        with restarted.app_context():
            db.session.remove()
            db.engine.dispose()
        self.client.post(f'/bills/{bill_id}/delete', data={'csrf_token': token})
        self.assertIsNone(self.get_bill(bill_id))
        self.assertIn('Conta revisada'.encode(), self.client.get('/bills/history').data)

    def test_filters_totals_calendar_and_reminders(self):
        self.login()
        due = date.today() + timedelta(days=7)
        bill_id = self.create_bill(title='Vencimento integrado', due_date=due.isoformat(), amount='90,10')
        month = due.strftime('%Y-%m')
        response = self.client.get(f'/bills?month={month}&category=Operacional&status=Pendente')
        self.assertEqual(200, response.status_code)
        self.assertIn(b'90,10', response.data)
        self.assertIn('Vencimento integrado'.encode(), response.data)
        filtered = self.client.get(f'/bills?month={month}&category=Outra')
        self.assertNotIn('Vencimento integrado'.encode(), filtered.data)
        calendar = self.client.get(f'/calendar?month={month}-01')
        self.assertEqual(200, calendar.status_code)
        self.assertIn(f'/bills/{bill_id}/edit'.encode(), calendar.data)
        daily = self.client.get(f'/calendar/date/{due.isoformat()}')
        self.assertIn('Vencimento integrado'.encode(), daily.data)
        reminders = self.client.get('/reminders')
        self.assertIn('Vencimento integrado'.encode(), reminders.data)
        self.assertIn(b'Prazo', reminders.data)
        self.assertEqual(400, self.client.get('/bills?month=invalid').status_code)
        self.assertEqual(400, self.client.get('/bills?due_start=2026-12-30&due_end=2026-01-01').status_code)

    def test_paid_bill_is_removed_from_alert_center(self):
        self.login()
        yesterday = date.today() - timedelta(days=1)
        bill_id = self.create_bill(title='Conta vencida pendente', due_date=yesterday.isoformat())
        reminders = self.client.get('/reminders')
        self.assertIn('Conta vencida pendente'.encode(), reminders.data)
        response = self.client.post(f'/reminders/bill/{bill_id}/complete', data={
            'csrf_token': self.csrf('/reminders'),
        })
        self.assertEqual(302, response.status_code)
        self.assertEqual('Paga', self.get_bill(bill_id)['status'])
        self.assertNotIn('Conta vencida pendente'.encode(), self.client.get('/reminders').data)

    def test_receipts_private_validated_and_overposting_rejected(self):
        self.login()
        invalid = self.form(receipt=(BytesIO(b'<script>private payload</script>'), 'falso.png'))
        response = self.client.post('/bills/new', data=invalid, content_type='multipart/form-data')
        self.assertEqual(400, response.status_code)
        self.assertNotIn(b'private payload', response.data)
        png = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(png, format='PNG')
        png.seek(0)
        response = self.client.post('/bills/new', data=self.form(
            receipt=(png, '../../comprovante.png'), receipt_id='injetado.png'),
            content_type='multipart/form-data')
        self.assertEqual(302, response.status_code)
        bill_id = response.headers['Location'].split('/')[-2]
        bill = self.get_bill(bill_id)
        self.assertRegex(bill['receipt_id'], r'^[a-f0-9]{32}\.png$')
        self.assertTrue((Path(self.config['RECEIPT_STORAGE_DIR']) / bill['receipt_id']).is_file())
        response = self.client.get(f'/bills/{bill_id}/receipt')
        self.assertEqual(200, response.status_code)
        self.assertEqual('nosniff', response.headers['X-Content-Type-Options'])
        self.assertIn('attachment;', response.headers['Content-Disposition'])
        self.assertIn('no-store', response.headers['Cache-Control'])
        response.close()
        anonymous = self.app.test_client()
        self.assertEqual(302, anonymous.get(f'/bills/{bill_id}/receipt').status_code)
        self.assertEqual(404, self.client.get('/static/' + bill['receipt_id']).status_code)
        plain = self.create_bill(receipt_id=bill['receipt_id'])
        self.assertFalse(self.get_bill(plain)['receipt_id'])
        self.assertEqual(404, self.client.get(f'/bills/{plain}/receipt').status_code)

    def test_invalid_form_does_not_leave_uploaded_orphan(self):
        self.login()
        png = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(png, format='PNG')
        png.seek(0)
        response = self.client.post('/bills/new', data=self.form(
            title='', receipt=(png, 'imagem.png')), content_type='multipart/form-data')
        self.assertEqual(400, response.status_code)
        self.assertEqual([], list(Path(self.config['RECEIPT_STORAGE_DIR']).glob('*')))

    def test_receipt_database_failure_removes_file_and_hides_error_details(self):
        self.login()
        bill_id = self.create_bill()
        self.client.post(f'/bills/{bill_id}/pay', data={
            'csrf_token': self.csrf(f'/bills/{bill_id}/edit'),
            'paid_on': date.today().isoformat(),
        })
        png = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(png, format='PNG')
        png.seek(0)
        token = self.csrf(f'/bills/{bill_id}/edit')
        marker = 'PRIVATE-DATABASE-PARAMETER-MUST-NOT-APPEAR'
        with patch('webapp.finance_routes.FinanceService.attach_receipt',
                   side_effect=RuntimeError(marker)), self.assertLogs(self.app.logger, level='ERROR') as logs:
            response = self.client.post(f'/bills/{bill_id}/receipt', data={
                'csrf_token': token, 'receipt': (png, 'comprovante.png'),
            }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertNotIn(marker.encode(), response.data)
        self.assertNotIn(marker, '\n'.join(logs.output))
        self.assertEqual([], list(Path(self.config['RECEIPT_STORAGE_DIR']).glob('*')))
        self.assertFalse(self.get_bill(bill_id)['receipt_id'])

    def test_calendar_invalid_dates_and_service_validation_return_400(self):
        self.login()
        for month in ('0001-01-01', '1899-12-01', '2200-01-01', '9999-12-01'):
            self.assertEqual(400, self.client.get('/calendar?month=' + month).status_code)
        with patch('webapp.routes.FinanceService.calendar_items', side_effect=ValueError('invalid range')):
            response = self.client.get('/calendar?month=2026-12-01')
        self.assertEqual(400, response.status_code)
        self.assertNotIn(b'invalid range', response.data)


if __name__ == '__main__':
    unittest.main()
