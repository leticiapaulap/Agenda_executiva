"""Manual operator app factory for Render Free, never used by the web service.

Run Flask CLI commands with: --app scripts.admin_operator:create_operator_app
The URL is entered without echo, never placed in command arguments or logs.
"""
import secrets

import click
from sqlalchemy.engine import make_url

from webapp import create_app


def create_operator_app():
    raw = click.prompt('URL EXTERNA do PostgreSQL autorizado (oculta)', hide_input=True)
    try:
        url = make_url(raw.strip())
        if url.get_backend_name() not in ('postgres', 'postgresql') or not url.host or not url.database:
            raise ValueError
        url = url.set(drivername='postgresql+psycopg')
        # libpq 17+ validates the server against system root CAs and hostname.
        url = url.update_query_dict({'sslmode': 'verify-full', 'sslrootcert': 'system'})
        return create_app({
            'SECRET_KEY': secrets.token_hex(32),  # local CLI only; no production session signing
            'SQLALCHEMY_DATABASE_URI': url.render_as_string(hide_password=False),
            'ADMIN_USERNAME': '', 'ADMIN_PASSWORD_HASH': '',
        })
    except Exception:
        raise click.ClickException('Não foi possível configurar a conexão PostgreSQL. Verifique a URL externa e os clientes TLS.') from None
