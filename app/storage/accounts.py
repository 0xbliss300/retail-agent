"""SQLite 中的账号凭据和登录会话。"""

from contextlib import closing
from hashlib import scrypt, sha256
from secrets import compare_digest, token_urlsafe
from time import time

from app.storage.database import get_connection

SESSION_MAX_AGE = 8 * 60 * 60


def hash_password(password: str, salt: bytes) -> bytes:
    return scrypt(password.encode(), salt=salt, n=2**15, r=8, p=3, maxmem=64 * 1024**2)


def _token_hash(token: str) -> bytes:
    return sha256(token.encode()).digest()


def start_session(email: str, password: str, previous_token: str | None) -> tuple[dict, str] | None:
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE email = ?", (email.strip().lower(),)
        ).fetchone()
        # 不存在的邮箱也执行一次哈希，让两类登录失败的处理一致。
        salt = row["password_salt"] if row else bytes(16)
        candidate = hash_password(password, salt)
        if row is None or not compare_digest(candidate, row["password_hash"]):
            return None

        token = token_urlsafe(32)
        now = int(time())
        with conn:
            conn.execute("DELETE FROM login_sessions WHERE expires_at <= ?", (now,))
            if previous_token:
                conn.execute(
                    "DELETE FROM login_sessions WHERE token_hash = ?", (_token_hash(previous_token),)
                )
            conn.execute(
                "INSERT INTO login_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (_token_hash(token), row["user_id"], now + SESSION_MAX_AGE),
            )
        return {key: row[key] for key in ("user_id", "email", "name")}, token


def get_account(token: str) -> dict | None:
    with closing(get_connection()) as conn:
        row = conn.execute(
            """
            SELECT accounts.user_id, accounts.email, accounts.name
            FROM login_sessions JOIN accounts USING (user_id)
            WHERE token_hash = ? AND expires_at > ?
            """,
            (_token_hash(token), int(time())),
        ).fetchone()
        return dict(row) if row else None


def end_session(token: str) -> None:
    with closing(get_connection()) as conn, conn:
        conn.execute("DELETE FROM login_sessions WHERE token_hash = ?", (_token_hash(token),))
