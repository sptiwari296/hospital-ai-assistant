"""Give every synthetic user the demo password from .env (DEMO_PASSWORD), stored as a salted hash.

Run:  .venv\\Scripts\\python scripts\\set_demo_passwords.py
Local development only — these are test accounts for synthetic data.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.auth import hash_password  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import connect  # noqa: E402

if __name__ == "__main__":
    get_settings()  # loads .env
    password = os.getenv("DEMO_PASSWORD")
    if not password:
        raise SystemExit("Set DEMO_PASSWORD in .env first")
    with connect() as conn:
        users = conn.execute("SELECT user_id, auth_subject FROM users ORDER BY user_id").fetchall()
        for u in users:
            conn.execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (hash_password(password), u["user_id"]))
    print(f"Demo password set for {len(users)} users: {', '.join(u['auth_subject'] for u in users)}")
