"""Create .env.cloud for the server: FRESH secrets (never reuse local ones) + your LLM key.

Run:  .venv\\Scripts\\python scripts\\cloud\\make_cloud_env.py <public-ip-or-domain>
Writes .env.cloud in the repository root (git-ignored). Copy it to the server as .env with scp.
The demo password is printed once so you can share it with the people you choose.
"""
import os
import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
if len(sys.argv) != 2:
    raise SystemExit("usage: make_cloud_env.py <public-ip-or-domain>")
target = sys.argv[1].strip()
site = f"{target}.sslip.io" if target.replace(".", "").isdigit() else target

local = {k.upper(): v for k, v in dotenv_values(ROOT / ".env").items()}   # accepts lowercase key names
groq = local.get("GROQ_API_KEY") or os.getenv("GROQ_API_KEY")
if not groq:
    raise SystemExit("No GROQ_API_KEY found in the local .env")

demo_password = f"Demo-{secrets.token_urlsafe(9)}"
values = {
    "SITE_ADDRESS": site,
    "POSTGRES_DB": "hospital", "POSTGRES_USER": "hospital_app",
    "POSTGRES_PASSWORD": secrets.token_urlsafe(24), "POSTGRES_PORT": "5432",
    "HOSPITAL_TIMEZONE": local.get("HOSPITAL_TIMEZONE", "Asia/Kolkata"),
    "API_DB_USER": "hospital_api", "API_DB_PASSWORD": secrets.token_urlsafe(24),
    "JWT_SECRET": secrets.token_urlsafe(48), "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "DEMO_PASSWORD": demo_password, "COOKIE_SECURE": "true",
    "LLM_PROVIDER": "groq", "GROQ_API_KEY": groq,
}
out = ROOT / ".env.cloud"
out.write_text("# Cloud server settings - generated, git-ignored. Copy to the server as .env\n"
               + "\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")
print(f"Wrote {out.name} for https://{site}")
print(f"Demo password for the live site: {demo_password}")
