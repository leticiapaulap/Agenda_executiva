"""Navigation exposes the existing vault and keeps task records read-only."""
import re
import unittest

from werkzeug.security import generate_password_hash

from webapp import create_app
from webapp.catalog import AccessEntry
from webapp.models import Task, User, db


class PasswordNavigationTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'navigation-test-session-key',
            'SESSION_COOKIE_SECURE': False, 'WTF_CSRF_SSL_STRICT': False,
        })
        self.addCleanup(self.dispose)
        migration = self.app.test_cli_runner().invoke(args=['db', 'upgrade'])
        if migration.exception:
            raise migration.exception
        with self.app.app_context():
            self.user = User(name='Administrador', email='navigation@example.test',
                             password_hash=generate_password_hash('Navigation-test-password'),
                             role='admin', active=True, vault_access=True)
            db.session.add(self.user)
            db.session.add(Task(title='Registro antigo preservado', description='Descrição original'))
            db.session.add(AccessEntry(id='navigation-access', name='Serviço reservado',
                                       username='user@example.test', updated_at='2026-11-09',
                                       password_ciphertext='ciphertext-must-not-appear', is_corporate=0))
            db.session.commit()
        self.client = self.app.test_client()
        token = re.search(rb'name="csrf_token" value="([^"]+)"',
                          self.client.get('/login').data).group(1).decode()
        response = self.client.post('/login', data={
            'csrf_token': token, 'username': 'navigation@example.test',
            'password': 'Navigation-test-password',
        })
        self.assertEqual(302, response.status_code)

    def dispose(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def test_dashboard_links_to_password_manager_without_loading_credentials(self):
        response = self.client.get('/')
        self.assertEqual(200, response.status_code)
        self.assertIn('href="/vault">Abrir Senhas'.encode(), response.data)
        self.assertIn(b'1 acesso cadastrado', response.data)
        self.assertNotIn(b'ciphertext-must-not-appear', response.data)
        self.assertNotIn(b'user@example.test', response.data)
        self.assertNotIn(b'Tarefas pendentes', response.data)
        self.assertNotIn(b'/toggle', response.data)

    def test_admin_without_vault_can_discover_preserved_records(self):
        with self.app.app_context():
            user = db.session.query(User).one()
            user.vault_access = False
            db.session.commit()
        response = self.client.get('/')
        self.assertEqual(200, response.status_code)
        self.assertNotIn(b'href="/vault"', response.data)
        self.assertIn(b'href="/tasks/archive"', response.data)
        archive = self.client.get('/tasks/archive')
        self.assertEqual(200, archive.status_code)
        self.assertIn(b'Registro antigo preservado', archive.data)

    def test_search_opens_task_details_without_edit_links(self):
        response = self.client.get('/search?q=Registro')
        self.assertEqual(200, response.status_code)
        self.assertIn(b'href="/tasks/1"', response.data)
        self.assertNotIn(b'/tasks?edit=', response.data)
        self.assertIn(b'registros antigos', response.data)


if __name__ == '__main__':
    unittest.main()
