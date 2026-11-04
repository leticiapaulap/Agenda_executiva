"""Repositório SQLite local. Não depende de widgets nem de diretório de execução.

As migrações são aditivas: IDs, conteúdo e categorias dos eventos existentes
permanecem intactos. Toda migração de um banco existente produz um backup.
"""
from contextlib import closing, contextmanager
from datetime import date, datetime
from pathlib import Path
import os
import logging
import sqlite3
import tempfile


APP_DIR = Path.home() / 'AgendaExecutiva'
DB_PATH = APP_DIR / 'agenda.db'
SCHEMA_VERSION = 2
EVENT_COLUMNS = (
    'title', 'event_date', 'event_time', 'category', 'location', 'notes',
    'reminder_minutes', 'completed', 'description', 'end_time', 'priority',
)
LEGACY_COLUMNS = {
    'id', 'title', 'event_date', 'event_time', 'category', 'location', 'notes',
    'reminder_minutes', 'completed',
}
DEFAULT_SETTINGS = {
    'manager_name': '', 'reminders_enabled': '1', 'default_reminder': '15',
    'appearance': 'Claro', 'scaling': '100',
}


class Database:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else DB_PATH
        self.path = self.path.expanduser().absolute()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate_connection(connection):
        result = [row[0] for row in connection.execute('PRAGMA integrity_check')]
        if result != ['ok']:
            raise ValueError('O banco de dados falhou na verificação de integridade.')
        objects = {row[0]: row[1] for row in connection.execute(
            "SELECT name,type FROM sqlite_master WHERE type IN ('table','view')")}
        if objects.get('events') != 'table':
            raise ValueError('O arquivo não é um banco de dados compatível com a Agenda Executiva.')
        if connection.execute('PRAGMA user_version').fetchone()[0] > SCHEMA_VERSION:
            raise ValueError('Este banco foi criado por uma versão mais recente da agenda.')
        # As tabelas modernas são opcionais em backups antigos, mas, se presentes,
        # precisam ter um esquema utilizável antes de qualquer migração/restauração.
        required = {
            'events': LEGACY_COLUMNS,
            'tasks': {'id', 'title', 'description', 'due_date', 'priority', 'completed'},
            'settings': {'key', 'value'},
            'reminder_log': {'event_id', 'event_date', 'event_time', 'reminder_minutes', 'notified_at'},
        }
        primary_keys = {
            'events': ['id'], 'tasks': ['id'], 'settings': ['key'],
            'reminder_log': ['event_id', 'event_date', 'event_time', 'reminder_minutes'],
        }
        for table, expected in required.items():
            if table not in objects:
                continue
            if objects[table] != 'table':
                raise ValueError(f'O esquema da tabela {table} não é compatível com a agenda.')
            definition = list(connection.execute(f'PRAGMA table_info({table})'))
            columns = {row[1] for row in definition}
            actual_key = [row[1] for row in sorted(definition, key=lambda row: row[5]) if row[5]]
            if not expected.issubset(columns) or actual_key != primary_keys[table]:
                raise ValueError(f'O esquema da tabela {table} não é compatível com a agenda.')
            if table in ('events', 'tasks'):
                id_type = next(row[2] for row in definition if row[1] == 'id')
                if id_type.upper() != 'INTEGER':
                    raise ValueError(f'A identificação da tabela {table} não é compatível com a agenda.')
        if 'reminder_log' in objects:
            references = list(connection.execute('PRAGMA foreign_key_list(reminder_log)'))
            valid_reference = any(row[2] == 'events' and row[3] == 'event_id'
                                  and row[4] == 'id' and row[6].upper() == 'CASCADE'
                                  for row in references)
            if not valid_reference:
                raise ValueError('O vínculo dos lembretes com os compromissos é inválido.')
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('O banco contém referências inválidas entre registros.')

    def _backup_name(self, reason):
        folder = self.path.parent / 'backups'
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f'agenda_{reason}_{datetime.now():%Y%m%d_%H%M%S_%f}.db'

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.is_file() and self.path.stat().st_size > 0
        needs_migration = True
        if existed:
            with self._connect() as connection:
                self._validate_connection(connection)
                columns = {row[1] for row in connection.execute('PRAGMA table_info(events)')}
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                needs_migration = (
                    connection.execute('PRAGMA user_version').fetchone()[0] < SCHEMA_VERSION
                    or not {'description', 'end_time', 'priority'}.issubset(columns)
                    or not {'tasks', 'settings', 'reminder_log'}.issubset(tables)
                )
            if needs_migration:
                self.backup(self._backup_name('antes_migracao'))
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('''CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, event_date TEXT NOT NULL, event_time TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'Compromisso',
                location TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                reminder_minutes INTEGER NOT NULL DEFAULT 15,
                completed INTEGER NOT NULL DEFAULT 0,
                description TEXT NOT NULL DEFAULT '', end_time TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'Normal'
            )''')
            columns = {row[1] for row in connection.execute('PRAGMA table_info(events)')}
            additions = {'description': "TEXT NOT NULL DEFAULT ''",
                         'end_time': "TEXT NOT NULL DEFAULT ''",
                         'priority': "TEXT NOT NULL DEFAULT 'Normal'"}
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(f'ALTER TABLE events ADD COLUMN {name} {definition}')
            connection.execute('CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date,event_time)')
            connection.execute('''CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', due_date TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'Normal', completed INTEGER NOT NULL DEFAULT 0
            )''')
            connection.execute('CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(completed,due_date)')
            connection.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            connection.execute('''CREATE TABLE IF NOT EXISTS reminder_log (
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                event_date TEXT NOT NULL, event_time TEXT NOT NULL,
                reminder_minutes INTEGER NOT NULL, notified_at TEXT NOT NULL,
                PRIMARY KEY(event_id,event_date,event_time,reminder_minutes)
            )''')
            connection.executemany('INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)', DEFAULT_SETTINGS.items())
            connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')

    @staticmethod
    def _date_value(value):
        return value.isoformat() if isinstance(value, date) else value

    def list_events(self, search='', start=None, end=None, status='Todos', category='Todas', priority='Todas'):
        sql, args = 'SELECT * FROM events WHERE 1=1', []
        if start:
            sql += ' AND event_date>=?'
            args.append(self._date_value(start))
        if end:
            sql += ' AND event_date<=?'
            args.append(self._date_value(end))
        if status in ('Pendentes', 'Pendente'):
            sql += ' AND completed=0'
        elif status in ('Concluídos', 'Concluídas', 'Concluído'):
            sql += ' AND completed=1'
        if category and category != 'Todas':
            sql += ' AND category=?'
            args.append(category)
        if priority and priority != 'Todas':
            sql += ' AND priority=?'
            args.append(priority)
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(sql + ' ORDER BY event_date,event_time,id', args)]
        # casefold oferece busca consistente em português, inclusive caracteres acentuados.
        needle = search.strip().casefold()
        if needle:
            rows = [row for row in rows if any(needle in str(row[field]).casefold()
                    for field in ('title', 'description', 'notes', 'location', 'category'))]
        return rows

    def get_event(self, event_id):
        with self._connect() as connection:
            row = connection.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
        return dict(row) if row is not None else None

    def save_event(self, data, id=None):
        operation = 'INSERT' if id is None else 'UPDATE'
        logging.info('SQLite %s event requested id=%s db=%s', operation, id, self.path)
        values = {'title': '', 'event_date': '', 'event_time': '', 'category': 'Compromisso',
                  'location': '', 'notes': '', 'reminder_minutes': 15, 'completed': 0,
                  'description': '', 'end_time': '', 'priority': 'Normal'}
        if id is not None:
            existing = self.get_event(id)
            if existing is None:
                raise ValueError('O compromisso não foi encontrado. Atualize a agenda.')
            values.update(existing)
        values.update({key: value for key, value in data.items() if key in EVENT_COLUMNS})
        with self._connect() as connection:
            if id is None:
                placeholders = ','.join('?' for _ in EVENT_COLUMNS)
                cursor = connection.execute(f'INSERT INTO events({",".join(EVENT_COLUMNS)}) VALUES ({placeholders})',
                                            [values[key] for key in EVENT_COLUMNS])
                logging.info('SQLite INSERT event succeeded id=%s db=%s', cursor.lastrowid, self.path)
                return cursor.lastrowid
            cursor = connection.execute(f'UPDATE events SET {",".join(key+"=?" for key in EVENT_COLUMNS)} WHERE id=?',
                                        [values[key] for key in EVENT_COLUMNS] + [id])
            if cursor.rowcount != 1:
                raise ValueError('O compromisso não foi encontrado. Atualize a agenda.')
            logging.info('SQLite UPDATE event succeeded id=%s rows=%s db=%s',
                         id, cursor.rowcount, self.path)
        return id

    def delete_event(self, event_id):
        logging.info('SQLite DELETE event requested id=%s db=%s', event_id, self.path)
        with self._connect() as connection:
            cursor = connection.execute('DELETE FROM events WHERE id=?', (event_id,))
            if cursor.rowcount != 1:
                raise ValueError('O compromisso não foi encontrado. Atualize a agenda.')
            logging.info('SQLite DELETE event succeeded id=%s rows=%s db=%s',
                         event_id, cursor.rowcount, self.path)

    def toggle_event(self, event_id):
        with self._connect() as connection:
            connection.execute('UPDATE events SET completed=CASE completed WHEN 0 THEN 1 ELSE 0 END WHERE id=?', (event_id,))

    def list_tasks(self, search='', status='Pendentes', priority='Todas'):
        sql, args = 'SELECT * FROM tasks WHERE 1=1', []
        if status in ('Pendentes', 'Pendente'):
            sql += ' AND completed=0'
        elif status in ('Concluídas', 'Concluídos', 'Concluída'):
            sql += ' AND completed=1'
        if priority and priority != 'Todas':
            sql += ' AND priority=?'
            args.append(priority)
        sql += " ORDER BY completed,CASE priority WHEN 'Urgente' THEN 0 WHEN 'Alta' THEN 1 WHEN 'Normal' THEN 2 ELSE 3 END,CASE WHEN due_date='' THEN 1 ELSE 0 END,due_date,id"
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(sql, args)]
        needle = search.strip().casefold()
        return [row for row in rows if not needle or needle in (row['title'] + ' ' + row['description']).casefold()]

    def get_task(self, task_id):
        with self._connect() as connection:
            row = connection.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        return dict(row) if row is not None else None

    def save_task(self, data, id=None):
        fields = ('title', 'description', 'due_date', 'priority', 'completed')
        values = {'title': '', 'description': '', 'due_date': '', 'priority': 'Normal', 'completed': 0}
        if id is not None:
            existing = self.get_task(id)
            if existing is None:
                raise ValueError('A tarefa não foi encontrada. Atualize a agenda.')
            values.update(existing)
        values.update({key: value for key, value in data.items() if key in fields})
        with self._connect() as connection:
            if id is None:
                cursor = connection.execute('INSERT INTO tasks(title,description,due_date,priority,completed) VALUES (?,?,?,?,?)',
                                            [values[key] for key in fields])
                return cursor.lastrowid
            cursor = connection.execute('UPDATE tasks SET title=?,description=?,due_date=?,priority=?,completed=? WHERE id=?',
                                        [values[key] for key in fields] + [id])
            if cursor.rowcount != 1:
                raise ValueError('A tarefa não foi encontrada. Atualize a agenda.')
        return id

    def toggle_task(self, task_id):
        with self._connect() as connection:
            connection.execute('UPDATE tasks SET completed=CASE completed WHEN 0 THEN 1 ELSE 0 END WHERE id=?', (task_id,))

    def delete_task(self, task_id):
        with self._connect() as connection:
            connection.execute('DELETE FROM tasks WHERE id=?', (task_id,))

    def get_settings(self):
        with self._connect() as connection:
            settings = dict(DEFAULT_SETTINGS)
            settings.update({row['key']: row['value'] for row in connection.execute('SELECT key,value FROM settings')})
        return settings

    def save_settings(self, data):
        with self._connect() as connection:
            connection.executemany('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                   [(str(key), str(value)) for key, value in data.items()])

    def reminder_claimed(self, event):
        with self._connect() as connection:
            row = connection.execute('''SELECT 1 FROM reminder_log
                WHERE event_id=? AND event_date=? AND event_time=? AND reminder_minutes=?''',
                (event['id'], event['event_date'], event['event_time'], event['reminder_minutes'])).fetchone()
        return row is not None

    def claim_reminder(self, event, now=None):
        with self._connect() as connection:
            cursor = connection.execute('''INSERT OR IGNORE INTO reminder_log
                (event_id,event_date,event_time,reminder_minutes,notified_at) VALUES (?,?,?,?,?)''',
                (event['id'], event['event_date'], event['event_time'], event['reminder_minutes'],
                 (now or datetime.now()).isoformat(timespec='seconds')))
        return cursor.rowcount == 1

    def backup(self, destination):
        destination = Path(destination).expanduser().absolute()
        if destination.resolve() == self.path.resolve():
            raise ValueError('Escolha outro arquivo para o backup; o banco em uso não pode ser substituído.')
        if not self.path.is_file():
            raise FileNotFoundError('O banco de dados da agenda não foi encontrado.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.agenda_backup_', suffix='.db', dir=destination.parent)
        os.close(descriptor)
        temporary = Path(temporary)
        try:
            # A API backup também inclui alterações confirmadas que estiverem no WAL.
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=10)) as source, \
                    closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
                target.commit()
                self._validate_connection(target)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def restore(self, source):
        source = Path(source).expanduser().absolute()
        if not source.is_file():
            raise FileNotFoundError('O arquivo de backup não foi encontrado.')
        if source.resolve() == self.path.resolve():
            raise ValueError('Selecione um backup diferente do banco em uso.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.agenda_restore_', suffix='.db', dir=self.path.parent)
        os.close(descriptor)
        temporary = Path(temporary)
        try:
            source_connection = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=10)
            try:
                self._validate_connection(source_connection)
                target = sqlite3.connect(temporary)
                try:
                    source_connection.backup(target)
                    target.commit()
                    self._validate_connection(target)
                finally:
                    target.close()
            finally:
                source_connection.close()
            # Validar/migrar a cópia inteira antes de tocar no banco corrente.
            staged = Database(temporary)
            staged.initialize()
            safety_backup = self.backup(self._backup_name('antes_restauracao'))
            # Nenhuma conexão é mantida pelo repositório entre operações.
            with self._connect() as connection:
                checkpoint = connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                if checkpoint[0] != 0:
                    raise sqlite3.OperationalError('Feche outras instâncias da agenda antes de restaurar o backup.')
                connection.execute('PRAGMA journal_mode=DELETE')
            os.replace(temporary, self.path)
            return safety_backup
        finally:
            temporary.unlink(missing_ok=True)
