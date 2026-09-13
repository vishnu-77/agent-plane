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
    return create_engine(url, connect_args=connect_args_for(url), future=True, **kwargs)
