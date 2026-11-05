"""A mesma suíte de regras roda no SQLite desktop e no adaptador da web.

Todos os bancos são temporários; nenhuma variável de produção é consultada.
"""
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest

from agenda.finance import FinanceService, due_in_month, parse_amount
from agenda.finance_storage import SQLiteFinanceRepository
from agenda.storage import Database


class FinanceCases:
    def bill(self, **overrides):
        values = {'title': 'Energia', 'category': 'Escritório', 'amount': '1.234,56',
                  'due_date': '2024-01-31', 'responsible': 'Gestora',
                  'recurrence': 'Única', 'notes': 'Consumo da sede'}
        values.update(overrides)
        return self.service.save_bill(values)

    def test_create_update_delete_and_totals(self):
        row_id = self.bill()
        row = self.service.get_bill(row_id)
        self.assertEqual(123456, row['amount_cents'])
        self.assertEqual('1234.56', row['amount'])
        self.service.save_bill({'title': 'Energia atualizada', 'amount': '99,90'}, row_id)
        rows = self.service.list_bills(month='2024-01', today='2024-01-30')
        self.assertEqual(['Energia atualizada'], [r['title'] for r in rows])
        self.assertEqual('99.90', self.service.totals(rows)['to_pay'])
        self.assertTrue(rows[0]['near_due'])
        self.service.delete_bill(row_id)
        self.assertIsNone(self.service.get_bill(row_id))
        self.assertEqual([], self.service.list_bills(month='2024-01'))

    def test_filters_status_dates_and_exact_decimal_totals(self):
        a = self.bill(amount='0,10', due_date='2024-01-10')
        b = self.bill(amount='0.20', due_date='2024-01-20', category='Pessoal')
        self.bill(amount='0,30', due_date='2024-01-30')
        self.service.pay_bill(a, '2024-01-09')
        rows = self.service.list_bills(month='2024-01', today='2024-01-25')
        self.assertEqual({'to_pay': '0.50', 'paid': '0.10', 'overdue': '0.20'},
                         {key: self.service.totals(rows)[key] for key in ('to_pay', 'paid', 'overdue')})
        self.assertEqual([b], [r['id'] for r in self.service.list_bills(
            month='2024-01', status='Vencida', today='2024-01-25')])
        self.assertEqual([b], [r['id'] for r in self.service.list_bills(
            category='pessoal', due_start='2024-01-15', due_end='2024-01-25')])

    def test_monthly_short_months_recover_original_day(self):
        self.bill(recurrence='Mensal')
        feb = self.service.list_bills(month='2024-02')
        march = self.service.list_bills(month='2024-03')
        self.assertEqual(['2024-02-29'], [r['due_date'] for r in feb])
        self.assertEqual(['2024-03-31'], [r['due_date'] for r in march])
        self.assertEqual(['2025-02-28'], [r['due_date'] for r in self.service.list_bills(month='2025-02')])
        self.assertEqual(['2025-03-31'], [r['due_date'] for r in self.service.list_bills(month='2025-03')])
        self.assertEqual(1, len(self.service.list_bills(month='2025-03')))

    def test_monthly_29_30_and_31(self):
        for day in (29, 30, 31):
            with self.subTest(day=day):
                row_id = self.bill(due_date=f'2023-01-{day}', recurrence='Mensal', title=str(day))
                series = self.service.get_bill(row_id)['series_id']
                feb = [r for r in self.service.list_bills(month='2023-02') if r['series_id'] == series]
                mar = [r for r in self.service.list_bills(month='2023-03') if r['series_id'] == series]
                self.assertEqual(['2023-02-28'], [r['due_date'] for r in feb])
                self.assertEqual([f'2023-03-{day}'], [r['due_date'] for r in mar])

    def test_payment_is_idempotent_and_history_survives_delete(self):
        row_id = self.bill(recurrence='Mensal')
        self.service.pay_bill(row_id, '2024-01-30')
        self.service.pay_bill(row_id, '2024-01-30')
        history = self.service.payment_history(row_id)
        self.assertEqual(1, len(history))
        self.assertEqual(123456, history[0]['amount_cents'])
        self.assertEqual('2024-01-30', self.service.get_bill(row_id)['paid_on'])
        self.assertEqual(1, len(self.service.list_bills(month='2024-02')))
        self.service.delete_bill(row_id)
        self.assertEqual(history, self.service.payment_history(row_id))
        self.assertEqual([], self.service.list_bills(month='2024-02'))

    def test_paid_snapshots_are_immutable_but_receipt_can_be_attached(self):
        row_id = self.bill()
        self.service.pay_bill(row_id, '2024-01-30')
        with self.assertRaises(ValueError):
            self.service.save_bill({'amount': '9.99', 'title': 'Alteração'}, row_id)
        self.service.attach_receipt(row_id, 'private-receipt-id')
        self.assertEqual('private-receipt-id', self.service.get_bill(row_id)['receipt_id'])
        self.assertEqual(123456, self.service.payment_history(row_id)[0]['amount_cents'])

    def test_edit_monthly_preserves_anchor_and_updates_unpaid_future(self):
        first = self.bill(recurrence='Mensal')
        self.service.list_bills(month='2024-04')
        feb = self.service.list_bills(month='2024-02')[0]
        self.service.save_bill({'title': 'Energia nova', 'amount': '20,00'}, feb['id'])
        march = self.service.list_bills(month='2024-03')[0]
        self.assertEqual('2024-03-31', march['due_date'])
        self.assertEqual('20.00', march['amount'])
        self.assertEqual('1234.56', self.service.get_bill(first)['amount'])
        self.service.save_bill({'due_date': '2024-02-27'}, feb['id'])
        self.assertEqual('2024-03-27', self.service.list_bills(month='2024-03')[0]['due_date'])

    def test_stop_monthly_keeps_past_and_paid_occurrences(self):
        first = self.bill(recurrence='Mensal')
        self.service.list_bills(month='2024-04')
        march = self.service.list_bills(month='2024-03')[0]
        self.service.pay_bill(march['id'], '2024-03-30')
        feb = self.service.list_bills(month='2024-02')[0]
        self.service.delete_bill(feb['id'])
        self.assertIsNotNone(self.service.get_bill(first))
        self.assertEqual('Paga', self.service.get_bill(march['id'])['status'])
        self.assertEqual([], self.service.list_bills(month='2024-04'))

    def test_calendar_and_7_3_1_reminder_claims_survive_restart(self):
        today = date.today()
        expected = []
        for days in (7, 3, 1):
            expected.append(self.bill(due_date=(today + timedelta(days=days)).isoformat()))
        self.bill(due_date=(today + timedelta(days=2)).isoformat())
        reminders = self.service.reminders(today)
        self.assertEqual(set(expected), {r['id'] for r in reminders})
        self.assertEqual(4, len(self.service.calendar_items(today, today + timedelta(days=7))))
        selected = reminders[0]
        self.assertTrue(self.service.claim_reminder(selected['id'], selected['days_before'], today))
        self.restart()
        self.assertFalse(self.service.claim_reminder(selected['id'], selected['days_before'], today))
        self.service.pay_bill(selected['id'], today)
        self.assertNotIn(selected['id'], {r['id'] for r in self.service.reminders(today)})

    def test_restart_persists_bills_payments_and_existing_agenda(self):
        row_id = self.bill(recurrence='Mensal')
        self.service.pay_bill(row_id, '2024-01-30')
        self.restart()
        self.assertEqual('Paga', self.service.get_bill(row_id)['status'])
        self.assertEqual(1, len(self.service.payment_history(row_id)))
        self.assertEqual(1, len(self.service.list_bills(month='2024-02')))

    def test_invalid_inputs_do_not_create_partial_series_or_payments(self):
        for overrides in ({'amount': 'NaN'}, {'amount': 'Infinity'}, {'amount': '-1'},
                          {'amount': '1.001'}, {'due_date': '31/02/2024'},
                          {'title': ''}, {'recurrence': 'semanal'}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.bill(**overrides)
        self.assertEqual([], self.service.list_bills())
        row_id = self.bill()
        with self.assertRaises(ValueError):
            self.service.pay_bill(row_id, date.today() + timedelta(days=1))
        self.assertEqual([], self.service.payment_history())
        with self.assertRaises(ValueError):
            self.service.list_bills(month='2024-13')
        with self.assertRaises(ValueError):
            self.service.list_bills(due_start='2024-02-01', due_end='2024-01-01')

    def test_date_limits_reject_expensive_queries_and_ancient_payment(self):
        row_id = self.bill(recurrence='Mensal')
        for query in ({'due_start': '0001-01-01'}, {'due_end': '9999-12-31'},
                      {'month': '9999-01'}, {'today': '9999-01-01'}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.service.list_bills(**query)
        with self.assertRaises(ValueError):
            self.service.pay_bill(row_id, '1899-12-31')
        self.assertEqual([], self.service.payment_history())
        self.assertEqual(1, len(self.service.list_bills(month='2024-01')))

    def test_edit_old_occurrence_does_not_restart_stopped_series(self):
        january = self.bill(recurrence='Mensal')
        march = self.service.list_bills(month='2024-03')[0]
        self.service.delete_bill(march['id'])
        self.service.save_bill({'notes': 'Corrigido depois do encerramento'}, january)
        self.assertEqual([], self.service.list_bills(month='2024-05'))

    def test_convert_single_to_monthly_and_back_keeps_occurrences_unique(self):
        row_id = self.bill(due_date='2024-01-15')
        self.service.save_bill({'recurrence': 'Mensal', 'due_date': '2024-01-31'}, row_id)
        february = self.service.list_bills(month='2024-02')
        self.assertEqual(1, len(february))
        self.assertEqual('2024-02-29', february[0]['due_date'])
        self.service.save_bill({'recurrence': 'Única'}, row_id)
        self.assertEqual([], self.service.list_bills(month='2024-02'))
        self.service.save_bill({'recurrence': 'Mensal'}, row_id)
        self.assertEqual(1, len(self.service.list_bills(month='2024-01')))
        self.assertEqual(1, len(self.service.list_bills(month='2024-02')))
        self.assertEqual(1, len(self.service.list_bills(month='2024-02')))


class SQLiteFinanceTests(FinanceCases, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-finance-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'agenda.db'
        self.database = Database(self.path)
        self.database.initialize()
        self.event_id = self.database.save_event({'title': 'Compromisso preservado',
            'event_date': '2024-01-15', 'event_time': '10:00'})
        self.service = FinanceService(SQLiteFinanceRepository(self.database))

    def restart(self):
        self.database = Database(self.path)
        self.database.initialize()
        self.service = FinanceService(SQLiteFinanceRepository(self.database))
        self.assertEqual('Compromisso preservado', self.database.get_event(self.event_id)['title'])


class WebFinanceRepositoryTests(FinanceCases, unittest.TestCase):
    def setUp(self):
        from webapp import create_app
        from webapp.finance_repository import WebFinanceRepository
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-finance-web-')
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / 'agenda.db'
        self.app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{path.as_posix()}',
                               'SECRET_KEY': 'temporary-finance-test-secret'})
        result = self.app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if result.exception:
            raise result.exception
        self.context = self.app.app_context()
        self.context.push()
        self.service = FinanceService(WebFinanceRepository())

    def tearDown(self):
        from webapp.models import db
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def restart(self):
        from webapp.models import db
        from webapp.finance_repository import WebFinanceRepository
        db.session.remove()
        db.engine.dispose()
        self.service = FinanceService(WebFinanceRepository())


class DateAndMoneyTests(unittest.TestCase):
    def test_decimal_parser_and_month_lengths(self):
        self.assertEqual(123456, parse_amount('R$ 1.234,56'))
        self.assertEqual(10, parse_amount('0.10'))
        self.assertEqual(date(2024, 2, 29), due_in_month(2024, 2, 31))
        self.assertEqual(date(2023, 2, 28), due_in_month(2023, 2, 29))


if __name__ == '__main__':
    unittest.main()
