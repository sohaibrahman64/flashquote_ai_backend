import os
from typing import Any

import psycopg
from dotenv import load_dotenv


_CREATE_SETTINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES "user"(id) ON DELETE CASCADE,
    workspace_name TEXT NULL,
    notification_email BOOLEAN NOT NULL DEFAULT TRUE,
    timezone TEXT NOT NULL DEFAULT 'UTC' CHECK (timezone IN ('UTC', 'IST', 'EST', 'PST')),
    default_pricing_model TEXT NOT NULL DEFAULT 'Fixed' CHECK (default_pricing_model IN ('Fixed', 'Hourly', 'Milestone-based')),
    currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency IN ('USD', 'EUR', 'INR', 'GBP')),
    default_quote_validity_days INTEGER NOT NULL DEFAULT 30,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


_SELECT_USER_SQL = """
SELECT id
FROM "user"
WHERE clerk_user_id = %(clerk_user_id)s
LIMIT 1;
"""


_SELECT_SETTINGS_SQL = """
SELECT id, user_id, workspace_name, notification_email, timezone,
       default_pricing_model, currency, default_quote_validity_days,
       created_at, updated_at
FROM settings
WHERE user_id = %(user_id)s
LIMIT 1;
"""


_UPSERT_SETTINGS_SQL = """
INSERT INTO settings (
    user_id, workspace_name, notification_email, timezone,
    default_pricing_model, currency, default_quote_validity_days
)
VALUES (
    %(user_id)s, %(workspace_name)s, %(notification_email)s, %(timezone)s,
    %(default_pricing_model)s, %(currency)s, %(default_quote_validity_days)s
)
ON CONFLICT (user_id)
DO UPDATE SET
    workspace_name = EXCLUDED.workspace_name,
    notification_email = EXCLUDED.notification_email,
    timezone = EXCLUDED.timezone,
    default_pricing_model = EXCLUDED.default_pricing_model,
    currency = EXCLUDED.currency,
    default_quote_validity_days = EXCLUDED.default_quote_validity_days,
    updated_at = NOW()
RETURNING id, user_id, workspace_name, notification_email, timezone,
          default_pricing_model, currency, default_quote_validity_days,
          created_at, updated_at;
"""


class UserResolutionError(RuntimeError):
    pass


def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "user_id": int(row[1]),
        "workspace_name": row[2],
        "notification_email": row[3],
        "timezone": row[4],
        "default_pricing_model": row[5],
        "currency": row[6],
        "default_quote_validity_days": row[7],
        "created_at": row[8].isoformat() if row[8] else None,
        "updated_at": row[9].isoformat() if row[9] else None,
    }


def _get_db_url() -> str:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return database_url


def _resolve_user(cur: psycopg.Cursor[Any], clerk_user_id: str) -> int:
    cur.execute(_SELECT_USER_SQL, {"clerk_user_id": clerk_user_id})
    row = cur.fetchone()
    if not row:
        raise UserResolutionError(
            f"No provisioned user found for clerk_user_id={clerk_user_id}"
        )
    return int(row[0])


def get_user_settings(clerk_user_id: str) -> dict[str, Any] | None:
    database_url = _get_db_url()

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SETTINGS_TABLE_SQL)
            conn.commit()

            user_id = _resolve_user(cur, clerk_user_id)

            cur.execute(_SELECT_SETTINGS_SQL, {"user_id": user_id})
            row = cur.fetchone()

    if not row:
        return None
    return _row_to_dict(row)


def upsert_user_settings(
    clerk_user_id: str,
    workspace_name: str | None,
    notification_email: bool,
    timezone: str,
    default_pricing_model: str,
    currency: str,
    default_quote_validity_days: int,
) -> dict[str, Any]:
    database_url = _get_db_url()

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SETTINGS_TABLE_SQL)
            conn.commit()

            user_id = _resolve_user(cur, clerk_user_id)

            cur.execute(
                _UPSERT_SETTINGS_SQL,
                {
                    "user_id": user_id,
                    "workspace_name": workspace_name,
                    "notification_email": notification_email,
                    "timezone": timezone,
                    "default_pricing_model": default_pricing_model,
                    "currency": currency,
                    "default_quote_validity_days": default_quote_validity_days,
                },
            )
            row = cur.fetchone()
        conn.commit()

    return _row_to_dict(row)
