import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text


TOKEN_PREFIX = "fernet:v1:"
TOKEN_COLUMNS = (
    ("netbox_instances", "token"),
    ("jobs", "netbox_token"),
)


class TokenEncryptionError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = os.environ.get("HAROLD_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise TokenEncryptionError("HAROLD_TOKEN_ENCRYPTION_KEY is required")
    try:
        return Fernet(key.encode())
    except (TypeError, ValueError) as exc:
        raise TokenEncryptionError(
            "HAROLD_TOKEN_ENCRYPTION_KEY is not a valid Fernet key"
        ) from exc


def is_encrypted_token(value: str) -> bool:
    return value.startswith(TOKEN_PREFIX)


def encrypt_token(value: str) -> str:
    if is_encrypted_token(value):
        return value
    encrypted = _fernet().encrypt(value.encode()).decode()
    return f"{TOKEN_PREFIX}{encrypted}"


def decrypt_token(value: str) -> str:
    if not is_encrypted_token(value):
        raise TokenEncryptionError("refusing to read an unencrypted NetBox token")
    try:
        return _fernet().decrypt(value.removeprefix(TOKEN_PREFIX).encode()).decode()
    except InvalidToken as exc:
        raise TokenEncryptionError("NetBox token decryption failed") from exc


def validate_token_encryption_key() -> None:
    _fernet()


def migrate_token_columns(connection, *, decrypt: bool = False) -> None:
    for table, column in TOKEN_COLUMNS:
        rows = connection.execute(
            text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")
        ).mappings()
        for row in rows:
            current = row[column]
            if decrypt:
                if not is_encrypted_token(current):
                    continue
                updated = decrypt_token(current)
            else:
                if is_encrypted_token(current):
                    # Idempotent reruns must still prove that the configured key
                    # can read every stored token.
                    decrypt_token(current)
                    continue
                updated = encrypt_token(current)
            connection.execute(
                text(f"UPDATE {table} SET {column} = :token WHERE id = :id"),
                {"token": updated, "id": row["id"]},
            )
