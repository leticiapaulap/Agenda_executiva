"""Regras de contas, sem dependência da interface ou do banco de dados.

Valores persistidos em centavos; ocorrências mensais têm uma chave única por
série/mês. O dia original é mantido mesmo em fevereiro e meses com 30 dias.
"""
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from uuid import uuid4

from .services import parse_date


RECURRENCES = ('Única', 'Mensal')
STATUSES = ('Pendente', 'Paga', 'Vencida')


def money(cents):
    return format(Decimal(cents) / 100, '.2f')


def parse_amount(value):
    raw = str(value or '').strip().replace('R$', '').replace(' ', '')
    if ',' in raw:
        raw = raw.replace('.', '').replace(',', '.')
    try:
        amount = Decimal(raw)
        if not amount.is_finite() or amount < 0 or amount > Decimal('999999999.99'):
            raise InvalidOperation
        cents = amount * 100
        if cents != cents.to_integral_value():
            raise InvalidOperation
        return int(cents)
    except (InvalidOperation, ValueError):
        raise ValueError('Informe um valor válido em reais, com até duas casas decimais.') from None


def due_in_month(year, month, anchor_day):
    return date(year, month, min(anchor_day, monthrange(year, month)[1]))


def next_month(value):
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def finance_date(value):
    result = parse_date(value)
    if not 1900 <= result.year <= 2199:
        raise ValueError('Informe uma data entre 1900 e 2199.')
    return result


def _month(value):
    text = str(value)
    if not re.fullmatch(r'\d{4}-\d{2}', text):
        raise ValueError('Informe um mês válido no formato AAAA-MM.')
    try:
        result = date.fromisoformat(text + '-01')
        if not 1900 <= result.year <= 2199:
            raise ValueError
        return result
    except ValueError:
        raise ValueError('Informe um mês válido entre 1900 e 2199.') from None


def _text(data, key, limit, default=''):
    value = str(data.get(key, default) or '').strip()
    if len(value) > limit:
        raise ValueError(f'O campo {key} ultrapassa o limite permitido.')
    return value


