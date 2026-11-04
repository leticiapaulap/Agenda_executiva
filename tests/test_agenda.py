"""Cobertura dos dados existentes, regras de negócio e recuperação segura."""
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agenda.services import AgendaService, long_date, parse_date, parse_time
from agenda.storage import Database


LEGACY_SCHEMA = """
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_time TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Compromisso',
    location TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    reminder_minutes INTEGER NOT NULL DEFAULT 15,
    completed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_events_date ON events(event_date, event_time);
"""


def legacy_database(path):
    """Reproduz exatamente o esquema do aplicativo anterior, sem dados reais."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(LEGACY_SCHEMA)
        conn.executemany(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (7, 'Reunião da diretoria', '2026-11-04', '09:00', 'Reunião',
                 'Sala São Paulo', 'Pauta: orçamento — manter acentuação', 1440, 0),
                (19, 'Relatório concluído', '2026-11-03', '16:00', 'Tarefa',
                 '', 'Registro histórico', -1, 1),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def event_data(**changes):
    values = {
        'title': 'Conselho executivo', 'event_date': '04/11/2026',
        'event_time': '09:00', 'end_time': '10:00', 'category': 'Reunião',
        'location': 'Sala principal', 'description': 'Análise dos indicadores',
        'notes': 'Levar apresentação', 'priority': 'Alta',
        'reminder_minutes': 15, 'completed': 0,
    }
    values.update(changes)
    return values


class TemporaryDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / 'agenda.db'
        self.db = Database(self.path)
        self.db.initialize()
        self.service = AgendaService(self.db)

    def new_event(self, **changes):
        return self.service.save_event(event_data(**changes))

    def ids(self, rows):
        return {row['id'] for row in rows}


class MigrationTests(unittest.TestCase):
    def test_legacy_migration_preserves_all_original_columns_and_ids(self):
        with tempfile.TemporaryDirectory(prefix='agenda-migration-') as folder:
            path = Path(folder) / 'agenda.db'
            legacy_database(path)
            conn = sqlite3.connect(path)
            before = conn.execute('SELECT * FROM events ORDER BY id').fetchall()
            conn.close()
            db = Database(path)
            db.initialize()
            rows = sorted(db.list_events(), key=lambda row: row['id'])
            columns = ['id', 'title', 'event_date', 'event_time', 'category',
                       'location', 'notes', 'reminder_minutes', 'completed']
            self.assertEqual(before, [tuple(row[name] for name in columns) for row in rows])
            self.assertEqual('Normal', rows[0]['priority'])
            self.assertEqual('', rows[0]['description'])
            self.assertEqual('', rows[0]['end_time'])
            self.assertEqual('Tarefa', db.get_event(19)['category'])
            snapshots = [p for p in Path(folder).rglob('*') if p.is_file() and p != path]
            self.assertTrue(snapshots, 'A migração deve criar backup antes de alterar o esquema.')
            snapshot = sqlite3.connect(snapshots[0])
            try:
                backup_columns = [row[1] for row in snapshot.execute('PRAGMA table_info(events)')]
                self.assertEqual(columns, backup_columns, 'O backup deve preceder a migração.')
                self.assertEqual(before, snapshot.execute('SELECT * FROM events ORDER BY id').fetchall())
            finally:
                snapshot.close()
            db.initialize()
            self.assertEqual(rows, sorted(db.list_events(), key=lambda row: row['id']),
                             'Reabrir não pode duplicar nem modificar registros.')
            self.assertEqual(2, len(Database(path).list_events()))

    def test_future_schema_is_rejected_without_downgrade_or_record_changes(self):
        with tempfile.TemporaryDirectory(prefix='agenda-future-') as folder:
            path = Path(folder) / 'agenda.db'
            legacy_database(path)
            conn = sqlite3.connect(path)
            conn.execute('PRAGMA user_version=999')
            conn.commit()
            conn.close()
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                Database(path).initialize()
            self.assertEqual(before, path.read_bytes())


class EventTests(TemporaryDatabaseTest):
    def test_crud_completion_and_reopen_persist(self):
        event_id = self.new_event()
        row = Database(self.path).get_event(event_id)
        self.assertEqual('2026-11-04', row['event_date'])
        self.assertEqual('Análise dos indicadores', row['description'])
        self.assertEqual('10:00', row['end_time'])
        self.service.save_event(event_data(title='Revisão do conselho', notes='Alterado'), event_id)
        self.db.toggle_event(event_id)
        reopened = Database(self.path)
        self.assertEqual('Revisão do conselho', reopened.get_event(event_id)['title'])
        self.assertEqual(1, reopened.get_event(event_id)['completed'])
        reopened.toggle_event(event_id)
        self.assertEqual(0, self.db.get_event(event_id)['completed'])
        reopened.delete_event(event_id)
        self.assertIsNone(self.db.get_event(event_id))

    def test_delete_uses_unique_id_even_when_titles_match(self):
        first_id = self.new_event(title='Compromisso repetido')
        second_id = self.new_event(title='Compromisso repetido')

        self.db.delete_event(first_id)

        self.assertIsNone(self.db.get_event(first_id))
        self.assertEqual('Compromisso repetido', self.db.get_event(second_id)['title'])
        with self.assertRaises(ValueError):
            self.db.delete_event(first_id)

    def test_edit_and_delete_with_repeated_titles_preserve_other_record(self):
        first_id = self.new_event(title='Título repetido')
        second_id = self.new_event(title='Título repetido', event_time='11:00', end_time='12:00')

        self.service.save_event(event_data(title='Primeiro compromisso editado'), first_id)

        self.assertEqual('Primeiro compromisso editado', self.db.get_event(first_id)['title'])
        self.assertEqual('Título repetido', self.db.get_event(second_id)['title'])
        reopened = Database(self.path)
        self.assertEqual('Primeiro compromisso editado', reopened.get_event(first_id)['title'])
        self.assertEqual('Título repetido', reopened.get_event(second_id)['title'])

        reopened.delete_event(second_id)

        persisted = Database(self.path)
        self.assertIsNone(persisted.get_event(second_id))
        self.assertEqual('Primeiro compromisso editado', persisted.get_event(first_id)['title'])

    def test_combined_filters_search_and_inclusive_period(self):
        selected = self.new_event(title='Planejamento São Paulo', priority='Urgente')
        self.new_event(title='Outro assunto', event_date='05/11/2026', category='Pessoal')
        done = self.new_event(title='Planejamento concluído', event_date='06/11/2026')
        self.db.toggle_event(done)
        self.assertEqual({selected}, self.ids(self.db.list_events(
            search='Planejamento', start='2026-11-04', end='2026-11-04',
            status='Pendentes', category='Reunião', priority='Urgente')))
        self.assertEqual({done}, self.ids(self.db.list_events(status='Concluídos')))
        self.assertEqual(2, len(self.db.list_events(start='2026-11-05', end='2026-11-06')))

    def test_search_does_not_execute_sql(self):
        original = self.new_event(title="Reunião d'água")
        self.assertEqual({original}, self.ids(self.db.list_events(search="d'água")))
        self.assertEqual([], self.db.list_events(search="'; DROP TABLE events;--"))
        self.assertIsNotNone(self.db.get_event(original))

    def test_invalid_event_never_writes(self):
        invalid = [
            {'title': '   '}, {'event_date': '31/02/2026'},
            {'event_time': '25:00'}, {'event_time': '09:75'},
            {'end_time': '08:59'}, {'end_time': '09:00'},
            {'priority': 'Muito alta'}, {'reminder_minutes': 3},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.new_event(**changes)
        self.assertEqual([], self.db.list_events())

    def test_conflicts_ignore_adjacent_completed_and_the_edited_event(self):
        original = self.new_event()
        self.assertEqual({original}, self.ids(self.service.conflicts(event_data(
            event_time='09:30', end_time='10:30'))))
        self.assertEqual([], self.service.conflicts(event_data(event_time='10:00', end_time='11:00')))
        self.assertEqual([], self.service.conflicts(event_data(event_time='08:00', end_time='09:00')))
        self.assertEqual([], self.service.conflicts(event_data(), exclude_id=original))
        self.assertEqual([], self.service.conflicts(event_data(event_date='05/11/2026')))
        overlapping = self.new_event(event_time='09:30', end_time='10:30')
        self.assertIsNotNone(self.db.get_event(overlapping), 'Conflito deve avisar, sem bloquear cadastro.')
        self.db.toggle_event(original)
        self.db.toggle_event(overlapping)
        self.assertEqual([], self.service.conflicts(event_data()))


class TaskAndSettingsTests(TemporaryDatabaseTest):
    def test_task_lifecycle_filters_and_persistence(self):
        task_id = self.service.save_task({
            'title': 'Preparar relatório', 'description': 'Consolidar metas',
            'due_date': '07/11/2026', 'priority': 'Urgente',
        })
        self.assertEqual('2026-11-07', self.db.get_task(task_id)['due_date'])
        self.assertEqual({task_id}, self.ids(self.db.list_tasks(search='relatório', priority='Urgente')))
        self.db.toggle_task(task_id)
        self.assertEqual([], self.db.list_tasks(status='Pendentes'))
        self.assertEqual({task_id}, self.ids(Database(self.path).list_tasks(status='Concluídas')))
        self.db.toggle_task(task_id)
        self.service.save_task({'title': 'Relatório revisado', 'due_date': '', 'priority': 'Normal'}, task_id)
        self.assertEqual('', self.db.get_task(task_id)['due_date'])
        self.db.delete_task(task_id)
        self.assertEqual([], self.db.list_tasks(status='Todas'))

    def test_invalid_tasks_are_not_persisted(self):
        for values in ({'title': ''}, {'title': 'Prazo inválido', 'due_date': '31/02/2026'},
                       {'title': 'Prioridade inválida', 'priority': 'Máxima'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.service.save_task(values)
        self.assertEqual([], self.db.list_tasks(status='Todas'))

    def test_settings_survive_restart(self):
        self.db.save_settings({'manager_name': 'Letícia Pereira', 'reminders_enabled': '0',
                               'default_reminder': '10', 'appearance': 'Claro', 'scaling': '110'})
        result = Database(self.path).get_settings()
        self.assertEqual('Letícia Pereira', result['manager_name'])
        self.assertEqual('0', result['reminders_enabled'])
        self.assertEqual('10', result['default_reminder'])
        self.assertEqual('110', result['scaling'])


class BackupRestoreTests(TemporaryDatabaseTest):
    def test_online_backup_includes_committed_wal_records(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.execute('PRAGMA journal_mode=WAL').fetchone()
            connection.execute('PRAGMA wal_autocheckpoint=0')
            connection.execute('SELECT COUNT(*) FROM events').fetchone()
            original = self.new_event(title='Alteração ainda no WAL')
            self.assertTrue(Path(str(self.path) + '-wal').exists())
            snapshot = self.directory / 'Cópia com espaços.db'
            self.db.backup(snapshot)
            self.assertEqual({original}, self.ids(Database(snapshot).list_events()))
            self.assertEqual('ok', self._integrity_check(snapshot))
        finally:
            connection.close()

    def test_backup_restore_roundtrip_preserves_previous_database_in_snapshot(self):
        original = self.new_event(title='Antes do backup')
        self.db.save_settings({'manager_name': 'Gestor original'})
        snapshot = self.directory / 'manual.db'
        self.db.backup(snapshot)
        self.new_event(title='Depois do backup', event_time='14:00', end_time='15:00')
        previous = Path(self.db.restore(snapshot))
        self.assertTrue(previous.is_file(), 'Restauração deve guardar backup da versão substituída.')
        self.assertEqual(2, len(Database(previous).list_events()))
        self.assertEqual({original}, self.ids(self.db.list_events()))
        self.assertEqual('Gestor original', self.db.get_settings()['manager_name'])
        self.assertEqual('ok', self._integrity_check(self.path))

    def _integrity_check(self, path):
        conn = sqlite3.connect(path)
        try:
            return conn.execute('PRAGMA integrity_check').fetchone()[0]
        finally:
            conn.close()

    def test_invalid_restore_cannot_modify_current_records(self):
        original = self.new_event()
        corrupt = self.directory / 'corrompido.db'
        corrupt.write_bytes(b'Este arquivo nao e SQLite.')
        unrelated = self.directory / 'outro.db'
        conn = sqlite3.connect(unrelated)
        conn.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)')
        conn.close()
        for source in (corrupt, unrelated, self.directory / 'inexistente.db'):
            with self.subTest(source=source):
                with self.assertRaises((ValueError, sqlite3.Error, OSError)):
                    self.db.restore(source)
                self.assertEqual({original}, self.ids(self.db.list_events()))
                self.assertEqual('ok', self._integrity_check(self.path))

    def test_restore_accepts_legacy_backup_and_migrates_without_loss(self):
        old_backup = self.directory / 'versao-antiga.db'
        legacy_database(old_backup)
        self.db.restore(old_backup)
        self.assertEqual({7, 19}, self.ids(self.db.list_events()))
        self.assertEqual('Registro histórico', self.db.get_event(19)['notes'])
        self.assertEqual('', self.db.get_event(7)['end_time'])

    def test_backup_cannot_overwrite_active_database(self):
        original = self.new_event()
        with self.assertRaises((ValueError, OSError)):
            self.db.backup(self.path)
        self.assertEqual({original}, self.ids(self.db.list_events()))


class ReminderTests(TemporaryDatabaseTest):
    def test_notification_claim_persists_after_restart_and_schedule_edit(self):
        event_id = self.new_event()
        before = datetime(2026, 11, 4, 8, 44, 59)
        due = before + timedelta(seconds=1)
        self.assertEqual([], self.service.due_reminders(before))
        event = self.service.due_reminders(due)[0]
        self.assertEqual(event_id, event['id'])
        self.assertTrue(self.service.claim_reminder(event, due))
        self.assertFalse(self.service.claim_reminder(event, due))
        reopened = AgendaService(Database(self.path))
        self.assertEqual([], reopened.due_reminders(due + timedelta(seconds=30)))
        self.service.save_event(event_data(event_time='10:00', end_time='11:00'), event_id)
        later = datetime(2026, 11, 4, 9, 45)
        self.assertEqual({event_id}, self.ids(reopened.due_reminders(later)))
        self.assertTrue(reopened.claim_reminder(reopened.due_reminders(later)[0], later))

    def test_disabled_completed_and_past_events_do_not_notify(self):
        self.new_event(reminder_minutes=-1)
        completed = self.new_event(event_time='09:10', end_time='09:40')
        self.db.toggle_event(completed)
        self.new_event(event_date='03/11/2026')
        self.assertEqual([], self.service.due_reminders(datetime(2026, 11, 4, 8, 55)))
        self.new_event(event_time='10:00', end_time='11:00')
        self.db.save_settings({'reminders_enabled': '0'})
        self.assertEqual([], self.service.due_reminders(datetime(2026, 11, 4, 9, 45)))


class DateFormattingTests(unittest.TestCase):
    def test_portuguese_display_and_leap_dates(self):
        self.assertEqual(date(2028, 2, 29), parse_date('29/02/2028'))
        self.assertEqual(date(2026, 11, 4), parse_date('2026-11-04'))
        self.assertEqual(23, parse_time('23:59').hour)
        self.assertIn('novembro', long_date(date(2026, 11, 4)).lower())
        with self.assertRaises(ValueError):
            parse_date('29/02/2026')


if __name__ == '__main__':
    unittest.main()
