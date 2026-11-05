"""Authenticated vault encryption with an explicit, external, versioned keyring.

This module never creates keys and never uses Flask's session secret. Missing or
invalid configuration leaves the access directory usable, with secrets disabled.
"""
import base64
import binascii
import json
import os
import re
from secrets import token_bytes

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # Keep the directory accessible when dependencies are absent.
    AESGCM = None


MAX_PASSWORD_BYTES = 4096
KEY_ID = re.compile(r'^[A-Za-z0-9_-]{1,32}$')
ERROR_MESSAGE = ('Não foi possível acessar a senha. Verifique as permissões e '
                 'a configuração segura do cofre.')


class VaultError(ValueError):
    """A deliberately generic error: never include configuration or user data."""


def _decode(value):
    if not isinstance(value, str):
        raise ValueError
    return base64.b64decode(value.encode('ascii'), altchars=b'-_', validate=True)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class VaultCipher:
    def __init__(self, config=None):
        source = os.environ if config is None else config
        self.available = False
        self._keys = {}
        self.active_key_id = None
        self.notice = ('Modo catálogo: o armazenamento de senhas está desativado. '
                       'Configure a chave exclusiva e a autorização de uso do cofre.')
        enabled = source.get('VAULT_ENABLED', False)
        if enabled is not True and str(enabled).lower() != 'true':
            return
        try:
            encoded = source.get('VAULT_KEYS', '')
            if not isinstance(encoded, str) or not 1 <= len(encoded) <= 10000:
                raise ValueError
            keys = json.loads(encoded, object_pairs_hook=_unique_object)
            active = source.get('VAULT_ACTIVE_KEY_ID', '')
            if (not isinstance(keys, dict) or not 1 <= len(keys) <= 16
                    or not isinstance(active, str) or not KEY_ID.fullmatch(active)):
                raise ValueError
            decoded = {}
            for key_id, value in keys.items():
                if not isinstance(key_id, str) or not KEY_ID.fullmatch(key_id):
                    raise ValueError
                material = _decode(value)
                if len(material) != 32:
                    raise ValueError
                decoded[key_id] = material
            if active not in decoded or AESGCM is None:
                raise ValueError
            self._keys = decoded
            self.active_key_id = active
            self.available = True
            self.notice = ('Cofre habilitado para acessos pessoais autorizados. '
                           'Credenciais corporativas permanecem somente no catálogo.')
        except (ValueError, TypeError, UnicodeError, binascii.Error):
            self.notice = ('Modo catálogo: a configuração segura do cofre está indisponível. '
                           'Os dados existentes foram preservados.')

    @staticmethod
    def _aad(entry_id, key_id):
        # Record identity prevents moving an otherwise valid ciphertext to another
        # record. IDs are generated before encryption and never change afterwards.
        if not isinstance(entry_id, str) or not 1 <= len(entry_id) <= 100:
            raise VaultError(ERROR_MESSAGE)
        return b'agenda-executiva:vault:v1:' + key_id.encode('ascii') + b':' + entry_id.encode('utf-8')

    def encrypt(self, secret, entry_id):
        try:
            if not self.available or not isinstance(secret, str):
                raise ValueError
            payload = secret.encode('utf-8')
            if not 1 <= len(payload) <= MAX_PASSWORD_BYTES:
                raise ValueError
            nonce = token_bytes(12)
            encrypted = AESGCM(self._keys[self.active_key_id]).encrypt(
                nonce, payload, self._aad(entry_id, self.active_key_id))
            return 'v1:' + self.active_key_id + ':' + base64.urlsafe_b64encode(nonce + encrypted).decode('ascii')
        except Exception:
            raise VaultError(ERROR_MESSAGE) from None

    def decrypt(self, envelope, entry_id):
        try:
            if not self.available or not isinstance(envelope, str) or len(envelope) > 6000:
                raise ValueError
            parts = envelope.split(':')
            if len(parts) != 3 or parts[0] != 'v1' or not KEY_ID.fullmatch(parts[1]):
                raise ValueError
            key = self._keys.get(parts[1])
            if key is None:
                raise ValueError
            data = _decode(parts[2])
            if not 29 <= len(data) <= MAX_PASSWORD_BYTES + 28:
                raise ValueError
            payload = AESGCM(key).decrypt(data[:12], data[12:], self._aad(entry_id, parts[1]))
            return payload.decode('utf-8')
        except Exception:
            raise VaultError(ERROR_MESSAGE) from None

    def rotate(self, envelope, entry_id):
        return self.encrypt(self.decrypt(envelope, entry_id), entry_id)
