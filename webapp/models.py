from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import CheckConstraint, Index, func


db = SQLAlchemy()


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    __table_args__ = (
        CheckConstraint("role IN ('admin','manager','reader')", name='ck_users_role'),
        CheckConstraint('session_version >= 1', name='ck_users_session_version'),
        CheckConstraint("role <> 'reader' OR NOT vault_access", name='ck_users_reader_vault'),
    )
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(254), nullable=False, unique=True)
    password_hash = db.Column(db.Text, nullable=False)
    role = db.Column(db.String(20), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    session_version = db.Column(db.Integer, nullable=False, default=1)
    agenda_access = db.Column(db.Boolean, nullable=False, default=False)
    finance_access = db.Column(db.Boolean, nullable=False, default=False)
    vault_access = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    last_login = db.Column(db.DateTime, nullable=True)

    def get_id(self):
        return f'{self.id}:{self.session_version}'

    @property
    def is_active(self):
        return bool(self.active)

    @property
    def is_admin(self):
        return bool(self.active and self.role == 'admin')

    @property
    def can_view_agenda(self):
        return bool(self.active and (self.role == 'admin' or self.agenda_access))

    @property
    def can_view_finance(self):
        return bool(self.active and (self.role == 'admin' or self.finance_access))

    @property
    def can_access_vault(self):
        return bool(self.active and self.role != 'reader' and self.vault_access)


Index('uq_users_email_lower', func.lower(User.email), unique=True)


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    actor_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=True)
    target_user_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)


class SecurityState(db.Model):
    __tablename__ = 'security_state'
    __table_args__ = (CheckConstraint('id = 1', name='ck_security_state_singleton'),)
    id = db.Column(db.Integer, primary_key=True)
    bootstrap_complete = db.Column(db.Boolean, nullable=False, default=False)
    # An actual update also serializes mutations on SQLite (FOR UPDATE has no effect there).
    lock_version = db.Column(db.Integer, nullable=False, default=0)


class Event(db.Model):
    __tablename__ = 'events'
    __table_args__ = (
        CheckConstraint('completed IN (0, 1)', name='ck_events_completed'),
        Index('ix_events_event_date_event_time', 'event_date', 'event_time'),
    )

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    event_date = db.Column(db.Date, nullable=False)
    event_time = db.Column(db.Time, nullable=False)
    category = db.Column(db.String(80), nullable=False, default='Compromisso')
    location = db.Column(db.String(200), nullable=False, default='')
    notes = db.Column(db.Text, nullable=False, default='')
    reminder_minutes = db.Column(db.Integer, nullable=False, default=15)
    completed = db.Column(db.Integer, nullable=False, default=0)
    description = db.Column(db.Text, nullable=False, default='')
    end_time = db.Column(db.Time, nullable=True)
    priority = db.Column(db.String(20), nullable=False, default='Normal')
    owner_id = db.Column(db.String(36), db.ForeignKey('users.id', name='fk_events_owner_id_users'), nullable=True)


class Task(db.Model):
    __tablename__ = 'tasks'
    __table_args__ = (
        CheckConstraint('completed IN (0, 1)', name='ck_tasks_completed'),
        Index('ix_tasks_completed_due_date', 'completed', 'due_date'),
    )

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False, default='')
    due_date = db.Column(db.Date, nullable=True)
    priority = db.Column(db.String(20), nullable=False, default='Normal')
    completed = db.Column(db.Integer, nullable=False, default=0)
    owner_id = db.Column(db.String(36), db.ForeignKey('users.id', name='fk_tasks_owner_id_users'), nullable=True)


class LoginAttempt(db.Model):
    __tablename__ = 'login_attempts'

    client_key = db.Column(db.String(64), primary_key=True)
    failures = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(db.BigInteger, nullable=False)
    locked_until = db.Column(db.BigInteger, nullable=False, default=0)


class BillSeries(db.Model):
    __tablename__ = 'bill_series'
    __table_args__ = (
        CheckConstraint('amount_cents >= 0', name='ck_bill_series_amount'),
        CheckConstraint('anchor_day BETWEEN 1 AND 31', name='ck_bill_series_day'),
        CheckConstraint('active IN (0,1)', name='ck_bill_series_active'),
    )
    id = db.Column(db.String(36), primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    amount_cents = db.Column(db.BigInteger, nullable=False)
    responsible = db.Column(db.String(200), nullable=False)
    notes = db.Column(db.Text, nullable=False)
    anchor_day = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    active = db.Column(db.Integer, nullable=False)


class Bill(db.Model):
    __tablename__ = 'bills'
    __table_args__ = (
        CheckConstraint('amount_cents >= 0', name='ck_bills_amount'),
        CheckConstraint('anchor_day BETWEEN 1 AND 31', name='ck_bills_day'),
        CheckConstraint('deleted IN (0,1)', name='ck_bills_deleted'),
        CheckConstraint("recurrence IN ('Única','Mensal')", name='ck_bills_recurrence'),
        db.UniqueConstraint('series_id', 'occurrence_month', name='uq_bills_series_month'),
        Index('ix_bills_due_date', 'due_date'),
    )
    id = db.Column(db.String(36), primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    amount_cents = db.Column(db.BigInteger, nullable=False)
    responsible = db.Column(db.String(200), nullable=False)
    notes = db.Column(db.Text, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    recurrence = db.Column(db.String(10), nullable=False)
    series_id = db.Column(db.String(36), db.ForeignKey('bill_series.id'), nullable=True)
    occurrence_month = db.Column(db.String(7), nullable=True)
    anchor_day = db.Column(db.Integer, nullable=False)
    paid_on = db.Column(db.Date, nullable=True)
    receipt_id = db.Column(db.String(128), nullable=False, default='')
    deleted = db.Column(db.Integer, nullable=False, default=0)


class BillPayment(db.Model):
    __tablename__ = 'bill_payments'
    __table_args__ = (CheckConstraint('amount_cents >= 0', name='ck_bill_payments_amount'),)
    id = db.Column(db.String(36), primary_key=True)
    bill_id = db.Column(db.String(36), db.ForeignKey('bills.id'), nullable=False, unique=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    amount_cents = db.Column(db.BigInteger, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    responsible = db.Column(db.String(200), nullable=False)
    paid_on = db.Column(db.Date, nullable=False)


class BillReminderLog(db.Model):
    __tablename__ = 'bill_reminder_log'
    __table_args__ = (CheckConstraint('days_before IN (7,3,1)', name='ck_bill_reminder_days'),)
    id = db.Column(db.String(64), primary_key=True)
    bill_id = db.Column(db.String(36), db.ForeignKey('bills.id'), nullable=False)
    days_before = db.Column(db.Integer, nullable=False)
    reminder_date = db.Column(db.Date, nullable=False)
    notified_at = db.Column(db.String(32), nullable=False)
