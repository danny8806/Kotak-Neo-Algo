"""Isolated PostgreSQL schemas for database-backed integration tests."""
from __future__ import annotations

import os
import secrets
from collections.abc import Callable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def create_test_database() -> tuple[str, Callable[[], None]]:
    base = os.environ.get("TEST_DATABASE_URL", "")
    if not base.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("Set TEST_DATABASE_URL to PostgreSQL 16 for database-backed tests")
    base_url = make_url(base)
    if base_url.drivername == "postgresql":
        # requirements-live installs psycopg v3, not the legacy psycopg2 driver.
        base_url = base_url.set(drivername="postgresql+psycopg")
    admin = create_engine(base_url, pool_pre_ping=True)
    schema = "test_" + secrets.token_hex(10)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = base_url.update_query_dict({"options": f"-c search_path={schema}"})

    def drop_schema() -> None:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()

    return url.render_as_string(hide_password=False), drop_schema
