"""Offline desktop vault rotation. Requires an explicit existing SQLite path.

Usage: python -m agenda.vault_admin rotate --database <path> [--apply]
Keys are read only from the environment. Dry run is the default.
"""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

from .storage import Database
from .vault import VaultCipher, VaultError


def main(argv=None):
    parser = argparse.ArgumentParser(description='Validar ou rotacionar as senhas do cofre desktop.')
    commands = parser.add_subparsers(dest='command', required=True)
    rotate = commands.add_parser('rotate', help='Valida todas as senhas; use --apply para recriptografar.')
    rotate.add_argument('--database', required=True, help='Caminho explícito do SQLite já atualizado.')
    rotate.add_argument('--apply', action='store_true', help='Cria backup e aplica a rotação atomicamente.')
    args = parser.parse_args(argv)
    try:
        path = Path(args.database).expanduser().resolve()
        if not path.is_file():
            raise ValueError
        cipher = VaultCipher()
        if not cipher.available:
            raise VaultError
        database = Database(path)
        # mode=rw prevents silently creating a new database after a path typo or
        # concurrent file removal. Rotation never migrates a database implicitly.
        with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('BEGIN IMMEDIATE' if args.apply else 'BEGIN')
            try:
                Database._validate_connection(connection)
                columns = {row[1] for row in connection.execute('PRAGMA table_info(access_catalog)')}
                if not {'password_ciphertext', 'is_corporate'}.issubset(columns):
                    raise ValueError
                rows = list(connection.execute('''SELECT id,password_ciphertext,is_corporate
                    FROM access_catalog WHERE password_ciphertext IS NOT NULL ORDER BY id'''))
                if args.apply:
                    # Reserved write lock prevents other writers between this
                    # consistent backup and the complete re-encryption operation.
                    database.backup(database._backup_name('antes_rotacao_cofre'))
                for row in rows:
                    if row['is_corporate']:
                        raise VaultError
                    if args.apply:
                        replacement = cipher.rotate(row['password_ciphertext'], row['id'])
                        connection.execute('UPDATE access_catalog SET password_ciphertext=? WHERE id=?',
                                           (replacement, row['id']))
                    else:
                        cipher.decrypt(row['password_ciphertext'], row['id'])
                if args.apply:
                    connection.commit()
                else:
                    connection.rollback()
            except Exception:
                connection.rollback()
                raise
        if args.apply:
            print(f'Rotação concluída: {len(rows)} senha(s). Backup anterior preservado.')
        else:
            print(f'Validação concluída: {len(rows)} senha(s). Nenhuma alteração aplicada.')
        return 0
    except Exception:
        # Never print exceptions, paths, ciphertexts, configuration or key values.
        print('Não foi possível concluir. Confira o banco existente, as permissões e as chaves do cofre.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
