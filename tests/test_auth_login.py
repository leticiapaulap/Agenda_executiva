"""Authentication regression checks using disposable databases and fictitious users."""
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.exc import OperationalError
from werkzeug.security import check_password_hash, generate_password_hash

from webapp import create_app
from webapp.auth import _DUMMY_PASSWORD_HASH, password_hash_is_supported
from webapp.models import SecurityState, User, db


class AuthenticationLoginTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='agenda-authentication-test-')
        self.addCleanup(self.directory.cleanup)
        self.username = 'admin@example.test'
        self.password = 'Senha-legitima-ficticia-2026!'
        self.config = {
            'TESTING': True, 'SECRET_KEY': 'authentication-tests-only',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (Path(self.directory.name) / 'web.db').as_posix(),
            'ADMIN_USERNAME': self.username,
            'ADMIN_PASSWORD_HASH': generate_password_hash(self.password),
            'SESSION_COOKIE_SECURE': False, 'WTF_CSRF_SSL_STRICT': False,
        }
        self.app = create_app(self.config)
        self.addCleanup(self.dispose, self.app)
        result = self.app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if result.exception:
            raise result.exception

    @staticmethod
    def dispose(app):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()

    def login(self, username=None, password=None, app=None):
        client = (app or self.app).test_client()
        response = client.get('/login')
        csrf = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
        response = client.post('/login', data={
            'csrf_token': csrf,
            'username': self.username if username is None else username,
            'password': self.password if password is None else password,
        })
        return client, response

    def test_correct_password_normalized_login_and_session(self):
        client, response = self.login(username='  ADMIN@EXAMPLE.TEST  ')
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            user = db.session.query(User).one()
            self.assertEqual(self.username, user.email)
            self.assertIsNotNone(user.last_login)
            session_id = user.get_id()
        with client.session_transaction() as session:
            self.assertEqual(session_id, session['_user_id'])
            self.assertTrue(session['_fresh'])
            self.assertTrue(session['_permanent'])
        self.assertEqual(200, client.get('/users').status_code)

    def test_unicode_normalization_uses_persisted_canonical_login(self):
        self.app.config['ADMIN_USERNAME'] = '  Straße  '
        _, response = self.login(username='STRASSE')
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            self.assertEqual('strasse', db.session.query(User).one().email)

    def test_changed_deployment_hash_does_not_overwrite_after_restart(self):
        self.assertEqual(302, self.login()[1].status_code)
        with self.app.app_context():
            user = db.session.query(User).one()
            user_id, original_hash, original_version = user.id, user.password_hash, user.session_version
        changed = create_app(self.config | {
            'ADMIN_USERNAME': 'different-bootstrap@example.test',
            'ADMIN_PASSWORD_HASH': generate_password_hash('Senha-ambiente-nao-persistida!'),
        })
        self.addCleanup(self.dispose, changed)
        self.assertEqual(302, self.login(app=changed)[1].status_code)
        self.assertEqual(200, self.login(password='Senha-ambiente-nao-persistida!', app=changed)[1].status_code)
        with changed.app_context():
            user = db.session.query(User).one()
            self.assertEqual((user_id, original_hash, original_version),
                             (user.id, user.password_hash, user.session_version))
            self.assertTrue(db.session.get(SecurityState, 1).bootstrap_complete)

    def test_existing_accounts_take_precedence_over_invalid_bootstrap_configuration(self):
        for marked in (False, True):
            with self.subTest(bootstrap_complete=marked):
                with self.app.app_context():
                    existing = db.session.query(User).first()
                    if existing is None:
                        existing = User(name='Administrador existente', email=self.username,
                                        password_hash=generate_password_hash(self.password), role='admin',
                                        active=True, agenda_access=True, finance_access=True)
                        db.session.add(existing)
                    db.session.get(SecurityState, 1).bootstrap_complete = marked
                    db.session.commit()
                self.app.config.update(ADMIN_USERNAME='unused', ADMIN_PASSWORD_HASH='broken-deployment-hash')
                self.assertEqual(302, self.login()[1].status_code)
                with self.app.app_context():
                    self.assertEqual(1, db.session.query(User).count())
                    self.assertTrue(db.session.get(SecurityState, 1).bootstrap_complete)

    def test_bootstrap_configuration_errors_are_generic_and_do_not_import_accounts(self):
        for bad_hash, reason in (('', 'bootstrap_unconfigured'),
                                 ('scrypt:32768:8:1$fake-salt$credential-sentinel', 'bootstrap_hash_invalid')):
            with self.subTest(reason=reason):
                self.app.config['ADMIN_PASSWORD_HASH'] = bad_hash
                with self.assertLogs(self.app.logger, level='ERROR') as captured:
                    _, response = self.login()
                self.assertEqual(503, response.status_code)
                self.assertIn('Não foi possível entrar agora.'.encode(), response.data)
                self.assertIn('reason=' + reason, '\n'.join(captured.output))
                for secret in (self.password, self.username, 'credential-sentinel'):
                    self.assertNotIn(secret, '\n'.join(captured.output))
                    self.assertNotIn(secret.encode(), response.data)
                with self.app.app_context():
                    self.assertEqual(0, db.session.query(User).count())
                    self.assertFalse(db.session.get(SecurityState, 1).bootstrap_complete)

    def test_unknown_incorrect_inactive_and_malformed_hash_have_same_public_failure(self):
        self.assertEqual(302, self.login()[1].status_code)
        with self.app.app_context():
            user = db.session.query(User).one()
            user_id, original_hash = user.id, user.password_hash
        scenarios = (
            ('unknown_account', 'unknown-credential-sentinel', self.password, True, original_hash, True),
            ('invalid_credentials', self.username, 'incorrect-credential-sentinel', True, original_hash, False),
            ('inactive_account', self.username, self.password, False, original_hash, True),
            ('stored_hash_invalid', self.username, self.password, True, 'malformed-credential-sentinel', True),
        )
        for reason, username, password, active, stored_hash, dummy in scenarios:
            with self.subTest(reason=reason):
                with self.app.app_context():
                    user = db.session.get(User, user_id)
                    user.active, user.password_hash = active, stored_hash
                    db.session.commit()
                with self.assertLogs(self.app.logger, level='WARNING') as captured, patch(
                        'webapp.auth.check_password_hash', wraps=check_password_hash) as checker:
                    client, response = self.login(username=username, password=password)
                self.assertEqual(200, response.status_code)
                self.assertIn('Usuário ou senha inválidos.'.encode(), response.data)
                self.assertIn('reason=' + reason, '\n'.join(captured.output))
                for secret in (self.username, self.password, 'credential-sentinel', original_hash):
                    self.assertNotIn(secret, '\n'.join(captured.output))
                self.assertEqual(1, checker.call_count)
                self.assertEqual(_DUMMY_PASSWORD_HASH if dummy else original_hash, checker.call_args.args[0])
                with client.session_transaction() as session:
                    self.assertNotIn('_user_id', session)

    def test_database_error_is_sanitized_without_exception_or_url(self):
        sensitive = 'postgresql://credential-sentinel:password-sentinel@database.invalid/db'
        with patch('webapp.routes.bootstrap_legacy_admin', side_effect=OperationalError(
                'secret-query', {}, Exception(sensitive))), self.assertLogs(
                    self.app.logger, level='ERROR') as captured:
            _, response = self.login()
        self.assertEqual(503, response.status_code)
        self.assertIn('reason=database_unavailable', '\n'.join(captured.output))
        self.assertNotIn('credential-sentinel', '\n'.join(captured.output))
        self.assertNotIn('password-sentinel', '\n'.join(captured.output))
        self.assertNotIn(sensitive.encode(), response.data)
        self.assertNotIn('Traceback', '\n'.join(captured.output))

    def test_missing_schema_is_sanitized_and_never_bootstraps_without_migration(self):
        unmigrated = create_app(self.config | {'SQLALCHEMY_DATABASE_URI': 'sqlite://'})
        self.addCleanup(self.dispose, unmigrated)
        with self.assertLogs(unmigrated.logger, level='ERROR') as captured:
            _, response = self.login(app=unmigrated)
        self.assertEqual(503, response.status_code)
        self.assertIn('reason=database_schema', '\n'.join(captured.output))
        self.assertNotIn('no such table', response.get_data(as_text=True))

    def test_supported_hashes_include_current_and_legacy_pbkdf2(self):
        for method in ('scrypt', 'pbkdf2:sha256:1000', 'pbkdf2:sha512:1000'):
            with self.subTest(method=method):
                password_hash = generate_password_hash(self.password, method=method)
                self.assertTrue(password_hash_is_supported(password_hash))
        for invalid in ('', None, 'plaintext-password', 'sha256$salt$abcdef',
                        'scrypt:32768:8:1$salt$abcdef', 'pbkdf2:sha256:0$salt$' + '0' * 64):
            self.assertFalse(password_hash_is_supported(invalid))


if __name__ == '__main__':
    unittest.main()
