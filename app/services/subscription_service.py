import os
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Json


class InvalidPlanError(ValueError):
    pass


class SubscriptionConflictError(RuntimeError):
    pass


class UserResolutionError(RuntimeError):
    pass


_CREATE_SUBSCRIPTIONS_SQL = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    plan_id BIGINT NOT NULL REFERENCES plans(id),
    status TEXT NOT NULL CHECK (status IN ('active', 'trialing', 'past_due', 'canceled', 'paused')),
    current_period_start TIMESTAMPTZ NOT NULL,
    current_period_end TIMESTAMPTZ NOT NULL,
    canceled_at TIMESTAMPTZ NULL,
    external_subscription_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


_CREATE_USAGE_COUNTERS_SQL = """
CREATE TABLE IF NOT EXISTS usage_counters (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    metric TEXT NOT NULL,
    period_key TEXT NOT NULL,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
    limit_value INTEGER NOT NULL CHECK (limit_value >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, metric, period_key)
);
"""


_CREATE_USAGE_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS usage_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    metric TEXT NOT NULL,
    units INTEGER NOT NULL DEFAULT 1 CHECK (units > 0),
    event_type TEXT NOT NULL,
    resource_type TEXT NULL,
    resource_id TEXT NULL,
    period_key TEXT NOT NULL,
    idempotency_key TEXT NULL,
    metadata JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


_CREATE_USAGE_EVENTS_IDEMPOTENCY_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS usage_events_subscription_idempotency_idx
ON usage_events (user_id, metric, idempotency_key)
WHERE idempotency_key IS NOT NULL;
"""


_SELECT_USER_SQL = """
SELECT id
FROM "user"
WHERE clerk_user_id = %(clerk_user_id)s
LIMIT 1;
"""


_SELECT_PLAN_SQL = """
SELECT id, code, monthly_quote_limit
FROM plans
WHERE code = %(plan_code)s AND is_active = TRUE
LIMIT 1;
"""


_SELECT_ACTIVE_SUBSCRIPTION_SQL = """
SELECT s.id, s.status, p.code
FROM subscriptions s
JOIN plans p ON p.id = s.plan_id
WHERE s.user_id = %(user_id)s
  AND s.status IN ('active', 'trialing')
ORDER BY s.updated_at DESC
LIMIT 1
FOR UPDATE;
"""


_INSERT_SUBSCRIPTION_SQL = """
INSERT INTO subscriptions (
    user_id,
    plan_id,
    status,
    current_period_start,
    current_period_end,
    canceled_at,
    external_subscription_id
)
VALUES (
    %(user_id)s,
    %(plan_id)s,
    'active',
    %(current_period_start)s,
    %(current_period_end)s,
    NULL,
    NULL
)
RETURNING id;
"""


_UPDATE_SUBSCRIPTION_SQL = """
UPDATE subscriptions
SET
    plan_id = %(plan_id)s,
    status = 'active',
    current_period_start = %(current_period_start)s,
    current_period_end = %(current_period_end)s,
    canceled_at = NULL,
    updated_at = NOW()
WHERE id = %(subscription_id)s
RETURNING id;
"""


_UPSERT_USAGE_COUNTER_SQL = """
INSERT INTO usage_counters (
    user_id,
    metric,
    period_key,
    period_start,
    period_end,
    used,
    limit_value
)
VALUES (
    %(user_id)s,
    'quotes_created',
    %(period_key)s,
    %(period_start)s,
    %(period_end)s,
    0,
    %(limit_value)s
)
ON CONFLICT (user_id, metric, period_key)
DO UPDATE SET
    period_start = EXCLUDED.period_start,
    period_end = EXCLUDED.period_end,
    limit_value = EXCLUDED.limit_value,
    updated_at = NOW()
RETURNING used, limit_value;
"""


_INSERT_USAGE_EVENT_SQL = """
INSERT INTO usage_events (
    user_id,
    metric,
    units,
    event_type,
    resource_type,
    resource_id,
    period_key,
    idempotency_key,
    metadata
)
VALUES (
    %(user_id)s,
    'subscription_activation',
    1,
    'subscription_started',
    'subscription',
    %(resource_id)s,
    %(period_key)s,
    %(idempotency_key)s,
    %(metadata)s
);
"""


_SELECT_EXISTING_IDEMPOTENT_EVENT_SQL = """
SELECT id
FROM usage_events
WHERE user_id = %(user_id)s
  AND metric = 'subscription_activation'
  AND idempotency_key = %(idempotency_key)s
LIMIT 1;
"""


_SELECT_RESPONSE_SNAPSHOT_SQL = """
SELECT
    s.status,
    p.code AS plan_code,
        COALESCE(uc.limit_value, 0) AS limit_value,
        COALESCE(uc.used, 0) AS used,
    s.current_period_start,
    s.current_period_end
FROM subscriptions s
JOIN plans p ON p.id = s.plan_id
LEFT JOIN usage_counters uc
  ON uc.user_id = s.user_id
 AND uc.metric = 'quotes_created'
WHERE s.user_id = %(user_id)s
  AND s.status IN ('active', 'trialing')
