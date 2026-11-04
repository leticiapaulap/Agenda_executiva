"""Backups incompatíveis devem ser recusados antes de tocar nos dados atuais."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agenda.storage import Database


class StorageIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-integrity-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'agenda.db'
        self.db = Database(self.path)
        self.db.initialize()
        self.event_id = self.db.save_event({
            'title': 'Registro atual a preservar', 'event_date': '2026-11-04',
            'event_time': '10:00', 'end_time': '11:00',
        })

    def modified_backup(self, statements):
        backup = self.folder / 'fonte.db'
        self.db.backup(backup)
        connection = sqlite3.connect(backup)
        try:
            connection.executescript(statements)
            connection.commit()
        finally:
            connection.close()
        return backup

    def assert_rejected_without_changes(self, source):
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.db.restore(source)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual('Registro atual a preservar', self.db.get_event(self.event_id)['title'])
        source_before = source.read_bytes()
        with self.assertRaises(ValueError):
            Database(source).initialize()
        self.assertEqual(source_before, source.read_bytes())

    def test_incomplete_modern_tables_are_rejected(self):
        schemas = {
            'tasks': 'CREATE TABLE tasks(id INTEGER PRIMARY KEY,due_date TEXT,completed INTEGER);',
            'settings': 'CREATE TABLE settings(key TEXT PRIMARY KEY);',
            'reminder_log': 'CREATE TABLE reminder_log(event_id INTEGER,event_date TEXT);',
        }
        for table, create in schemas.items():
            with self.subTest(table=table):
                source = self.modified_backup(f'DROP TABLE {table}; {create}')
                self.assert_rejected_without_changes(source)

    def test_tasks_require_integer_rowid_primary_key(self):
        source = self.modified_backup('''DROP TABLE tasks;
            CREATE TABLE tasks(id TEXT PRIMARY KEY,title TEXT,description TEXT,
                              due_date TEXT,priority TEXT,completed INTEGER);''')
        self.assert_rejected_without_changes(source)

    def test_settings_require_unique_key(self):
        source = self.modified_backup('''DROP TABLE settings;
            CREATE TABLE settings(key TEXT,value TEXT);''')
        self.assert_rejected_without_changes(source)

    def test_reminders_require_composite_primary_key(self):
        source = self.modified_backup('''DROP TABLE reminder_log;
            CREATE TABLE reminder_log(event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
                event_date TEXT,event_time TEXT,reminder_minutes INTEGER,notified_at TEXT);''')
        self.assert_rejected_without_changes(source)

    def test_reminders_require_event_foreign_key(self):
        source = self.modified_backup('''DROP TABLE reminder_log;
            CREATE TABLE reminder_log(event_id INTEGER,event_date TEXT,event_time TEXT,
                reminder_minutes INTEGER,notified_at TEXT,
                PRIMARY KEY(event_id,event_date,event_time,reminder_minutes));''')
        self.assert_rejected_without_changes(source)

    def test_orphan_reminder_is_rejected_before_restore(self):
        source = self.modified_backup('''INSERT INTO reminder_log VALUES
            (999999,'2026-11-04','10:00',15,'2026-11-04T09:45:00');''')
        self.assert_rejected_without_changes(source)

    def test_view_cannot_impersonate_modern_table(self):
        source = self.modified_backup('''DROP TABLE tasks;
            CREATE VIEW tasks AS SELECT id,title,description,event_date AS due_date,priority,completed FROM events;''')
        self.assert_rejected_without_changes(source)

    def test_unrelated_extension_tables_are_preserved(self):
        source = self.modified_backup('''CREATE TABLE custom_metadata(name TEXT);
            INSERT INTO custom_metadata VALUES ('Extensão preservada');''')
        self.db.restore(source)
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual([('Extensão preservada',)], connection.execute('SELECT * FROM custom_metadata').fetchall())
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()
