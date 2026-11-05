"""Smoke desktop com SQLite temporário: python -m tests.smoke_features."""
from datetime import date, timedelta
from pathlib import Path
import tempfile
import traceback
from unittest.mock import patch

from PIL import Image

from agenda.storage import Database
from agenda.ui import AgendaApp
from tests.smoke_ui import fail_ui, pump, set_field, widget_text, assert_horizontal_fit


def run():
    with tempfile.TemporaryDirectory(prefix='agenda-features-ui-') as folder:
        app = AgendaApp(Database(Path(folder) / 'agenda.db'), enable_reminders=False)
        app.callback_errors = []
        app.report_callback_exception = lambda *exc: app.callback_errors.append(''.join(traceback.format_exception(*exc)))
        try:
            with patch.object(AgendaApp, 'show_error', fail_ui):
                pump(app)
                app.navigate('catalog')
                pump(app)
                assert_horizontal_fit(app)
                app.open_dialog()
                pump(app)
                form = app._dialog
                assert set(form.inputs) == {'name', 'url', 'category', 'notes', 'is_corporate', 'password'}
                set_field(form.inputs['name'], 'Portal de serviços')
                set_field(form.inputs['url'], 'https://example.org/login')
                set_field(form.inputs['category'], 'Serviços')
                form.save()
                pump(app)
                assert form.saved
                assert 'Portal de serviços' in widget_text(app.content)
                access_id = form.saved_id
                app.open_feature_dialog('access', app.catalog.get_entry(access_id))
                pump(app)
                set_field(app._dialog.inputs['category'], 'Portal')
                app._dialog.save()
                pump(app)
                assert app.catalog.list_entries(search='Portal')[0]['category'] == 'Portal'
                app.navigate('bills')
                pump(app)
                assert_horizontal_fit(app)
                app.open_dialog()
                pump(app)
                form = app._dialog
                set_field(form.inputs['title'], 'Conta de demonstração')
                set_field(form.inputs['amount'], '120,50')
                set_field(form.inputs['category'], 'Escritório')
                set_field(form.inputs['recurrence'], 'Mensal')
                form.save()
                pump(app)
                assert form.saved
                bill_id = form.saved_id
                assert 'Conta de demonstração' in widget_text(app.content)
                app.open_feature_dialog('bill', app.finance.get_bill(bill_id))
                pump(app)
                set_field(app._dialog.inputs['amount'], '125,50')
                app._dialog.save()
                pump(app)
                assert app.finance.get_bill(bill_id)['amount_cents'] == 12550
                month = (date.today().replace(day=28) + timedelta(days=4)).strftime('%Y-%m')
                assert len(app.finance.list_bills(month=month)) == 1
                app.pay_bill_dialog(app.finance.get_bill(bill_id))
                pump(app)
                payment = app._dialog
                payment.save()
                assert payment.saved
                app.render()
                pump(app)
                assert 'Paga' in ' '.join(widget_text(app.content))
                image = Path(folder) / 'recibo.png'
                Image.new('RGB', (8, 8), 'white').save(image)
                with patch('agenda.feature_views.filedialog.askopenfilename', return_value=str(image)):
                    app.attach_bill_receipt(app.finance.get_bill(bill_id))
                pump(app)
                receipt_id = app.finance.get_bill(bill_id)['receipt_id']
                assert receipt_id.endswith('.png')
                with patch('agenda.feature_views.os.startfile') as open_file:
                    app.open_bill_receipt(app.finance.get_bill(bill_id))
                    assert open_file.call_args.args[0].is_file()
                app.navigate('calendar')
                pump(app)
                assert 'Contas com vencimento nesta data' in widget_text(app.content)
                assert_horizontal_fit(app)
                app.geometry('1000x650')
                pump(app)
                for view in ('dashboard', 'events', 'tasks', 'settings', 'catalog', 'bills'):
                    app.navigate(view)
                    pump(app)
                    assert_horizontal_fit(app)
                reminder_id = app.finance.save_bill({'title': 'Aviso amanhã', 'amount': '1,00',
                      'due_date': (date.today() + timedelta(days=1)).isoformat(), 'recurrence': 'Única'})
                with patch.object(app, 'show_reminder') as notify:
                    app.check_reminders()
                    assert notify.call_count == 1
                    app.after_cancel(app._reminder_job)
                    app._reminder_job = None
                    app.check_reminders()
                    assert notify.call_count == 1
                with patch('agenda.feature_views.messagebox.askyesno', return_value=True):
                    app.delete_access(app.catalog.get_entry(access_id))
                assert app.catalog.get_entry(access_id) is None
                app.finance.delete_bill(bill_id)
                assert app.finance.payment_history(bill_id)
        finally:
            app.close()
    print('Smoke de catálogo, contas, pagamentos e calendário concluído com SQLite temporário.')


if __name__ == '__main__':
    run()