class FinanceService:
    def __init__(self, repository):
        self.repo = repository

    def _materialize(self, store, through):
        """Preenche lacunas até o mês solicitado, sob transação serializada."""
        through = min(through, date(2199, 12, 31))
        # Inclui ocorrências excluídas: elas são marcadores permanentes e não
        # devem reaparecer quando uma série for consultada novamente.
        existing = {(row['series_id'], row['occurrence_month']) for row in store.all('bills')
                    if row['series_id']}
        for series in store.all('series'):
            if not series['active']:
                continue
            start = parse_date(series['start_date'])
            cursor = start.replace(day=1)
            while cursor <= through.replace(day=1):
                occurrence_month = cursor.strftime('%Y-%m')
                if (series['id'], occurrence_month) in existing:
                    cursor = next_month(cursor)
                    continue
                due = due_in_month(cursor.year, cursor.month, series['anchor_day'])
                values = {key: series[key] for key in (
                    'title', 'category', 'amount_cents', 'responsible', 'notes')}
                values.update(id=str(uuid4()), series_id=series['id'],
                              occurrence_month=occurrence_month,
                              due_date=due.isoformat(), anchor_day=series['anchor_day'],
                              recurrence='Mensal', paid_on='', receipt_id='', deleted=0)
                store.insert_occurrence(values)
                existing.add((series['id'], occurrence_month))
                cursor = next_month(cursor)

    @staticmethod
    def _view(row, today=None):
        if row is None:
            return None
        result = dict(row)
        today = parse_date(today or date.today())
        due = parse_date(row['due_date'])
        result['amount'] = money(row['amount_cents'])
        result['status'] = 'Paga' if row['paid_on'] else ('Vencida' if due < today else 'Pendente')
        result['near_due'] = not row['paid_on'] and 0 <= (due - today).days <= 7
        return result

    def save_bill(self, data, bill_id=None):
        with self.repo.transaction() as store:
            old = store.get('bills', bill_id, lock=True) if bill_id else None
            if bill_id and (old is None or old['deleted']):
                raise ValueError('Conta não encontrada.')
            if old and old['paid_on']:
                raise ValueError('Uma conta paga possui histórico imutável. Cadastre uma nova conta.')
            values = {**(old or {}), **data}
            title = _text(values, 'title', 200) or _text(values, 'name', 200)
            if not title:
                raise ValueError('Informe o nome da conta.')
            due = finance_date(values.get('due_date', ''))
            recurrence = values.get('recurrence', 'Única')
            if recurrence not in RECURRENCES:
                raise ValueError('Selecione pagamento único ou recorrência mensal.')
            normalized = {
                'title': title, 'category': _text(values, 'category', 80) or 'Geral',
                'amount_cents': parse_amount(values.get('amount', money(old['amount_cents']) if old else '')),
                'due_date': due.isoformat(), 'responsible': _text(values, 'responsible', 200),
                'recurrence': recurrence, 'notes': _text(values, 'notes', 5000),
                'receipt_id': _text(values, 'receipt_id', 128),
            }
            # An edit affects this occurrence and the template for future occurrences;
            # already paid occurrences and their snapshots never change.
            series_id = old['series_id'] if old else None
            old_due = parse_date(old['due_date']) if old else None
            anchor = old['anchor_day'] if old and old_due == due else due.day
            if old and old['occurrence_month'] != due.strftime('%Y-%m') and series_id:
                raise ValueError('Altere o dia da ocorrência mensal dentro do mesmo mês.')
            if recurrence == 'Mensal':
                template = {key: normalized[key] for key in (
                    'title', 'category', 'amount_cents', 'responsible', 'notes')}
                template.update(anchor_day=anchor, active=1)
                if series_id:
                    # Corrigir uma ocorrência antiga não reativa uma série
                    # encerrada por exclusão ou conversão para pagamento único.
                    template['active'] = store.get('series', series_id)['active']
                    store.update('series', series_id, template)
                else:
                    series_id = str(uuid4())
                    store.insert('series', {**template, 'id': series_id, 'start_date': due.isoformat()})
            elif series_id:
                store.update('series', series_id, {'active': 0})
                self._cancel_future(store, series_id, old['occurrence_month'])
                series_id = None
            normalized.update(series_id=series_id, anchor_day=anchor,
                              occurrence_month=due.strftime('%Y-%m') if series_id else None)
            if old:
                store.update('bills', bill_id, normalized)
                if series_id:
                    for row in store.all('bills'):
                        if (row['series_id'] == series_id and row['id'] != bill_id
                                and row['occurrence_month'] > old['occurrence_month']
                                and not row['paid_on'] and not row['deleted']):
                            future = {key: normalized[key] for key in (
                                'title', 'category', 'amount_cents', 'responsible', 'notes', 'anchor_day')}
                            target = _month(row['occurrence_month'])
                            future['due_date'] = due_in_month(target.year, target.month, anchor).isoformat()
                            store.update('bills', row['id'], future)
            else:
                bill_id = str(uuid4())
                store.insert('bills', {**normalized, 'id': bill_id, 'paid_on': '', 'deleted': 0})
            return bill_id

    def list_bills(self, month='', status='', category='', due_start='', due_end='', today=None):
        today = finance_date(today or date.today())
        target = _month(month) if month else today.replace(day=1)
        start = finance_date(due_start) if due_start else None
        end = finance_date(due_end) if due_end else None
        if start and end and start > end:
            raise ValueError('O vencimento inicial deve ser anterior ao final.')
        if status not in ('', 'Todos', 'Todas', *STATUSES):
            raise ValueError('Selecione um status válido.')
        through = max(target, end.replace(day=1) if end else target)
        with self.repo.transaction() as store:
            self._materialize(store, through)
            rows = [self._view(row, today) for row in store.all('bills') if not row['deleted']]
        rows = [row for row in rows if
                (not month or row['due_date'].startswith(target.strftime('%Y-%m')))
                and (status in ('', 'Todos', 'Todas') or row['status'] == status)
                and (category in ('', 'Todas', 'Todos') or row['category'].casefold() == category.casefold())
                and (not start or row['due_date'] >= start.isoformat())
                and (not end or row['due_date'] <= end.isoformat())]
        return sorted(rows, key=lambda row: (row['due_date'], row['title'].casefold(), row['id']))

    def get_bill(self, bill_id):
        with self.repo.transaction() as store:
            row = store.get('bills', bill_id)
            return self._view(row) if row and not row['deleted'] else None

    @staticmethod
    def _cancel_future(store, series_id, after_month):
        for row in store.all('bills'):
            if (row['series_id'] == series_id and not row['paid_on']
                    and row['occurrence_month'] > after_month):
                store.update('bills', row['id'], {'deleted': 1})

    def delete_bill(self, bill_id):
        with self.repo.transaction() as store:
            row = store.get('bills', bill_id, lock=True)
            if not row or row['deleted']:
                raise ValueError('Conta não encontrada.')
            store.update('bills', bill_id, {'deleted': 1})
            if row['series_id']:
                store.update('series', row['series_id'], {'active': 0})
                self._cancel_future(store, row['series_id'], row['occurrence_month'])

    def pay_bill(self, bill_id, paid_on=None):
        paid_on = finance_date(paid_on or date.today())
        if paid_on > date.today():
            raise ValueError('A data do pagamento não pode estar no futuro.')
        with self.repo.transaction() as store:
            row = store.get('bills', bill_id, lock=True)
            if not row or row['deleted']:
                raise ValueError('Conta não encontrada.')
            if row['paid_on']:
                return bill_id  # Reenvio do formulário é idempotente.
            store.update('bills', bill_id, {'paid_on': paid_on.isoformat()})
            snapshot = {key: row[key] for key in ('title', 'category', 'amount_cents',
                                                  'due_date', 'responsible')}
            store.insert('payments', {**snapshot, 'id': str(uuid4()), 'bill_id': bill_id,
                                      'paid_on': paid_on.isoformat()})
            if row['series_id']:
                self._materialize(store, next_month(parse_date(row['due_date'])))
            return bill_id

    def attach_receipt(self, bill_id, receipt_id):
        value = _text({'receipt_id': receipt_id}, 'receipt_id', 128)
        with self.repo.transaction() as store:
            row = store.get('bills', bill_id, lock=True)
            if not row or row['deleted']:
                raise ValueError('Conta não encontrada.')
            store.update('bills', bill_id, {'receipt_id': value})

    def payment_history(self, bill_id=None):
        with self.repo.transaction() as store:
            rows = store.all('payments')
        return [dict(row, amount=money(row['amount_cents'])) for row in
                sorted(rows, key=lambda row: (row['paid_on'], row['id']), reverse=True)
                if not bill_id or row['bill_id'] == bill_id]

    @staticmethod
    def totals(rows):
        result = {'to_pay_cents': sum(row['amount_cents'] for row in rows if row['status'] != 'Paga'),
                  'paid_cents': sum(row['amount_cents'] for row in rows if row['status'] == 'Paga'),
                  'overdue_cents': sum(row['amount_cents'] for row in rows if row['status'] == 'Vencida')}
        result.update({key: money(result[key + '_cents']) for key in ('to_pay', 'paid', 'overdue')})
        return result

    def calendar_items(self, start, end):
        return self.list_bills(due_start=start, due_end=end)

    def reminders(self, today=None):
        today = finance_date(today or date.today())
        end = min(today + timedelta(days=7), date(2199, 12, 31))
        rows = self.list_bills(due_start=today, due_end=end, today=today)
        return [dict(row, days_before=(parse_date(row['due_date']) - today).days)
                for row in rows if row['status'] != 'Paga'
                and (parse_date(row['due_date']) - today).days in (7, 3, 1)]

    def claim_reminder(self, bill_id, days_before, today=None):
        today = finance_date(today or date.today())
        if days_before not in (7, 3, 1):
            raise ValueError('Antecedência inválida para lembrete.')
        with self.repo.transaction() as store:
            row = store.get('bills', bill_id, lock=True)
            if (not row or row['deleted'] or row['paid_on']
                    or (parse_date(row['due_date']) - today).days != days_before):
                return False
            return store.claim_reminder({
                'id': f'{bill_id}:{days_before}:{today.isoformat()}', 'bill_id': bill_id,
                'days_before': days_before, 'reminder_date': today.isoformat(),
                'notified_at': datetime.now().isoformat(timespec='seconds')})
