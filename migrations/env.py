import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool, create_engine
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.models.db import Base
target_metadata = Base.metadata

# Prefer DATABASE_URL env var over alembic.ini (needed when running inside Docker)
_db_url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")


def run_migrations_offline():
    context.configure(url=_db_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = create_engine(_db_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()



if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
