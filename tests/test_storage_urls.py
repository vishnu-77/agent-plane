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


def test_a_missing_postgres_driver_says_what_to_install(monkeypatch):
    """"Can't load plugin: sqlalchemy.dialects:postgresql.psycopg" tells nobody
    what to do, and a base install has no driver, so asking for Postgres on a
    host that installs only the core dependencies produced exactly that."""
    import builtins

    from agent_plane.storage import create_sql_engine

    real = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("psycopg"):
            raise ModuleNotFoundError("No module named 'psycopg'")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(RuntimeError, match=r"agent-plane\[postgres\]"):
        create_sql_engine("postgresql://u:p@localhost:5432/db")


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


# --------------------------------------------------------------------------- #
# the short path: one secret, one database url
# --------------------------------------------------------------------------- #
MASTER = "one-master-secret-0123456789abcdef"


def test_database_url_selects_postgres_without_a_second_variable():
    """Every platform calls it DATABASE_URL, and Railway's Postgres injects it."""
    s = Settings(secret_key=MASTER, database_url="postgres://u:p@host:5432/db")
    assert s.uses_postgres is True
    assert s.audit_db_url == "postgres://u:p@host:5432/db"
    assert normalise_db_url(s.audit_db_url).startswith("postgresql+psycopg://")

    # The older pair still works, untouched.
    old = Settings(jwt_secret="x" * 40, storage_backend="postgres",
                   postgres_url="postgresql+psycopg://u:p@h:5432/db")
    assert old.uses_postgres is True and old.audit_db_url.endswith("/db")


def test_one_secret_becomes_three_distinct_and_stable_ones():
    a = Settings(secret_key=MASTER)
    b = Settings(secret_key=MASTER)
    assert len({a.jwt_secret, a.audit_signing_key, a.api_key_secret}) == 3
    # Stable across processes, or every restart signs everyone out.
    assert (a.jwt_secret, a.audit_signing_key, a.api_key_secret) ==            (b.jwt_secret, b.audit_signing_key, b.api_key_secret)
    # And not the insecure defaults it replaced.
    assert a.jwt_secret != "dev-secret-change-me"
    assert a.audit_signing_key != "dev-audit-key-change-me"


def test_a_different_master_gives_different_secrets():
    assert Settings(secret_key=MASTER).jwt_secret != Settings(secret_key=MASTER + "!").jwt_secret


def test_an_explicit_secret_still_wins():
    s = Settings(secret_key=MASTER, jwt_secret="chosen-by-hand-0123456789")
    assert s.jwt_secret == "chosen-by-hand-0123456789"
    assert s.audit_signing_key != "dev-audit-key-change-me"   # still derived


def test_the_short_path_passes_the_production_checks():
    s = Settings(secret_key=MASTER, database_url="postgres://u:p@host:5432/db",
                 environment="production")
    assert s.production_errors() == []


def test_database_url_also_satisfies_the_serverless_guard(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    assert Settings(secret_key=MASTER, database_url="postgres://u:p@h:5432/db",
                    environment="production").production_errors() == []


# --------------------------------------------------------------------------- #
# connection failures, in words
# --------------------------------------------------------------------------- #
SUPABASE_IPV6 = ('connection is bad: connection to server at '
                 '"2a05:d018:cb1:bb00:2a4a:dbc2:a243:5cc9", port 5432 failed: '
                 'Network is unreachable')


def test_an_ipv6_only_database_names_the_pooler():
    """Supabase's direct connection is IPv6-only and most hosts have no IPv6
    route, so this arrives as a thousand lines of traceback saying nothing."""
    from agent_plane.storage import explain_connection_error

    out = explain_connection_error(Exception(SUPABASE_IPV6), SUPABASE_DIRECT)
    assert "IPv6" in out and "pooler" in out
    assert "db.abcdefgh.supabase.co:5432" in out


def test_a_rejected_password_mentions_encoding():
    from agent_plane.storage import explain_connection_error

    out = explain_connection_error(
        Exception('FATAL: password authentication failed for user "postgres"'), SUPABASE_DIRECT)
    assert "%23" in out and "%40" in out


def test_an_unreachable_host_says_so_without_a_traceback():
    from agent_plane.storage import explain_connection_error

    out = explain_connection_error(Exception("Connection refused"), RAILWAY)
    assert "Cannot reach the database" in out
    assert len(out.splitlines()) == 1
