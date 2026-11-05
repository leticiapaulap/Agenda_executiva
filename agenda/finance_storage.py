"""Armazenamento financeiro SQLite local, usando as transações da agenda."""
from contextlib import contextmanager


TABLES = {'series': 'bill_series', 'bills': 'bills', 'payments': 'bill_payments'}


def initialize_finance(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS bill_series (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
        responsible TEXT NOT NULL, notes TEXT NOT NULL,
        anchor_day INTEGER NOT NULL CHECK(anchor_day BETWEEN 1 AND 31),
        start_date TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1))
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS bills (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
        responsible TEXT NOT NULL, notes TEXT NOT NULL, due_date TEXT NOT NULL,
        recurrence TEXT NOT NULL CHECK(recurrence IN ('Única','Mensal')),
        series_id TEXT REFERENCES bill_series(id), occurrence_month TEXT,
        anchor_day INTEGER NOT NULL CHECK(anchor_day BETWEEN 1 AND 31),
        paid_on TEXT NOT NULL DEFAULT '', receipt_id TEXT NOT NULL DEFAULT '',
        deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
        UNIQUE(series_id,occurrence_month)
    )''')
    connection.execute('CREATE INDEX IF NOT EXISTS ix_bills_due_date ON bills(due_date)')
    connection.execute('''CREATE TABLE IF NOT EXISTS bill_payments (
        id TEXT PRIMARY KEY, bill_id TEXT NOT NULL UNIQUE REFERENCES bills(id),
        title TEXT NOT NULL, category TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
        due_date TEXT NOT NULL, responsible TEXT NOT NULL, paid_on TEXT NOT NULL
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS bill_reminder_log (
        id TEXT PRIMARY KEY, bill_id TEXT NOT NULL REFERENCES bills(id),
        days_before INTEGER NOT NULL CHECK(days_before IN (7,3,1)),
        reminder_date TEXT NOT NULL, notified_at TEXT NOT NULL
    )''')


class SQLiteFinanceRepository:
    def __init__(self, database):
        self.database = database
        self.connection = None

    @contextmanager
    def transaction(self):
        with self.database._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self.connection = connection
            try:
                yield self
            finally:
                self.connection = None

    def all(self, table):
        return [dict(row) for row in self.connection.execute('SELECT * FROM ' + TABLES[table])]

    def get(self, table, row_id, lock=False):
        row = self.connection.execute('SELECT * FROM ' + TABLES[table] + ' WHERE id=?',
                                      (row_id,)).fetchone()
        return dict(row) if row else None

    def insert(self, table, values):
        keys = list(values)
        self.connection.execute('INSERT INTO ' + TABLES[table] + ' (' + ','.join(keys)
                                + ') VALUES (' + ','.join('?' for _ in keys) + ')',
                                [values[key] for key in keys])

    def update(self, table, row_id, values):
        self.connection.execute('UPDATE ' + TABLES[table] + ' SET '
                                + ','.join(key + '=?' for key in values) + ' WHERE id=?',
                                [*values.values(), row_id])

    def insert_occurrence(self, values):
        keys = list(values)
        self.connection.execute('INSERT INTO bills (' + ','.join(keys) + ') VALUES ('
                                + ','.join('?' for _ in keys)
                                + ') ON CONFLICT(series_id,occurrence_month) DO NOTHING',
                                [values[key] for key in keys])

    def claim_reminder(self, values):
        keys = list(values)
        cursor = self.connection.execute('INSERT INTO bill_reminder_log (' + ','.join(keys)
                                        + ') VALUES (' + ','.join('?' for _ in keys)
                                        + ') ON CONFLICT(id) DO NOTHING',
                                        [values[key] for key in keys])
        return cursor.rowcount == 1
