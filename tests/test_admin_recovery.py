"""Operator recovery tests use only temporary databases and fictional accounts."""
from datetime import date, datetime, time, timedelta
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.engine import URL
from werkzeug.security import check_password_hash, generate_password_hash

from webapp import create_app
from webapp.catalog import AccessEntry
from webapp.models import (AdminRecoveryGrant, AuditLog, Bill, Event,
                           ManualReminder, SecurityState, Task, User, db, utc_now)
from webapp.recovery import BACKUP_TABLES, create_operator_backup, verify_backup
from scripts.admin_operator import create_operator_app


ADMIN_LOGIN = 'recovery-admin@example.test'
OLD_PASSWORD = 'Senha-anterior-ficticia-2026!'
NEW_PASSWORD = 'Senha-recuperada-ficticia-2026!'
BACKUP_DIGEST = 'a' * 64


class AdminRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agenda-recovery-test-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.backup_file = self.folder / 'verified.dump'
        self.backup_file.write_bytes(b'PGDMPtest-only-backup')
        self.token_file = self.folder / 'authorization.secret'
        self.config = {
            'TESTING': True, 'SECRET_KEY': 'recovery-tests-only',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (self.folder / 'recovery.db').as_posix(),
            'ADMIN_USERNAME': ADMIN_LOGIN,
            'ADMIN_PASSWORD_HASH': generate_password_hash(OLD_PASSWORD),
            'SESSION_COOKIE_SECURE': False, 'WTF_CSRF_SSL_STRICT': False,
        }
        self.app = create_app(self.config)
        self.addCleanup(self.dispose_app, self.app)
        result = self.app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if result.exception:
            raise result.exception
        self.client = self.app.test_client()
        self.assertEqual(302, self.login(self.client, OLD_PASSWORD).status_code)
        with self.app.app_context():
            admin = db.session.query(User).filter_by(email=ADMIN_LOGIN).one()
            self.admin_id = admin.id
            self.original_hash = admin.password_hash
            self.original_version = admin.session_version
            other = User(name='Gestor preservado', email='manager@example.test',
                         password_hash=generate_password_hash('Senha-gestor-ficticia-2026!'),
                         role='manager', active=True, agenda_access=True)
            db.session.add(other)
            db.session.flush()
            self.manager_id = other.id
            db.session.add_all([
                Event(title='Compromisso preservado', event_date=date(2026, 12, 10),
                      event_time=time(9), owner_id=admin.id),
                Task(title='Tarefa preservada', due_date=date(2026, 12, 11), owner_id=other.id),
                ManualReminder(owner_id=admin.id, title='Lembrete preservado',
                               due_at=datetime(2026, 12, 12, 15), notes='Observações privadas'),
                Bill(id='bill-test', title='Conta preservada', category='Teste', amount_cents=12345,
                     responsible='Fictício', notes='Não alterar', due_date=date(2026, 12, 10),
                     recurrence='Única', anchor_day=10),
                AccessEntry(id='vault-test', name='Cofre preservado', url='', category='Teste',
                            notes='Não alterar', updated_at='2026-11-06', is_corporate=0,
                            password_ciphertext='test-only-encrypted-value'),
                AuditLog(actor_id=admin.id, target_user_id=other.id, action='existing_history'),
            ])
            db.session.commit()

    @staticmethod
    def dispose_app(app):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()

    def csrf(self, client, path='/login'):
        response = client.get(path)
        self.assertEqual(200, response.status_code)
        return re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()

    def login(self, client, password, username=ADMIN_LOGIN):
        return client.post('/login', data={
            'csrf_token': self.csrf(client), 'username': username, 'password': password})

    def command(self, name, *, email=ADMIN_LOGIN, input_value=None, extra=(), app=None):
        args = [name, '--email', email]
        if name != 'auth-status':
            args.extend(['--backup-file', str(self.backup_file)])
        args.extend(extra)
        with patch('webapp.recovery.verify_backup', return_value=BACKUP_DIGEST):
            return (app or self.app).test_cli_runner().invoke(args=args, input=input_value)

    def authorize(self):
        result = self.command('admin-recovery-authorize', input_value=self.admin_id + '\n',
                              extra=['--token-file', str(self.token_file)])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertTrue(self.token_file.is_file())
        with self.app.app_context():
            grant = db.session.query(AdminRecoveryGrant).filter(
                AdminRecoveryGrant.token_digest.is_not(None)).one()
            grant_id = grant.id
            self.assertEqual(self.admin_id, grant.target_user_id)
            self.assertEqual(self.original_version, grant.expected_session_version)
            self.assertEqual(BACKUP_DIGEST, grant.backup_digest)
            self.assertLessEqual(grant.expires_at - grant.issued_at, timedelta(minutes=15))
        return grant_id

    def password_input(self, *, user_id=None, password=NEW_PASSWORD):
        return f'{user_id or self.admin_id}\n{password}\n{password}\n'

    def unchanged_account(self):
        with self.app.app_context():
            admin = db.session.get(User, self.admin_id)
            self.assertEqual(self.original_hash, admin.password_hash)
            self.assertEqual(self.original_version, admin.session_version)

    def data_snapshot(self):
        with self.app.app_context():
            snapshot = {}
            excluded = {'users', 'audit_logs', 'security_state', 'login_attempts',
                        'admin_recovery_grants'}
            for table in db.metadata.sorted_tables:
                if table.name not in excluded:
                    snapshot[table.name] = sorted(
                        (dict(row) for row in db.session.execute(select(table)).mappings()),
                        key=repr)
            # Only the selected hash and session version may change during recovery.
            snapshot['users'] = []
            for row in db.session.execute(select(User.__table__)).mappings():
                value = dict(row)
                if value['id'] == self.admin_id:
                    value.pop('password_hash')
                    value.pop('session_version')
                snapshot['users'].append(value)
            snapshot['users'].sort(key=lambda row: row['id'])
            snapshot['prior_history'] = [row.id for row in db.session.query(AuditLog).filter_by(
                action='existing_history').all()]
            return snapshot

    def test_interactive_reset_preserves_all_data_and_invalidates_existing_sessions(self):
        before = self.data_snapshot()
        with self.assertLogs(self.app.logger, level='INFO') as logs:
            result = self.command('reset-admin-password', email=' RECOVERY-ADMIN@EXAMPLE.TEST ',
                                  input_value=self.password_input())
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual(before, self.data_snapshot())
        self.assertNotIn(NEW_PASSWORD, result.output + '\n'.join(logs.output))
        self.assertNotIn(self.original_hash, result.output + '\n'.join(logs.output))
        self.assertEqual(302, self.client.get('/users').status_code)
        with self.app.app_context():
            admin = db.session.get(User, self.admin_id)
            self.assertTrue(check_password_hash(admin.password_hash, NEW_PASSWORD))
            self.assertEqual(self.original_version + 1, admin.session_version)
            audit = db.session.query(AuditLog).filter_by(action='admin_password_recovered').one()
            self.assertIsNone(audit.actor_id)
            self.assertEqual(self.admin_id, audit.target_user_id)
        old_client = self.app.test_client()
        self.assertEqual(200, self.login(old_client, OLD_PASSWORD).status_code)
        self.assertEqual(302, self.login(old_client, NEW_PASSWORD).status_code)

    def test_old_command_name_requires_same_explicit_confirmation_and_backup(self):
        missing = self.app.test_cli_runner().invoke(args=[
            'users-reset-admin-password', '--email', ADMIN_LOGIN], input=self.password_input())
        self.assertNotEqual(0, missing.exit_code)
        cancelled = self.command('users-reset-admin-password', input_value='wrong-account-id\n')
        self.assertNotEqual(0, cancelled.exit_code)
        self.unchanged_account()
        success = self.command('users-reset-admin-password', input_value=self.password_input())
        self.assertEqual(0, success.exit_code, success.output)

    def test_non_admin_inactive_and_unknown_accounts_are_never_created_or_modified(self):
        with self.app.app_context():
            db.session.add(User(name='Admin inativo', email='inactive@example.test',
                                password_hash=generate_password_hash(OLD_PASSWORD),
                                role='admin', active=False))
            db.session.commit()
            count = db.session.query(User).count()
        for email in ('manager@example.test', 'inactive@example.test', 'missing@example.test'):
            with self.subTest(email=email):
                result = self.command('reset-admin-password', email=email,
                                      input_value=self.password_input())
                self.assertNotEqual(0, result.exit_code)
                self.unchanged_account()
        with self.app.app_context():
            self.assertEqual(count, db.session.query(User).count())
            self.assertFalse(db.session.query(User).filter_by(email='inactive@example.test').one().active)

    def test_one_time_grant_survives_restart_and_is_consumed_with_secret_removed(self):
        grant_id = self.authorize()
        token_contents = self.token_file.read_text(encoding='utf-8')
        before = self.data_snapshot()
        restarted = create_app({**self.config, 'ADMIN_PASSWORD_HASH': generate_password_hash(
            'Deployment-value-must-not-reset-password!')})
        self.addCleanup(self.dispose_app, restarted)
        self.unchanged_account()
        with restarted.app_context():
            self.assertIsNone(db.session.get(AdminRecoveryGrant, grant_id).consumed_at)
        result = self.command('reset-admin-password-once', app=restarted,
                              input_value=self.password_input(),
                              extra=['--authorization-file', str(self.token_file)])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertFalse(self.token_file.exists())
        self.assertNotIn(token_contents.strip(), result.output)
        self.assertEqual(before, self.data_snapshot())
        with self.app.app_context():
            grant = db.session.get(AdminRecoveryGrant, grant_id)
            self.assertIsNotNone(grant.consumed_at)
            self.assertIsNone(grant.token_digest)
            self.assertEqual(self.original_version + 1,
                             db.session.get(User, self.admin_id).session_version)
        # A copied token cannot be replayed after process/application restart.
        self.token_file.write_text(token_contents, encoding='utf-8')
        replay = self.command('reset-admin-password-once', input_value=self.password_input(),
                              extra=['--authorization-file', str(self.token_file)])
        self.assertNotEqual(0, replay.exit_code)

    def test_one_time_grant_rejects_expiration_changed_version_and_wrong_backup(self):
        for failure in ('expired', 'changed_version', 'wrong_backup'):
            with self.subTest(failure=failure):
                if self.token_file.exists():
                    self.token_file.unlink()
                grant_id = self.authorize()
                with self.app.app_context():
                    grant = db.session.get(AdminRecoveryGrant, grant_id)
                    if failure == 'expired':
                        grant.expires_at = utc_now() - timedelta(seconds=1)
                    elif failure == 'changed_version':
                        grant.expected_session_version += 1
                    else:
                        grant.backup_digest = 'b' * 64
                    db.session.commit()
                result = self.command('reset-admin-password-once', input_value=self.password_input(),
                                      extra=['--authorization-file', str(self.token_file)])
                self.assertNotEqual(0, result.exit_code)
                self.unchanged_account()

    def test_one_time_grant_rejects_wrong_token_or_target_without_mutation(self):
        self.authorize()
        token_contents = self.token_file.read_text(encoding='utf-8')
        self.token_file.write_text('not-the-issued-authorization', encoding='utf-8')
        result = self.command('reset-admin-password-once', input_value=self.password_input(),
                              extra=['--authorization-file', str(self.token_file)])
        self.assertNotEqual(0, result.exit_code)
        self.unchanged_account()
        self.token_file.write_text(token_contents, encoding='utf-8')
        result = self.command('reset-admin-password-once', email='manager@example.test',
                              input_value=self.password_input(user_id=self.manager_id),
                              extra=['--authorization-file', str(self.token_file)])
        self.assertNotEqual(0, result.exit_code)
        self.unchanged_account()

    def test_failed_commit_rolls_back_password_and_grant_consumption_without_secret_logs(self):
        grant_id = self.authorize()
        secret_error = 'postgresql://must-not-log:secret@database.example/private'
        with patch('webapp.recovery.db.session.commit', side_effect=RuntimeError(secret_error)):
            with self.assertLogs(self.app.logger, level='WARNING') as logs:
                result = self.command('reset-admin-password-once', input_value=self.password_input(),
                                      extra=['--authorization-file', str(self.token_file)])
        self.assertNotEqual(0, result.exit_code)
        self.assertNotIn(secret_error, result.output + '\n'.join(logs.output))
        self.assertNotIn(NEW_PASSWORD, result.output + '\n'.join(logs.output))
        self.unchanged_account()
        self.assertTrue(self.token_file.is_file())
        with self.app.app_context():
            grant = db.session.get(AdminRecoveryGrant, grant_id)
            self.assertIsNone(grant.consumed_at)
            self.assertIsNotNone(grant.token_digest)

    def test_missing_or_invalid_backup_prevents_recovery_before_password_update(self):
        with patch('webapp.recovery.verify_backup', side_effect=ValueError('Backup inválido.')):
            result = self.app.test_cli_runner().invoke(args=[
                'reset-admin-password', '--email', ADMIN_LOGIN,
                '--backup-file', str(self.backup_file)], input=self.password_input())
        self.assertNotEqual(0, result.exit_code)
        self.unchanged_account()

    def test_diagnostic_is_read_only_and_does_not_reveal_hash_or_password(self):
        before = self.data_snapshot()
        result = self.command('auth-status')
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn(self.admin_id, result.output)
        self.assertNotIn(self.original_hash, result.output)
        self.assertNotIn(OLD_PASSWORD, result.output)
        self.assertEqual(before, self.data_snapshot())
        self.unchanged_account()
        with self.app.app_context():
            self.assertTrue(db.session.get(SecurityState, 1).bootstrap_complete)

    def test_backup_verifier_checks_archive_contents_and_corresponding_database(self):
        with self.app.app_context():
            database_name = db.engine.url.database
            output = '; dbname: ' + database_name + '\n' + '\n'.join(
                f'1; 0 1 TABLE DATA public {table} owner' for table in BACKUP_TABLES)
            with patch('webapp.recovery.subprocess.run', return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=output, stderr='')) as restore:
                digest = verify_backup(self.backup_file)
            self.assertRegex(digest, r'^[0-9a-f]{64}$')
            self.assertIn('--list', restore.call_args.args[0])
            self.assertNotIn('://', ' '.join(restore.call_args.args[0]))
            for wrong_output in (output.replace(database_name, 'different_database'),
                                 '; dbname: ' + database_name + '\n'):
                with self.subTest(output=wrong_output), patch(
                        'webapp.recovery.subprocess.run', return_value=subprocess.CompletedProcess(
                            args=[], returncode=0, stdout=wrong_output, stderr='')):
                    with self.assertRaises((ValueError, RuntimeError)):
                        verify_backup(self.backup_file)
            self.backup_file.write_bytes(b'not-a-postgresql-custom-archive')
            with self.assertRaises((ValueError, RuntimeError)):
                verify_backup(self.backup_file)

    def test_operator_backup_uses_hidden_password_and_creates_new_verified_archive(self):
        destination = self.folder / 'operator-created.dump'
        database_url = URL.create(
            'postgresql+psycopg', username='test-operator', password='hidden-password',
            host='postgres.example.test', port=5432, database='agenda_test',
            query={'sslmode': 'verify-full', 'sslrootcert': 'system'},
        )
        archive_listing = '; dbname: agenda_test\n' + '\n'.join(
            f'1; 0 1 TABLE DATA public {table} owner' for table in BACKUP_TABLES)

        def fake_process(arguments, **kwargs):
            if arguments[0] == 'pg_dump':
                kwargs['stdout'].write(b'PGDMPcomplete-test-archive')
                return subprocess.CompletedProcess(args=arguments, returncode=0, stderr=b'')
            return subprocess.CompletedProcess(
                args=arguments, returncode=0, stdout=archive_listing, stderr='')

        with self.app.app_context(), patch.object(db.engine, 'url', database_url), patch(
                'webapp.recovery.subprocess.run', side_effect=fake_process) as run:
            digest = create_operator_backup(destination)
        self.assertRegex(digest, r'^[0-9a-f]{64}$')
        self.assertTrue(destination.read_bytes().startswith(b'PGDMP'))
        dump_args, dump_kwargs = run.call_args_list[0]
        self.assertNotIn('hidden-password', ' '.join(dump_args[0]))
        self.assertNotIn('postgresql://', ' '.join(dump_args[0]))
        self.assertEqual('hidden-password', dump_kwargs['env']['PGPASSWORD'])
        self.assertEqual('verify-full', dump_kwargs['env']['PGSSLMODE'])
        with self.app.app_context(), patch.object(db.engine, 'url', database_url):
            with self.assertRaises(ValueError):
                create_operator_backup(destination)

    def test_external_operator_factory_requires_postgresql_and_forces_verified_tls(self):
        with patch('scripts.admin_operator.click.prompt', return_value=(
                'postgresql://operator:fictional-password@db.example.test:5432/agenda?sslmode=disable')):
            operator_app = create_operator_app()
        self.addCleanup(self.dispose_app, operator_app)
        url = operator_app.config['SQLALCHEMY_DATABASE_URI']
        self.assertIn('sslmode=verify-full', url)
        self.assertIn('sslrootcert=system', url)
        self.assertNotIn('sqlite', url)
        with patch('scripts.admin_operator.click.prompt', return_value='sqlite:///not-allowed.db'):
            with self.assertRaises(Exception) as error:
                create_operator_app()
        self.assertNotIn('fictional-password', str(error.exception))


if __name__ == '__main__':
    unittest.main()
