import os
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Json
from dotenv import load_dotenv


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users_login (
    clerk_user_id TEXT PRIMARY KEY,
    session_id TEXT,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    full_name TEXT,
    primary_email_address TEXT,
    image_url TEXT,
    user_created_at TIMESTAMPTZ NULL,
    user_updated_at TIMESTAMPTZ NULL,
    payload_json JSONB NOT NULL,
    last_login_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


_UPSERT_SQL = """
INSERT INTO users_login (
    clerk_user_id,
    session_id,
    username,
    first_name,
    last_name,
    full_name,
    primary_email_address,
    image_url,
    user_created_at,
    user_updated_at,
    payload_json,
    last_login_at
)
VALUES (
    %(clerk_user_id)s,
    %(session_id)s,
    %(username)s,
    %(first_name)s,
    %(last_name)s,
    %(full_name)s,
    %(primary_email_address)s,
    %(image_url)s,
    %(user_created_at)s,
    %(user_updated_at)s,
    %(payload_json)s,
    NOW()
)
ON CONFLICT (clerk_user_id)
DO UPDATE SET
    session_id = EXCLUDED.session_id,
    username = EXCLUDED.username,
    first_name = EXCLUDED.first_name,
    last_name = EXCLUDED.last_name,
    full_name = EXCLUDED.full_name,
    primary_email_address = EXCLUDED.primary_email_address,
    image_url = EXCLUDED.image_url,
    user_created_at = EXCLUDED.user_created_at,
    user_updated_at = EXCLUDED.user_updated_at,
    payload_json = EXCLUDED.payload_json,
    last_login_at = NOW();
"""


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def persist_user_login_payload(payload: dict[str, Any]) -> None:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    create_users_sql = """
    CREATE TABLE IF NOT EXISTS "user" (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        clerk_user_id TEXT NOT NULL UNIQUE,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        full_name TEXT,
        primary_email_address TEXT,
        image_url TEXT,
        user_created_at TIMESTAMPTZ NULL,
        user_updated_at TIMESTAMPTZ NULL
    );
    """

    create_user_sessions_sql = """
    CREATE TABLE IF NOT EXISTS user_sessions (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
        session_id TEXT UNIQUE
    );
    """

    upsert_user_sql = """
    INSERT INTO "user" (
        clerk_user_id,
        username,
        first_name,
        last_name,
        full_name,
        primary_email_address,
        image_url,
        user_created_at,
        user_updated_at
    )
    VALUES (
        %(clerk_user_id)s,
        %(username)s,
        %(first_name)s,
        %(last_name)s,
        %(full_name)s,
        %(primary_email_address)s,
        %(image_url)s,
        %(user_created_at)s,
        %(user_updated_at)s
    )
    ON CONFLICT (clerk_user_id)
    DO UPDATE SET
        username = EXCLUDED.username,
        first_name = EXCLUDED.first_name,
        last_name = EXCLUDED.last_name,
        full_name = EXCLUDED.full_name,
        primary_email_address = EXCLUDED.primary_email_address,
        image_url = EXCLUDED.image_url,
        user_created_at = EXCLUDED.user_created_at,
        user_updated_at = EXCLUDED.user_updated_at
    RETURNING id;
    """

    upsert_user_session_sql = """
    INSERT INTO user_sessions (user_id, session_id)
    VALUES (%(user_id)s, %(session_id)s)
    ON CONFLICT (session_id)
    DO UPDATE SET
        user_id = EXCLUDED.user_id;
    """

    auth = payload.get("auth") or {}
    user = payload.get("user") or {}

    clerk_user_id = user.get("id") or auth.get("userId")
    if not clerk_user_id:
        raise ValueError("Payload missing user id")

    user_row = {
        "clerk_user_id": clerk_user_id,
        "username": user.get("username"),
        "first_name": user.get("firstName"),
        "last_name": user.get("lastName"),
        "full_name": user.get("fullName"),
        "primary_email_address": user.get("primaryEmailAddress"),
        "image_url": user.get("imageUrl"),
        "user_created_at": _parse_iso_datetime(user.get("createdAt")),
        "user_updated_at": _parse_iso_datetime(user.get("updatedAt")),
    }

    session_id = auth.get("sessionId")

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(create_users_sql)
            cur.execute(create_user_sessions_sql)
            cur.execute(upsert_user_sql, user_row)
            persisted_user_id = cur.fetchone()

            if session_id and persisted_user_id:
                cur.execute(
                    upsert_user_session_sql,
                    {"user_id": persisted_user_id[0], "session_id": session_id},
                )
        conn.commit()
