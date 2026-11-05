"""Organizational accounts protected by per-user permissions and CSRF."""
from datetime import date
import mimetypes

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, send_file, url_for)
from flask_login import login_required

from agenda.finance import FinanceService
from agenda.receipts import ReceiptStore
from .finance_repository import WebFinanceRepository
from .models import db
from .permissions import can_read_finance, can_write_finance


bp = Blueprint('finance', __name__)


@bp.before_request
@login_required
def protect_finance():
    if not can_read_finance():
        abort(403)
    if request.method != 'GET' and not can_write_finance():
        abort(403)
    if request.endpoint == 'finance.new_bill' and not can_write_finance():
        abort(403)
    return None


@bp.after_request
def private_responses(response):
    response.headers['Cache-Control'] = 'no-store, private'
    return response


def service():
    return FinanceService(WebFinanceRepository())


def receipt_store():
    root = current_app.config.get('RECEIPT_STORAGE_DIR')
    if not root:
        raise ValueError('Configure o armazenamento privado de comprovantes antes de anexar arquivos.')
    return ReceiptStore(root)


def _blank_bill():
    return {'title': '', 'category': '', 'amount': '', 'due_date': date.today().isoformat(),
            'responsible': '', 'recurrence': 'Única', 'notes': '', 'receipt_id': ''}


def _save_bill(bill_id=None, initial=None):
    # O identificador privado do comprovante nunca é aceito do formulário.
    values = {key: request.form.get(key, '') for key in
              ('title', 'category', 'amount', 'due_date', 'responsible', 'recurrence', 'notes')}
    stored_id = None
    try:
        attachment = request.files.get('receipt')
        if attachment and attachment.filename:
            saved = receipt_store().save(attachment.stream, attachment.filename)
            stored_id = saved['id']
            values['receipt_id'] = stored_id
        saved_id = service().save_bill(values, bill_id=bill_id)
        db.session.commit()
        flash('Conta salva com sucesso.', 'success')
        return redirect(url_for('finance.edit_bill', bill_id=saved_id))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    except Exception:
        db.session.rollback()
        # Não registrar formulários nem parâmetros de SQL: comprovantes são privados.
        current_app.logger.error('Falha ao salvar conta.')
        flash('Não foi possível salvar a conta. Tente novamente.', 'error')
    if stored_id:
        receipt_store().delete(stored_id)
    bill = {**_blank_bill(), **(initial or {}), **values}
    bill.pop('receipt_id', None)
    bill['receipt_id'] = (initial or {}).get('receipt_id', '')
    return render_template('bill_form.html', bill=bill, editing=bill_id is not None,
                           payments=service().payment_history(bill_id) if bill_id else [],
                           uploads_enabled=bool(current_app.config.get('RECEIPT_STORAGE_DIR')),
                           today=date.today().isoformat()), 400


@bp.get('/bills')
def bills():
    filters = {key: request.args.get(key, '').strip() for key in
               ('month', 'status', 'category', 'due_start', 'due_end')}
    filters['month'] = filters['month'] or date.today().strftime('%Y-%m')
    try:
        rows = service().list_bills(**filters)
    except ValueError:
        abort(400)
    return render_template('bills.html', bills=rows, filters=filters,
                           totals=service().totals(rows), today=date.today().isoformat())


@bp.route('/bills/new', methods=['GET', 'POST'])
def new_bill():
    if request.method == 'POST':
        return _save_bill()
    return render_template('bill_form.html', bill=_blank_bill(), editing=False,
                           payments=[], uploads_enabled=bool(current_app.config.get('RECEIPT_STORAGE_DIR')),
                           today=date.today().isoformat())


@bp.route('/bills/<bill_id>/edit', methods=['GET', 'POST'])
def edit_bill(bill_id):
    bill = service().get_bill(bill_id)
    if bill is None:
        abort(404)
    if request.method == 'POST':
        return _save_bill(bill_id, bill)
    return render_template('bill_form.html', bill=bill, editing=True,
                           payments=service().payment_history(bill_id),
                           uploads_enabled=bool(current_app.config.get('RECEIPT_STORAGE_DIR')),
                           today=date.today().isoformat())


@bp.post('/bills/<bill_id>/pay')
def pay_bill(bill_id):
    if service().get_bill(bill_id) is None:
        abort(404)
    try:
        service().pay_bill(bill_id, request.form.get('paid_on', ''))
        db.session.commit()
        flash('Pagamento registrado. O histórico foi preservado.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    return redirect(url_for('finance.edit_bill', bill_id=bill_id))


@bp.post('/bills/<bill_id>/delete')
def delete_bill(bill_id):
    if service().get_bill(bill_id) is None:
        abort(404)
    service().delete_bill(bill_id)
    db.session.commit()
    flash('Conta excluída. Pagamentos anteriores permanecem no histórico.', 'success')
    return redirect(url_for('finance.bills'))


@bp.post('/bills/<bill_id>/receipt')
def attach_receipt(bill_id):
    if service().get_bill(bill_id) is None:
        abort(404)
    stored_id = None
    try:
        attachment = request.files.get('receipt')
        if not attachment or not attachment.filename:
            raise ValueError('Selecione uma imagem PNG ou JPEG.')
        saved = receipt_store().save(attachment.stream, attachment.filename)
        stored_id = saved['id']
        service().attach_receipt(bill_id, stored_id)
        db.session.commit()
        flash('Comprovante salvo.', 'success')
    except Exception as error:
        db.session.rollback()
        if stored_id:
            receipt_store().delete(stored_id)
        if not isinstance(error, (ValueError, OSError)):
            # Exceções do banco podem incluir parâmetros; registrar apenas contexto fixo.
            current_app.logger.error('Falha ao anexar comprovante.')
        flash('Não foi possível anexar. Envie uma imagem PNG ou JPEG de até 5 MB.', 'error')
    return redirect(url_for('finance.edit_bill', bill_id=bill_id))


@bp.get('/bills/history')
def history():
    return render_template('bill_history.html', payments=service().payment_history())


@bp.get('/bills/<bill_id>/receipt')
def receipt(bill_id):
    bill = service().get_bill(bill_id)
    if bill is None or not bill.get('receipt_id'):
        abort(404)
    try:
        path = receipt_store().open(bill['receipt_id'])
    except (OSError, ValueError):
        abort(404)
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    response = send_file(path, mimetype=mime, as_attachment=True,
                         download_name='comprovante' + path.suffix, conditional=False)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
