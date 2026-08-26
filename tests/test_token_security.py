import os
from pathlib import Path
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import Column, MetaData, String, Table, Text, create_engine, text
from sqlalchemy.orm import Session

from app.models.db import Base, Job, NetBoxInstance
from app.security.tokens import (
    TokenEncryptionError,
    decrypt_token,
    encrypt_token,
    is_encrypted_token,
    migrate_token_columns,
    validate_token_encryption_key,
)


class TokenSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = Fernet.generate_key().decode()

    def test_round_trip_hides_plaintext_and_is_idempotent(self) -> None:
        with patch.dict(os.environ, {"HAROLD_TOKEN_ENCRYPTION_KEY": self.key}):
            encrypted = encrypt_token("netbox-secret-token")

            self.assertTrue(is_encrypted_token(encrypted))
            self.assertNotIn("netbox-secret-token", encrypted)
            self.assertEqual(decrypt_token(encrypted), "netbox-secret-token")
            self.assertEqual(encrypt_token(encrypted), encrypted)

    def test_plaintext_and_missing_key_fail_closed(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TokenEncryptionError):
                encrypt_token("netbox-secret-token")
            with self.assertRaises(TokenEncryptionError):
                validate_token_encryption_key()

        with patch.dict(os.environ, {"HAROLD_TOKEN_ENCRYPTION_KEY": self.key}):
            with self.assertRaises(TokenEncryptionError):
                decrypt_token("netbox-secret-token")

        with patch.dict(
            os.environ,
            {"HAROLD_TOKEN_ENCRYPTION_KEY": "not-a-fernet-key"},
        ):
            with self.assertRaises(TokenEncryptionError):
                validate_token_encryption_key()

    def test_models_encrypt_saved_and_per_job_tokens(self) -> None:
        with patch.dict(os.environ, {"HAROLD_TOKEN_ENCRYPTION_KEY": self.key}):
            instance = NetBoxInstance(
                name="production",
                url="https://netbox.example.com",
                token="saved-token",
            )
            job = Job(
                name="import",
                file_type="racks",
                netbox_url="https://netbox.example.com",
                netbox_token="job-token",
            )

            self.assertTrue(is_encrypted_token(instance._token))
            self.assertTrue(is_encrypted_token(job._netbox_token))
            self.assertEqual(instance.token, "saved-token")
            self.assertEqual(job.netbox_token, "job-token")

    def test_database_only_receives_ciphertext(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)

        with patch.dict(os.environ, {"HAROLD_TOKEN_ENCRYPTION_KEY": self.key}):
            with Session(engine) as session:
                session.add(
                    NetBoxInstance(
                        name="production",
                        url="https://netbox.example.com",
                        token="saved-token",
                    )
                )
                session.add(
                    Job(
                        name="import",
                        file_type="racks",
                        netbox_url="https://netbox.example.com",
                        netbox_token="job-token",
                    )
                )
                session.commit()

            with engine.connect() as connection:
                saved = connection.execute(
                    text("SELECT token FROM netbox_instances")
                ).scalar_one()
                copied = connection.execute(
                    text("SELECT netbox_token FROM jobs")
                ).scalar_one()

            self.assertTrue(is_encrypted_token(saved))
            self.assertTrue(is_encrypted_token(copied))
            self.assertNotIn("saved-token", saved)
            self.assertNotIn("job-token", copied)

    def test_runtime_manifests_reference_a_separate_key_secret(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative_path in ("k8s/base/app.yaml", "k8s/base/worker.yaml"):
            manifest = (root / relative_path).read_text()
            self.assertIn("name: HAROLD_TOKEN_ENCRYPTION_KEY", manifest)
            self.assertIn("name: harold-token-encryption", manifest)
            self.assertIn("key: HAROLD_TOKEN_ENCRYPTION_KEY", manifest)

        tracked_secret = (root / "k8s/base/secret.yaml").read_text()
        self.assertNotIn("HAROLD_TOKEN_ENCRYPTION_KEY", tracked_secret)

    def test_migration_encrypts_both_columns_and_supports_rollback(self) -> None:
        engine = create_engine("sqlite://")
        metadata = MetaData()
        instances = Table(
            "netbox_instances",
            metadata,
            Column("id", String, primary_key=True),
            Column("token", Text),
        )
        jobs = Table(
            "jobs",
            metadata,
            Column("id", String, primary_key=True),
            Column("netbox_token", Text),
        )
        metadata.create_all(engine)

        with engine.begin() as connection:
            connection.execute(instances.insert().values(id="i1", token="saved-token"))
            connection.execute(jobs.insert().values(id="j1", netbox_token="job-token"))
            with patch.dict(os.environ, {"HAROLD_TOKEN_ENCRYPTION_KEY": self.key}):
                migrate_token_columns(connection)
                saved = connection.execute(
                    text("SELECT token FROM netbox_instances WHERE id='i1'")
                ).scalar_one()
                copied = connection.execute(
                    text("SELECT netbox_token FROM jobs WHERE id='j1'")
                ).scalar_one()
                self.assertTrue(is_encrypted_token(saved))
                self.assertTrue(is_encrypted_token(copied))

                wrong_key = Fernet.generate_key().decode()
                with patch.dict(
                    os.environ,
                    {"HAROLD_TOKEN_ENCRYPTION_KEY": wrong_key},
                ):
                    with self.assertRaises(TokenEncryptionError):
                        migrate_token_columns(connection)

                migrate_token_columns(connection, decrypt=True)
                self.assertEqual(
                    connection.execute(
                        text("SELECT token FROM netbox_instances WHERE id='i1'")
                    ).scalar_one(),
                    "saved-token",
                )
                self.assertEqual(
                    connection.execute(
                        text("SELECT netbox_token FROM jobs WHERE id='j1'")
                    ).scalar_one(),
                    "job-token",
                )


if __name__ == "__main__":
    unittest.main()
