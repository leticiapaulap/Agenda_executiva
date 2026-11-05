"""Offline rotation uses only random test keys and disposable SQLite files."""
import base64
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch

from agenda.catalog import AccessCatalog, SQLiteCatalogRepository
from agenda.storage import Database
from agenda.vault import VaultCipher
from agenda.vault_admin import main


def key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')


class VaultAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-vault-rotation-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'temporary.db'
        self.database = Database(self.path)
        self.database.initialize()
        self.old_key, self.new_key = key(), key()
        self.old_config = {'VAULT_ENABLED': 'true', 'VAULT_KEYS': json.dumps({'old': self.old_key}),
                           'VAULT_ACTIVE_KEY_ID': 'old'}
        self.config = {'VAULT_ENABLED': 'true',
                       'VAULT_KEYS': json.dumps({'old': self.old_key, 'new': self.new_key}),
                       'VAULT_ACTIVE_KEY_ID': 'new'}
        self.repo = SQLiteCatalogRepository(self.database)
        self.service = AccessCatalog(self.repo, VaultCipher(self.old_config))
        self.secret = 'generated-fixture-only-Sensitive-471!'
        self.entry_id = self.service.save_entry({'name': 'Personal fixture', 'is_corporate': False,
                                                 'password': self.secret})

    def run_cli(self, *extra, path=None, config=None):
        output = StringIO()
        with patch.dict(os.environ, config or self.config), redirect_stdout(output), redirect_stderr(output):
            status = main(['rotate', '--database', str(path or self.path), *extra])
        text = output.getvalue()
        for forbidden in (self.secret, self.old_key, self.new_key):
            self.assertNotIn(forbidden, text)
        return status, text

    def test_dry_run_verifies_every_secret_without_modifying_or_backup(self):
        before = self.repo.get_secret(self.entry_id)
        status, text = self.run_cli()
        self.assertEqual(0, status)
        self.assertIn('Nenhuma alteração', text)
        self.assertEqual(before, self.repo.get_secret(self.entry_id))
        self.assertFalse((self.path.parent / 'backups').exists())

    def test_apply_rotates_atomically_preserves_metadata_and_keeps_backup(self):
        before = self.repo.get_secret(self.entry_id)
        metadata = self.service.get_entry(self.entry_id)
        status, _ = self.run_cli('--apply')
        self.assertEqual(0, status)
        after = self.repo.get_secret(self.entry_id)
        self.assertNotEqual(before, after)
        self.assertEqual(metadata, self.service.get_entry(self.entry_id))
        new_only = VaultCipher({'VAULT_ENABLED': 'true', 'VAULT_KEYS': json.dumps({'new': self.new_key}),
                                'VAULT_ACTIVE_KEY_ID': 'new'})
        self.assertEqual(self.secret, new_only.decrypt(after, self.entry_id))
        backups = list((self.path.parent / 'backups').glob('*antes_rotacao_cofre*.db'))
        self.assertEqual(1, len(backups))
        self.assertEqual(before, SQLiteCatalogRepository(Database(backups[0])).get_secret(self.entry_id))

    def test_later_crypto_failure_rolls_back_all_previous_rows(self):
        second = self.service.save_entry({'name': 'Second fixture', 'is_corporate': False,
                                          'password': self.secret})
        # Process the valid row first, forcing rollback after an update occurred.
        first, last = sorted([self.entry_id, second])
        valid_before = self.repo.get_secret(first)
        with self.database._connect() as connection:
            connection.execute('UPDATE access_catalog SET password_ciphertext=? WHERE id=?',
                               ('malformed-sensitive-fixture', last))
        status, text = self.run_cli('--apply')
        self.assertEqual(1, status)
        self.assertNotIn('malformed-sensitive-fixture', text)
        self.assertEqual(valid_before, self.repo.get_secret(first))
        self.assertEqual('malformed-sensitive-fixture', self.repo.get_secret(last))

    def test_missing_database_is_not_created_and_disabled_configuration_fails(self):
        missing = self.path.parent / 'missing.db'
        self.assertEqual(1, self.run_cli(path=missing)[0])
        self.assertFalse(missing.exists())
        before = self.repo.get_secret(self.entry_id)
        self.assertEqual(1, self.run_cli('--apply', config={'VAULT_ENABLED': 'false'})[0])
        self.assertEqual(before, self.repo.get_secret(self.entry_id))
        self.assertFalse((self.path.parent / 'backups').exists())


if __name__ == '__main__':
    unittest.main()
