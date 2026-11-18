"""One-time security setup (idempotent). Uses the ADMIN connection.

1. Sets the password of the least-privilege API role `hospital_api` (from API_DB_PASSWORD).
2. Encrypts patients' national ID numbers into national_id_encrypted and removes the plaintext.

Run after the database is created:  .venv\\Scripts\\python scripts\\setup_security.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from psycopg import sql  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.crypto import decrypt, encrypt  # noqa: E402
from app.db import admin_connect  # noqa: E402

if __name__ == "__main__":
    get_settings()
    with admin_connect() as conn:
        conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(os.getenv("API_DB_USER", "hospital_api")), sql.Literal(os.environ["API_DB_PASSWORD"])))
        print("API role password set.")

        rows = conn.execute("SELECT patient_id, national_id FROM patients WHERE national_id IS NOT NULL").fetchall()
        for r in rows:
            token = encrypt(r["national_id"])
            assert decrypt(token) == r["national_id"]  # verify before deleting the plaintext
            conn.execute("UPDATE patients SET national_id_encrypted = %s, national_id = NULL WHERE patient_id = %s",
                         (token, r["patient_id"]))
        print(f"Encrypted national IDs: {len(rows)} (already encrypted ones are skipped).")
