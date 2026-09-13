"""The connection strings a platform actually hands you.

Railway gives `postgres://`, Supabase gives `postgresql://`, and SQLAlchemy
reads both as psycopg2, which this project does not ship. A pasted URL used to
fail with a driver error, which reads like "Postgres is not supported" rather
than "the prefix is wrong".
"""
from __future__ import annotations

import pytest

from agent_plane.config import Settings
from agent_plane.storage import connect_args_for, normalise_db_url

SUPABASE_DIRECT = "postgresql://postgres:pw@db.abcdefgh.supabase.co:5432/postgres"
SUPABASE_POOLER = "postgresql://postgres.abcdefgh:pw@aws-0-eu-west-2.pooler.supabase.com:6543/postgres"
RAILWAY = "postgres://postgres:pw@containers-us-west-1.railway.app:7432/railway"


@pytest.mark.parametrize("url", [SUPABASE_DIRECT, SUPABASE_POOLER, RAILWAY,
                                 "postgresql+psycopg://u:p@localhost:5432/agentplane"])
def test_every_postgres_url_form_selects_the_driver_we_ship(url):
    assert normalise_db_url(url).startswith("postgresql+psycopg://")


def test_the_rest_of_the_url_is_left_alone():
    out = normalise_db_url(RAILWAY)
    assert out.endswith("@containers-us-west-1.railway.app:7432/railway")
    assert "postgres:pw" in out


def test_sqlite_is_untouched():
    assert normalise_db_url("sqlite:///audit.db") == "sqlite:///audit.db"
    assert connect_args_for("sqlite:///audit.db") == {"check_same_thread": False}


def test_a_transaction_pooler_turns_off_prepared_statements():
    """Supavisor and PgBouncer hand each transaction a different backend, so a
    prepared statement made on one is not there for the next."""
    assert connect_args_for(normalise_db_url(SUPABASE_POOLER)) == {"prepare_threshold": None}
    # A direct connection keeps them: it is one backend for the session.
    assert connect_args_for(normalise_db_url(SUPABASE_DIRECT)) == {}


def test_psycopg2_urls_never_get_a_psycopg3_argument():
    """prepare_threshold is psycopg 3 only; passing it to psycopg2 is an error."""
    assert connect_args_for("postgresql+psycopg2://u:p@host:6543/db") == {}


# --------------------------------------------------------------------------- #
# choosing a cache, and refusing to lose state
# --------------------------------------------------------------------------- #
def settings(**overrides) -> Settings:
    return Settings(jwt_secret="x" * 40, **overrides)


def test_postgres_does_not_drag_in_redis():
    """A managed Postgres does not come with one, and requiring it made the
    database choice harder to adopt than it needed to be."""
    assert settings(storage_backend="postgres").uses_redis is False
    assert settings(storage_backend="postgres",
                    redis_url="redis://cache:6379/0").uses_redis is True
    assert settings(cache_backend="redis").uses_redis is True
    assert settings(cache_backend="memory",
                    redis_url="redis://cache:6379/0").uses_redis is False


def test_ephemeral_storage_is_refused_in_production(monkeypatch):
    """SQLite on a serverless platform loses every account at the next cold
    start, which looks like "my account vanished" rather than an error."""
    monkeypatch.setenv("VERCEL", "1")
    fatal = settings(environment="production", audit_signing_key="y" * 40).production_errors()
    assert any("does not survive a cold start" in e for e in fatal), fatal

    # Postgres on the same platform is fine.
    ok = settings(environment="production", audit_signing_key="y" * 40,
                  storage_backend="postgres").production_errors()
    assert not any("cold start" in e for e in ok), ok


def test_a_long_lived_host_is_not_treated_as_serverless(monkeypatch):
    for marker in ("VERCEL", "AWS_LAMBDA_FUNCTION_NAME", "K_SERVICE",
                   "FUNCTION_TARGET", "AZURE_FUNCTIONS_ENVIRONMENT"):
        monkeypatch.delenv(marker, raising=False)
    assert settings().storage_is_ephemeral is False
    assert settings().serverless_platform is None
