"""Database connection helpers.

connect(patient_id)  — the APPLICATION connection. It logs in as the least-privilege role
                       `hospital_api`, which is subject to Row-Level Security: on patient tables
                       it can only see rows of `patient_id`. Without a patient it sees none.
admin_connect()      — the owner/superuser connection, ONLY for setup scripts, migrations and
                       test fixtures. Application code must never use it.
"""
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from app.config import get_settings


def _open(url: str) -> psycopg.Connection:
    conn = psycopg.connect(url, row_factory=dict_row)
    register_vector(conn)
    return conn


def connect(patient_id: str | None = None, user_id: str | None = None) -> psycopg.Connection:
    """Rows as dicts, `vector` type supported, patient scope set for Row-Level Security.
    `user_id` is only used by the my_assigned_patients view (staff picking a patient)."""
    conn = _open(get_settings().database_url)
    if patient_id or user_id:
        conn.execute("SELECT set_config('app.patient_id', %s, false), set_config('app.user_id', %s, false)",
                     (patient_id or "", user_id or ""))
        conn.commit()
    return conn


def admin_connect() -> psycopg.Connection:
    return _open(get_settings().admin_database_url)
