from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, Index


db = SQLAlchemy()


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


class LoginAttempt(db.Model):
    __tablename__ = 'login_attempts'

    client_key = db.Column(db.String(64), primary_key=True)
    failures = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(db.BigInteger, nullable=False)
    locked_until = db.Column(db.BigInteger, nullable=False, default=0)