ORDER BY s.updated_at DESC, uc.updated_at DESC NULLS LAST
LIMIT 1;
"""


def _build_subscription_response(
    status_value: str,
    plan_code: str,
    quota_limit: int,
    quota_used: int,
    current_period_start: datetime,
    current_period_end: datetime,
    idempotent_replay: bool,
) -> dict[str, Any]:
    quota_remaining = max(quota_limit - quota_used, 0)
    return {
        "subscription_status": status_value,
        "plan_code": plan_code,
        "quota_limit": quota_limit,
        "quota_used": quota_used,
        "quota_remaining": quota_remaining,
        "current_period_start": current_period_start.isoformat(),
        "current_period_end": current_period_end.isoformat(),
        "idempotent_replay": idempotent_replay,
    }


def subscribe_user_to_plan(
    clerk_user_id: str,
    plan_code: str,
    idempotency_key: str | None,
    source: str | None,
    client_timestamp: str | None,
) -> dict[str, Any]:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    normalized_plan_code = (plan_code or "").strip().upper()
    if not normalized_plan_code:
        raise InvalidPlanError("plan_code is required")

    now_utc = datetime.now(timezone.utc)
    period_start = now_utc
    period_end = now_utc + timedelta(days=30)
    period_key = now_utc.strftime("%Y-%m")

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SUBSCRIPTIONS_SQL)
            cur.execute(_CREATE_USAGE_COUNTERS_SQL)
            cur.execute(_CREATE_USAGE_EVENTS_SQL)
            cur.execute(_CREATE_USAGE_EVENTS_IDEMPOTENCY_INDEX_SQL)

            cur.execute(_SELECT_USER_SQL, {"clerk_user_id": clerk_user_id})
            user_row = cur.fetchone()
            if not user_row:
                raise UserResolutionError("Authenticated user is not provisioned")
            user_id = user_row[0]

            if idempotency_key:
                cur.execute(
                    _SELECT_EXISTING_IDEMPOTENT_EVENT_SQL,
                    {"user_id": user_id, "idempotency_key": idempotency_key},
                )
                existing_event = cur.fetchone()
                if existing_event:
                    cur.execute(
                        _SELECT_RESPONSE_SNAPSHOT_SQL,
                        {"user_id": user_id},
                    )
                    snapshot = cur.fetchone()
                    if snapshot:
                        return _build_subscription_response(
                            status_value=snapshot[0],
                            plan_code=snapshot[1],
                            quota_limit=int(snapshot[2] or 0),
                            quota_used=int(snapshot[3] or 0),
                            current_period_start=snapshot[4],
                            current_period_end=snapshot[5],
                            idempotent_replay=True,
                        )

            cur.execute(_SELECT_PLAN_SQL, {"plan_code": normalized_plan_code})
            plan_row = cur.fetchone()
            if not plan_row:
                raise InvalidPlanError("Invalid or inactive plan_code")

            plan_id, resolved_plan_code, monthly_quote_limit = plan_row

            cur.execute(_SELECT_ACTIVE_SUBSCRIPTION_SQL, {"user_id": user_id})
            active_subscription = cur.fetchone()

            subscription_id: int
            if active_subscription:
                existing_subscription_id, _, existing_plan_code = active_subscription
                if existing_plan_code != resolved_plan_code and existing_plan_code != "FREE":
                    raise SubscriptionConflictError(
                        "User already has an active paid subscription"
                    )

                cur.execute(
                    _UPDATE_SUBSCRIPTION_SQL,
                    {
                        "plan_id": plan_id,
                        "current_period_start": period_start,
                        "current_period_end": period_end,
                        "subscription_id": existing_subscription_id,
                    },
                )
                updated_subscription = cur.fetchone()
                subscription_id = int(updated_subscription[0])
            else:
                cur.execute(
                    _INSERT_SUBSCRIPTION_SQL,
                    {
                        "user_id": user_id,
                        "plan_id": plan_id,
                        "current_period_start": period_start,
                        "current_period_end": period_end,
                    },
                )
                inserted_subscription = cur.fetchone()
                subscription_id = int(inserted_subscription[0])

            cur.execute(
                _UPSERT_USAGE_COUNTER_SQL,
                {
                    "user_id": user_id,
                    "period_key": period_key,
                    "period_start": period_start,
                    "period_end": period_end,
                    "limit_value": int(monthly_quote_limit),
                },
            )
            counter_row = cur.fetchone()

            cur.execute(
                _INSERT_USAGE_EVENT_SQL,
                {
                    "user_id": user_id,
                    "resource_id": str(subscription_id),
                    "period_key": period_key,
                    "idempotency_key": idempotency_key,
                    "metadata": Json(
                        {
                            "source": source,
                            "client_timestamp": client_timestamp,
                            "plan_code": resolved_plan_code,
                        }
                    ),
                },
            )

        conn.commit()

    quota_used = int(counter_row[0] if counter_row else 0)
    quota_limit = int(counter_row[1] if counter_row else monthly_quote_limit)

    return _build_subscription_response(
        status_value="active",
        plan_code=resolved_plan_code,
        quota_limit=quota_limit,
        quota_used=quota_used,
        current_period_start=period_start,
        current_period_end=period_end,
        idempotent_replay=False,
    )
