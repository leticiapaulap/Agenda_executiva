"""Access directory and explicitly configured password vault.

Normal reads expose only metadata. Secrets are decrypted only by the dedicated
operation whose callers must enforce reauthentication and authorization.
"""
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from .vault import VaultCipher, VaultError, ERROR_MESSAGE, MAX_PASSWORD_BYTES


CATALOG_NOTICE = ('Modo catálogo: o armazenamento de senhas está desativado. '
                  'Não inclua credenciais nas observações ou na URL. '
                  'Acessos corporativos exigem autorização e respeito às políticas da organização.')


def initialize_catalog(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS access_catalog (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        password_ciphertext TEXT,
        is_corporate INTEGER NOT NULL DEFAULT 1 CHECK(is_corporate IN (0,1))
        CHECK(is_corporate=0 OR password_ciphertext IS NULL))''')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(access_catalog)')}
    if 'password_ciphertext' not in columns:
        connection.execute('ALTER TABLE access_catalog ADD COLUMN password_ciphertext TEXT')
    if 'is_corporate' not in columns:
        connection.execute('ALTER TABLE access_catalog ADD COLUMN is_corporate INTEGER '
                           'NOT NULL DEFAULT 1 CHECK(is_corporate IN (0,1)) '
                           'CHECK(is_corporate=0 OR password_ciphertext IS NULL)')


def _flag(value):
    if value is True or value == 1 or value in ('true', '1'):
        return True
    if value is False or value == 0 or value in ('false', '0'):
        return False
    raise ValueError('Selecione uma classificação válida para o acesso.')


def _public(row):
    if row is None:
        return None
    result = {key: row[key] for key in ('id', 'name', 'url', 'category', 'notes', 'updated_at')}
    result['has_password'] = bool(row.get('has_password', False))
    result['is_corporate'] = bool(row.get('is_corporate', True))
    return result


class AccessCatalog:
    def __init__(self, repository, cipher=None):
        self.repository = repository
        self.cipher = cipher if cipher is not None else VaultCipher()

    def list_entries(self, search=''):
        needle = str(search).strip().casefold()
        return [_public(r) for r in self.repository.list_entries() if not needle or
                needle in (r['name'] + ' ' + r['category']).casefold()]

    def get_entry(self, entry_id):
        return _public(self.repository.get_entry(entry_id))

    def save_entry(self, data, entry_id=None):
        if set(data) - {'name', 'url', 'category', 'notes', 'password', 'is_corporate', 'clear_password'}:
            raise ValueError('O formulário contém campos não permitidos.')
        existing = self.get_entry(entry_id) if entry_id else None
        if entry_id and existing is None:
            raise ValueError('Acesso não encontrado.')
        values = {}
        for key, limit in [('name', 200), ('url', 1000), ('category', 80), ('notes', 2000)]:
            raw = str(data.get(key, '') or '')
            if key == 'url' and any(ord(c) < 32 for c in raw):
                raise ValueError('A URL contém caracteres inválidos.')
            value = raw.strip()
            if len(value) > limit:
                raise ValueError('Um campo ultrapassa o tamanho permitido.')
            values[key] = value
        if not values['name']:
            raise ValueError('Informe o nome do sistema ou serviço.')
        if values['url']:
            try:
                parsed = urlsplit(values['url'])
                valid = (parsed.scheme in ('http', 'https') and parsed.hostname and
                         not parsed.username and not parsed.password and
                         not parsed.query and not parsed.fragment and
                         not any(ord(c) < 33 for c in values['url']) and
                         '\\' not in values['url'])
            except ValueError:
                valid = False
            if not valid:
                raise ValueError('Use uma URL HTTP/HTTPS sem credenciais, parâmetros ou fragmentos.')
        values['updated_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        corporate = _flag(data.get('is_corporate', existing['is_corporate'] if existing else True))
        password = data.get('password', '')
        if password is None:
            password = ''
        try:
            invalid_password = not isinstance(password, str) or len(password.encode('utf-8')) > MAX_PASSWORD_BYTES
        except UnicodeError:
            invalid_password = True
        if invalid_password:
            raise ValueError('A nova senha ultrapassa o tamanho permitido.')
        clear = _flag(data.get('clear_password', False))
        if clear and password:
            raise ValueError('Selecione remover a senha ou informe uma nova senha.')
        if corporate and (password or (existing and existing['has_password'] and not clear)):
            raise ValueError('Acessos corporativos permitem somente catálogo, sem armazenar senhas.')
        values['is_corporate'] = int(corporate)
        record_id = entry_id or str(uuid4())
        if password:
            values['password_ciphertext'] = self.cipher.encrypt(password, record_id)
        elif clear:
            values['password_ciphertext'] = None
        # Empty password deliberately omits this field, preserving existing data
        # even if keys are temporarily unavailable or were lost.
        if not entry_id:
            values['id'] = record_id
        return self.repository.save_entry(values, entry_id)

    def delete_entry(self, entry_id):
        self.repository.delete_entry(entry_id)

    def reveal_password(self, entry_id):
        entry = self.get_entry(entry_id)
        if not entry or entry['is_corporate'] or not entry['has_password']:
            raise VaultError(ERROR_MESSAGE)
        return self.cipher.decrypt(self.repository.get_secret(entry_id), entry_id)

    def rotate_password(self, entry_id):
        entry = self.get_entry(entry_id)
        if not entry or entry['is_corporate'] or not entry['has_password']:
            raise VaultError(ERROR_MESSAGE)
        previous = self.repository.get_secret(entry_id)
        encrypted = self.cipher.rotate(previous, entry_id)
        if not self.repository.compare_and_swap_secret(entry_id, previous, encrypted):
            raise VaultError('O acesso foi atualizado durante a rotação. Tente novamente.')


class SQLiteCatalogRepository:
    def __init__(self, database):
        self.database = database

    def list_entries(self):
        with self.database._connect() as connection:
            return [dict(r) for r in connection.execute('''SELECT id,name,url,category,notes,updated_at,
                is_corporate, CASE WHEN password_ciphertext IS NOT NULL AND password_ciphertext<>''
                THEN 1 ELSE 0 END AS has_password FROM access_catalog ORDER BY name,id''')]

    def get_entry(self, entry_id):
        with self.database._connect() as connection:
            row = connection.execute('''SELECT id,name,url,category,notes,updated_at,is_corporate,
                CASE WHEN password_ciphertext IS NOT NULL AND password_ciphertext<>''
                THEN 1 ELSE 0 END AS has_password FROM access_catalog WHERE id=?''', (entry_id,)).fetchone()
            return dict(row) if row else None

    def get_secret(self, entry_id):
        with self.database._connect() as connection:
            row = connection.execute('SELECT password_ciphertext FROM access_catalog WHERE id=?',
                                     (entry_id,)).fetchone()
            return row[0] if row else None

    def save_entry(self, values, entry_id=None):
        allowed = {'id', 'name', 'url', 'category', 'notes', 'updated_at', 'is_corporate', 'password_ciphertext'}
        if not values or set(values) - allowed or ('id' in values and entry_id):
            raise ValueError('Dados de acesso inválidos.')
        with self.database._connect() as connection:
            if entry_id:
                cursor = connection.execute('UPDATE access_catalog SET ' + ','.join(key + '=?' for key in values)
                                            + ' WHERE id=?', (*values.values(), entry_id))
                if cursor.rowcount != 1:
                    raise ValueError('Acesso não encontrado.')
            else:
                entry_id = values['id']
                connection.execute('INSERT INTO access_catalog (' + ','.join(values)
                                   + ') VALUES (' + ','.join('?' for _ in values) + ')', tuple(values.values()))
        return entry_id

    def compare_and_swap_secret(self, entry_id, expected, replacement):
        with self.database._connect() as connection:
            cursor = connection.execute('''UPDATE access_catalog SET password_ciphertext=?
                WHERE id=? AND password_ciphertext=? AND is_corporate=0''',
                (replacement, entry_id, expected))
            return cursor.rowcount == 1

    def delete_entry(self, entry_id):
        with self.database._connect() as connection:
            if connection.execute('DELETE FROM access_catalog WHERE id=?', (entry_id,)).rowcount != 1:
                raise ValueError('Acesso não encontrado.')
