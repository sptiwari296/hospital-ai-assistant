"""Test-wide settings: generous rate limits so the suite's many logins aren't throttled.
Rate limiting itself is tested explicitly with a low limit in test_security.py."""
import os

os.environ.setdefault("LOGIN_RATE_LIMIT", "10000")
os.environ.setdefault("CHAT_RATE_LIMIT", "10000")
