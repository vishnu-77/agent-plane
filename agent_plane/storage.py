"""One place where SQL engines are made.

Every store needs the same two decisions, and they were copied into each of
them. Worse, the copies only knew about SQLite: the URL forms a managed
Postgres actually hands you did not work, which made "point it at Supabase"
harder than it should be.

Three things are handled here:

* **The URL people are given.** Railway hands out ``postgres://``, Supabase
  hands out ``postgresql://``. SQLAlchemy reads both as psycopg2, which this
  project does not ship, so a pasted connection string failed with a driver
  error. Both are normalised to the psycopg (3) driver that is installed.
* **Poolers.** Supabase's Supavisor and PgBouncer in transaction mode give a
  different backend connection to each transaction, so a prepared statement
  made on one is not there for the next. psycopg prepares automatically after
  a few executions, which surfaces as "prepared statement already exists".
  Prepared statements are turned off when the URL points at such a pooler.
* **SQLite's thread check**, which every store disabled by hand.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import NoSuchModuleError

# Enough to recognise a transaction-mode pooler. 6543 is Supavisor's transaction
# port; pgbouncer=true is the flag Prisma and friends put on the URL.
_POOLER_HINTS = ("pooler.supabase.com", ":6543", "pgbouncer=true")


def normalise_db_url(db_url: str) -> str:
    """The URL a platform gave you, in the form SQLAlchemy needs."""
    if db_url.startswith("postgres://"):          # Railway, Heroku
        db_url = "postgresql://" + db_url[len("postgres://"):]
    if db_url.startswith("postgresql://"):        # would select psycopg2
        db_url = "postgresql+psycopg://" + db_url[len("postgresql://"):]
    return db_url


def connect_args_for(db_url: str) -> dict[str, Any]:
    if db_url.startswith("sqlite"):
        return {"check_same_thread": False}
    if "+psycopg://" in db_url and any(hint in db_url for hint in _POOLER_HINTS):
        # psycopg 3 only; psycopg2 has no such argument.
        return {"prepare_threshold": None}
    return {}


def create_sql_engine(db_url: str, **kwargs: Any) -> Engine:
    """The engine every store should use."""
    url = normalise_db_url(db_url)
    try:
        return create_engine(url, connect_args=connect_args_for(url), future=True, **kwargs)
    except NoSuchModuleError as exc:
        # "Can't load plugin: sqlalchemy.dialects:postgresql.psycopg" tells
        # nobody what to do. The driver is an optional extra, so a base install
        # asked for Postgres and got a plugin error instead of an instruction.
        if "psycopg" in str(exc):
            raise RuntimeError(
                "STORAGE_BACKEND=postgres needs the Postgres driver, which is an "
                "optional extra: install agent-plane[postgres] (the container image "
                "already includes it)."
            ) from exc
        raise


def explain_connection_error(exc: Exception, db_url: str) -> str:
    """One line a person can act on, instead of a thousand of traceback.

    The common failures here are environmental and each has a specific
    remedy, but they arrive as the same wall of SQLAlchemy frames repeated on
    every restart, which on a platform with a log rate limit buries the one
    line that matters.
    """
    message = str(exc)
    host = db_url.split("@")[-1].split("/")[0] if "@" in db_url else db_url

    if "Network is unreachable" in message and _looks_ipv6(message):
        return (
            f"Cannot reach the database at {host}: the address it resolves to is IPv6 "
            "and this host has no IPv6 route. Supabase's direct connection is IPv6-only; "
            "use the connection pooler instead (Project Settings, Database, Connection "
            "pooling), which is reachable over IPv4."
        )
    if "Network is unreachable" in message or "Connection refused" in message:
        return f"Cannot reach the database at {host}. Check DATABASE_URL and that the database accepts connections from here."
    if "password authentication failed" in message:
        return (
            f"The database at {host} rejected the credentials. If the password contains "
            "punctuation it must be percent-encoded in the URL: # is %23, @ is %40."
        )
    if "does not exist" in message and "database" in message:
        return f"The database named in DATABASE_URL does not exist on {host}."
    return f"Cannot open the database at {host}: {message.splitlines()[0]}"


def _looks_ipv6(message: str) -> bool:
    """An IPv6 address in the "at "..."" part of a psycopg error."""
    between = message.split('at "', 1)[-1].split('"', 1)[0] if 'at "' in message else ""
    return between.count(":") >= 2
