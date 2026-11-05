"""Adaptador transacional PostgreSQL, com SQLite somente nos testes web."""
from contextlib import contextmanager
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import Bill, BillPayment, BillReminderLog, BillSeries, db


MODELS = {'series': BillSeries, 'bills': Bill, 'payments': BillPayment}
DATE_FIELDS = {'due_date', 'paid_on', 'start_date', 'reminder_date'}


def _values(values):
    return {key: date.fromisoformat(value) if key in DATE_FIELDS and value else
            (None if key in DATE_FIELDS and not value else value) for key, value in values.items()}


def _row(model):
    if model is None:
        return None
    return {column.name: (getattr(model, column.name).isoformat() if getattr(model, column.name)
                         else '') if column.name in DATE_FIELDS else getattr(model, column.name)
            for column in model.__table__.columns}


class WebFinanceRepository:
    @contextmanager
    def transaction(self):
        try:
            # Serialize shared financial mutations across all authorized users
            # and workers, including monthly generation, edits and payments.
            if db.session.get_bind().dialect.name == 'postgresql':
                db.session.execute(text('SELECT pg_advisory_xact_lock(714288402)'))
            yield self
            db.session.commit()
        except BaseException:
            db.session.rollback()
            raise

    def all(self, table):
        return [_row(row) for row in db.session.scalars(select(MODELS[table])).all()]

    def get(self, table, row_id, lock=False):
        model = MODELS[table]
        query = select(model).where(model.id == row_id)
        if lock:
            query = query.with_for_update()
        return _row(db.session.scalar(query.execution_options(populate_existing=True)))

    def insert(self, table, values):
        db.session.add(MODELS[table](**_values(values)))
        db.session.flush()

    def update(self, table, row_id, values):
        model = db.session.get(MODELS[table], row_id)
        for key, value in _values(values).items():
            setattr(model, key, value)
        db.session.flush()

    @staticmethod
    def _insert(model):
        dialect = db.session.get_bind().dialect.name
        return pg_insert(model) if dialect == 'postgresql' else sqlite_insert(model)

    def insert_occurrence(self, values):
        db.session.execute(self._insert(Bill).values(**_values(values)).on_conflict_do_nothing(
            index_elements=['series_id', 'occurrence_month']))

    def claim_reminder(self, values):
        result = db.session.execute(self._insert(BillReminderLog).values(**_values(values))
                                    .on_conflict_do_nothing(index_elements=['id']))
        return result.rowcount == 1
