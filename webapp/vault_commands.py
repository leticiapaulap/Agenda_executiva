"""Operator-only key rotation; no plaintext is printed or sent to clients."""
import click
from sqlalchemy import select

from agenda.vault import VaultCipher
from .catalog import AccessEntry
from .models import db


def register_commands(app):
    @app.cli.command('vault-rotate')
    @click.option('--apply', is_flag=True, help='Recriptografar atomicamente; o padrão é somente validar.')
    def vault_rotate(apply):
        cipher = VaultCipher(app.config)
        if not cipher.available:
            raise click.ClickException('Cofre indisponível. Verifique a configuração segura das chaves.')
        try:
            query = select(AccessEntry).where(AccessEntry.password_ciphertext.is_not(None)).order_by(AccessEntry.id)
            if apply:
                query = query.with_for_update()
            rows = db.session.scalars(query).all()
            for row in rows:
                replacement = cipher.rotate(row.password_ciphertext, row.id)
                if apply:
                    row.password_ciphertext = replacement
            count = len(rows)
            if apply:
                db.session.commit()
            else:
                db.session.rollback()
        except Exception:
            db.session.rollback()
            raise click.ClickException('Rotação não realizada. Verifique chaves, permissões e backup.') from None
        click.echo(f'{count} senha(s) ' + ('recriptografada(s).' if apply else 'validada(s); nenhuma alteração aplicada.'))
