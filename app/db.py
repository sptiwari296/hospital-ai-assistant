"""Database connection helper."""
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from app.config import get_settings


def connect() -> psycopg.Connection:
    """Open a connection that returns rows as dicts and understands the `vector` type."""
    conn = psycopg.connect(get_settings().database_url, row_factory=dict_row)
    register_vector(conn)
    return conn
