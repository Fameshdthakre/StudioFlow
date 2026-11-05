"""SQLAlchemy connection boundary for existing parameterized feature queries.

Only placeholder conversion is performed; dialect differences are explicit in
repositories rather than string-rewriting arbitrary SQL.
"""

from contextlib import contextmanager
from functools import lru_cache
import re

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

SQL_TOKENS = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\?|\buser\b", re.IGNORECASE
)


class Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class Result:
    def __init__(self, result):
        self.result = result
        self.rowcount = result.rowcount

    def fetchone(self):
        row = self.result.mappings().fetchone()
        return Row(row) if row is not None else None

    def fetchall(self):
        return [Row(row) for row in self.result.mappings()]

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    dialect = "postgresql"

    def __init__(self, connection):
        self.raw = connection

    def execute(self, statement, parameters=()):
        if not isinstance(statement, str):
            return Result(self.raw.execute(statement, parameters or {}))
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            # Shared submission/checkpoint fence. Network and paid work never run
            # while this transaction-level lock is held.
            return Result(
                self.raw.execute(text("SELECT pg_advisory_xact_lock(193700, 1)"))
            )
        if statement.strip().upper() == "BEGIN":
            return Result(
                self.raw.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                )
            )
        values = {}

        def bind(match):
            token = match.group()
            if token == "?":
                name = f"p{len(values)}"
                values[name] = parameters[len(values)]
                return f":{name}"
            if token.lower() == "user":
                return '"user"'
            return token

        sql = SQL_TOKENS.sub(bind, statement)
        if len(values) != len(parameters):
            raise ValueError("SQL parameter count mismatch")
        return Result(self.raw.execute(text(sql), values))


@lru_cache(maxsize=8)
def engine(url):
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        connect_args={"connect_timeout": 10},
    )


@contextmanager
def connect(url):
    with engine(url).begin() as raw:
        yield Connection(raw)


@lru_cache(maxsize=4)
def lease_engine(url):
    # Lease renewal must not wait behind a pool full of feature transactions.
    return create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 10})


@contextmanager
def connect_lease(url):
    with lease_engine(url).begin() as raw:
        yield Connection(raw)


def check_schema(url):
    with connect(url) as c:
        version = c.execute("SELECT version_num FROM alembic_version").fetchone()
        if not version or version[0] != "0001_platform":
            raise RuntimeError(
                "Database schema is incompatible. Run the tested maintenance migration first."
            )
