import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import check_password_hash, generate_password_hash

from webapp import create_app
from webapp import account
from webapp.models import AuditLog, LoginAttempt, SecurityState, Task, User, db


PASSWORD = 'Senha-atual-conta-teste-2026!'
NEW_PASSWORD = 'Nova-senha-independente-2026!'


class OwnPasswordTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='agenda-own-password-')
        self.addCleanup(self.folder.cleanup)
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'self-password-tests-only',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + (Path(self.folder.name) / 'web.db').as_posix(),
            'ADMIN_USERNAME': '', 'ADMIN_PASSWORD_HASH': '',
            'SESSION_COOKIE_SECURE': False, 'WTF_CSRF_SSL_STRICT': False,
        })
        result = self.app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if result.exception:
            raise result.exception
        with self.app.app_context():
            db.session.get(SecurityState, 1).bootstrap_complete = True
            for role in ('reader', 'manager', 'admin'):
                db.session.add(User(name=role, email=role + '@example.test', role=role,
                                    password_hash=generate_password_hash(PASSWORD),
                                    agenda_access=True, finance_access=False, vault_access=False))
            db.session.add(Task(title='Tarefa preservada', priority='Normal', completed=0))
            db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    @staticmethod
    def token(client, path):
        response = client.get(path)
        assert response.status_code == 200
        return re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()

    def login(self, role='reader', client=None, password=PASSWORD):
        client = client or self.client
        return client.post('/login', data={'csrf_token': self.token(client, '/login'),
                                          'username': role + '@example.test', 'password': password})

    def form(self, client=None, **changes):
        client = client or self.client
        return {'csrf_token': self.token(client, '/account/password'),
                'current_password': PASSWORD, 'password': NEW_PASSWORD,
                'password_confirm': NEW_PASSWORD} | changes

    def test_each_profile_can_change_only_own_password_and_invalidates_all_sessions(self):
        for role in ('reader', 'manager', 'admin'):
            with self.subTest(role=role):
                client, other_session = self.app.test_client(), self.app.test_client()
                self.assertEqual(302, self.login(role, client).status_code)
                self.assertEqual(302, self.login(role, other_session).status_code)
                with self.app.app_context():
                    user = db.session.query(User).filter_by(role=role).one()
                    user_id, version = user.id, user.session_version
                    preserved = (user.name, user.email, user.role, user.active, user.agenda_access,
                                 user.finance_access, user.vault_access, user.created_at)
                    hashes = {u.id: u.password_hash for u in db.session.query(User).all() if u.id != user_id}
                result = client.post('/account/password', data=self.form(client))
                self.assertEqual(302, result.status_code)
                self.assertTrue(result.location.endswith('/login'))
                self.assertEqual(302, client.get('/account/password').status_code)
                self.assertEqual(302, other_session.get('/account/password').status_code)
                self.assertEqual(200, self.login(role, client).status_code)
                self.assertEqual(302, self.login(role, client, NEW_PASSWORD).status_code)
                with self.app.app_context():
                    user = db.session.get(User, user_id)
                    self.assertTrue(check_password_hash(user.password_hash, NEW_PASSWORD))
                    self.assertEqual(version + 1, user.session_version)
                    self.assertEqual(preserved, (user.name, user.email, user.role, user.active,
                                                user.agenda_access, user.finance_access,
                                                user.vault_access, user.created_at))
                    self.assertEqual(hashes, {u.id: u.password_hash for u in db.session.query(User).all()
                                              if u.id != user_id})
                    audit = db.session.query(AuditLog).filter_by(target_user_id=user_id).one()
                    self.assertEqual(('own_password_changed', user_id), (audit.action, audit.actor_id))
                    self.assertEqual(1, db.session.query(Task).count())

    def test_rejects_missing_csrf_wrong_password_mismatch_and_target_injection(self):
        self.assertEqual(302, self.client.get('/account/password').status_code)
        self.login()
        self.assertEqual(400, self.client.post('/account/password', data={
            'current_password': PASSWORD, 'password': NEW_PASSWORD,
            'password_confirm': NEW_PASSWORD}).status_code)
        for fields, status in (({'current_password': 'Wrong-password-secret'}, 401),
                               ({'password_confirm': 'Unmatched-secret'}, 400),
                               ({'password': 'short', 'password_confirm': 'short'}, 400),
                               ({'user_id': 'someone-else'}, 400)):
            with self.subTest(fields=fields):
                response = self.client.post('/account/password', data=self.form(**fields))
                self.assertEqual(status, response.status_code)
                self.assertNotIn(NEW_PASSWORD.encode(), response.data)
                self.assertNotIn(PASSWORD.encode(), response.data)
        with self.app.app_context():
            self.assertEqual(0, db.session.query(AuditLog).count())
            for user in db.session.query(User).all():
                self.assertTrue(check_password_hash(user.password_hash, PASSWORD))

    def test_current_password_rate_limit_and_private_response(self):
        self.login()
        self.assertIn('no-store', self.client.get('/account/password').headers['Cache-Control'])
        for attempt in range(5):
            response = self.client.post('/account/password', data=self.form(current_password='wrong'))
            self.assertEqual(429 if attempt == 4 else 401, response.status_code)
        self.assertEqual(429, self.client.post('/account/password', data=self.form()).status_code)
        with self.app.app_context():
            self.assertEqual(1, db.session.query(LoginAttempt).count())
            self.assertEqual(0, db.session.query(AuditLog).count())

    def test_concurrent_reset_invalidates_request_instead_of_overwriting(self):
        self.login()
        original_verify = account.verify_current_password

        def concurrent_reset(*args, **kwargs):
            failure = original_verify(*args, **kwargs)
            user = db.session.query(User).filter_by(role='reader').one()
            user.password_hash = generate_password_hash('Senha-reset-concorrente-2026!')
            user.session_version += 1
            db.session.commit()
            return failure

        with patch('webapp.account.verify_current_password', side_effect=concurrent_reset):
            result = self.client.post('/account/password', data=self.form())
        self.assertEqual(302, result.status_code)
        self.assertTrue(result.location.endswith('/login'))
        with self.app.app_context():
            user = db.session.query(User).filter_by(role='reader').one()
            self.assertTrue(check_password_hash(user.password_hash, 'Senha-reset-concorrente-2026!'))
            self.assertEqual(0, db.session.query(AuditLog).count())


if __name__ == '__main__':
    unittest.main()
