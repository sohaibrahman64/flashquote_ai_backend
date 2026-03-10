import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Json


class InvalidQuoteRequestError(ValueError):
    pass


class QuoteInProgressError(RuntimeError):
    pass


class UserResolutionError(RuntimeError):
    pass


class QuotaExceededError(RuntimeError):
    pass


_CREATE_QUOTE_REQUESTS_SQL = """
CREATE TABLE IF NOT EXISTS quote_requests (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    request_id TEXT NULL,
    idempotency_key TEXT NULL,
    prompt TEXT NOT NULL,
    context_json JSONB NULL,
    output_format TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
    error_code TEXT NULL,
    error_message TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    UNIQUE (user_id, idempotency_key)
);
"""


_CREATE_AI_RUNS_SQL = """
CREATE TABLE IF NOT EXISTS ai_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    quote_request_id BIGINT NOT NULL REFERENCES quote_requests(id) ON DELETE CASCADE,
    provider TEXT NULL,
    model TEXT NULL,
    latency_ms INTEGER NULL,
    prompt_tokens INTEGER NULL,
    completion_tokens INTEGER NULL,
    total_tokens INTEGER NULL,
    cost_usd NUMERIC(12, 6) NULL,
    raw_response_json JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


_CREATE_QUOTES_SQL = """
CREATE TABLE IF NOT EXISTS quotes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    quote_request_id BIGINT NOT NULL UNIQUE REFERENCES quote_requests(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    title TEXT NULL,
    currency TEXT NULL,
    subtotal NUMERIC(14, 2) NULL,
    tax NUMERIC(14, 2) NULL,
    discount NUMERIC(14, 2) NULL,
    total NUMERIC(14, 2) NULL,
    assumptions_json JSONB NULL,
    quote_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


_CREATE_QUOTE_REQUESTS_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS quote_requests_user_status_created_idx
ON quote_requests (user_id, status, created_at DESC);
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


_SELECT_USER_SQL = """
SELECT id
FROM "user"
WHERE clerk_user_id = %(clerk_user_id)s
LIMIT 1;
"""


_SELECT_ALL_QUOTES_FOR_USER_SQL = """
SELECT
    q.id,
    q.quote_request_id,
    q.title,
    q.currency,
    q.subtotal,
    q.tax,
    q.discount,
    q.total,
    q.assumptions_json,
    q.quote_json,
    q.created_at,
    qr.status AS request_status,
    qr.prompt
FROM quotes q
JOIN quote_requests qr ON qr.id = q.quote_request_id
WHERE q.user_id = %(user_id)s
ORDER BY q.created_at DESC;
"""


_SELECT_REQUEST_BY_IDEMPOTENCY_SQL = """
SELECT id, status
FROM quote_requests
WHERE user_id = %(user_id)s
  AND idempotency_key = %(idempotency_key)s
LIMIT 1;
"""


_SELECT_REPLAY_RESPONSE_SQL = """
SELECT
    qr.id,
    q.id,
    q.quote_json,
    ar.provider,
    ar.model,
    ar.prompt_tokens,
    ar.completion_tokens,
    ar.total_tokens,
    ar.cost_usd
FROM quote_requests qr
JOIN quotes q ON q.quote_request_id = qr.id
LEFT JOIN ai_runs ar ON ar.quote_request_id = qr.id
WHERE qr.id = %(quote_request_id)s
ORDER BY ar.created_at DESC
LIMIT 1;
"""


_SELECT_ACTIVE_PLAN_QUOTA_SQL = """
SELECT p.monthly_quote_limit
FROM subscriptions s
JOIN plans p ON p.id = s.plan_id
WHERE s.user_id = %(user_id)s
    AND s.status IN ('active', 'trialing')
ORDER BY s.updated_at DESC
LIMIT 1;
"""


_SELECT_FREE_PLAN_QUOTA_SQL = """
SELECT monthly_quote_limit
FROM plans
WHERE code = 'FREE' AND is_active = TRUE
LIMIT 1;
"""


_UPSERT_MONTHLY_QUOTE_COUNTER_SQL = """
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
        updated_at = NOW();
"""


_RESERVE_MONTHLY_QUOTE_SLOT_SQL = """
UPDATE usage_counters
SET
        used = used + 1,
        updated_at = NOW()
WHERE user_id = %(user_id)s
    AND metric = 'quotes_created'
    AND period_key = %(period_key)s
    AND used < limit_value
RETURNING used, limit_value;
"""


_RELEASE_MONTHLY_QUOTE_SLOT_SQL = """
UPDATE usage_counters
SET
        used = GREATEST(used - 1, 0),
        updated_at = NOW()
WHERE user_id = %(user_id)s
    AND metric = 'quotes_created'
    AND period_key = %(period_key)s;
"""


_INSERT_QUOTE_REQUEST_SQL = """
INSERT INTO quote_requests (
    user_id,
    request_id,
    idempotency_key,
    prompt,
    context_json,
    output_format,
    status
)
VALUES (
    %(user_id)s,
    %(request_id)s,
    %(idempotency_key)s,
    %(prompt)s,
    %(context_json)s,
    %(output_format)s,
    'processing'
)
RETURNING id;
"""


_INSERT_AI_RUN_SQL = """
INSERT INTO ai_runs (
    quote_request_id,
    provider,
    model,
    latency_ms,
    prompt_tokens,
    completion_tokens,
    total_tokens,
    cost_usd,
    raw_response_json
)
VALUES (
    %(quote_request_id)s,
    %(provider)s,
    %(model)s,
    %(latency_ms)s,
    %(prompt_tokens)s,
    %(completion_tokens)s,
    %(total_tokens)s,
    %(cost_usd)s,
    %(raw_response_json)s
)
RETURNING id;
"""


_INSERT_QUOTE_SQL = """
INSERT INTO quotes (
    quote_request_id,
    user_id,
    title,
    currency,
    subtotal,
    tax,
    discount,
    total,
    assumptions_json,
    quote_json
)
VALUES (
    %(quote_request_id)s,
    %(user_id)s,
    %(title)s,
    %(currency)s,
    %(subtotal)s,
    %(tax)s,
    %(discount)s,
    %(total)s,
    %(assumptions_json)s,
    %(quote_json)s
)
RETURNING id;
"""


_MARK_QUOTE_REQUEST_COMPLETED_SQL = """
UPDATE quote_requests
SET
    status = 'completed',
    updated_at = NOW(),
    completed_at = NOW()
WHERE id = %(quote_request_id)s;
"""


_MARK_QUOTE_REQUEST_FAILED_SQL = """
UPDATE quote_requests
SET
    status = 'failed',
    error_code = %(error_code)s,
    error_message = %(error_message)s,
    updated_at = NOW()
WHERE id = %(quote_request_id)s;
"""


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ensure_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _extract_quote_amount(
    quote_payload: dict[str, Any],
    top_level_keys: tuple[str, ...],
    cost_summary_keys: tuple[str, ...],
) -> float | None:
    for key in top_level_keys:
        value = _to_float(quote_payload.get(key))
        if value is not None:
            return value

    cost_summary = _ensure_dict(quote_payload.get("cost_summary"))
    for key in cost_summary_keys:
        value = _to_float(cost_summary.get(key))
        if value is not None:
            return value

    return None


def _current_month_window(now_utc: datetime) -> tuple[str, datetime, datetime]:
    period_key = now_utc.strftime("%Y-%m")
    period_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period_start.month == 12:
        period_end = period_start.replace(year=period_start.year + 1, month=1)
    else:
        period_end = period_start.replace(month=period_start.month + 1)
    return period_key, period_start, period_end


def _resolve_monthly_quote_limit(cur: psycopg.Cursor[Any], user_id: int) -> int:
    cur.execute(_SELECT_ACTIVE_PLAN_QUOTA_SQL, {"user_id": user_id})
    active_plan = cur.fetchone()
    if active_plan and active_plan[0] is not None:
        return int(active_plan[0])

    cur.execute(_SELECT_FREE_PLAN_QUOTA_SQL)
    free_plan = cur.fetchone()
    if free_plan and free_plan[0] is not None:
        return int(free_plan[0])

    return 5


def _reserve_quote_quota_slot(
    cur: psycopg.Cursor[Any],
    user_id: int,
    period_key: str,
    period_start: datetime,
    period_end: datetime,
    limit_value: int,
) -> tuple[int, int]:
    cur.execute(
        _UPSERT_MONTHLY_QUOTE_COUNTER_SQL,
        {
            "user_id": user_id,
            "period_key": period_key,
            "period_start": period_start,
            "period_end": period_end,
            "limit_value": limit_value,
        },
    )
    cur.execute(
        _RESERVE_MONTHLY_QUOTE_SLOT_SQL,
        {
            "user_id": user_id,
            "period_key": period_key,
        },
    )
    reserved = cur.fetchone()
    if not reserved:
        raise QuotaExceededError("Monthly quote limit reached for current plan")

    used_after_reservation, resolved_limit = reserved
    return int(used_after_reservation), int(resolved_limit)


def _invoke_ai_agent(
    prompt: str,
    context: dict[str, Any],
    output_format: str,
    request_id: str | None,
) -> tuple[dict[str, Any], int]:
    load_dotenv()
    ai_agent_url = os.getenv("AI_AGENT_URL")
    ai_agent_api_key = os.getenv("AI_AGENT_API_KEY")

    if not ai_agent_url:
        raise RuntimeError("AI_AGENT_URL is not set")

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if ai_agent_api_key:
        headers["Authorization"] = f"Bearer {ai_agent_api_key}"
    if request_id:
        headers["X-Request-Id"] = request_id

    payload = {
        "prompt": prompt,
        "context": context,
        "output_format": output_format,
    }

    started = time.perf_counter()
    with httpx.Client(timeout=60.0) as client:
        response = client.post(ai_agent_url, json=payload, headers=headers)
        response.raise_for_status()
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        response_json = response.json()
        if not isinstance(response_json, dict):
            raise RuntimeError("AI Agent response must be a JSON object")

        return response_json, elapsed_ms


def _build_replay_response(row: tuple[Any, ...]) -> dict[str, Any]:
    quote_request_id, quote_id, quote_json, provider, model, prompt_tokens, completion_tokens, total_tokens, cost_usd = row

    return {
        "quote_request_id": int(quote_request_id),
        "quote_id": int(quote_id),
        "status": "completed",
        "idempotent_replay": True,
        "quote": quote_json,
        "ai_run": {
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": float(cost_usd) if cost_usd is not None else None,
        },
    }


def generate_quotation_for_user(
    clerk_user_id: str,
    prompt: str,
    context: dict[str, Any] | None,
    output_format: str,
    idempotency_key: str | None,
    request_id: str | None,
) -> dict[str, Any]:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    normalized_prompt = (prompt or "").strip()
    if not normalized_prompt:
        raise InvalidQuoteRequestError("prompt is required")

    normalized_output_format = (output_format or "quote_draft_v1").strip() or "quote_draft_v1"
    normalized_context = _ensure_dict(context)
    normalized_idempotency_key = (idempotency_key or "").strip() or None
    normalized_request_id = (request_id or "").strip() or None
    now_utc = datetime.now(timezone.utc)
    period_key, period_start, period_end = _current_month_window(now_utc)
    used_after_reservation = 0
    quota_limit = 0
    quota_reserved = False

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_QUOTE_REQUESTS_SQL)
            cur.execute(_CREATE_AI_RUNS_SQL)
            cur.execute(_CREATE_QUOTES_SQL)
            cur.execute(_CREATE_USAGE_COUNTERS_SQL)
            cur.execute(_CREATE_QUOTE_REQUESTS_STATUS_INDEX_SQL)

            cur.execute(_SELECT_USER_SQL, {"clerk_user_id": clerk_user_id})
            user_row = cur.fetchone()
            if not user_row:
                raise UserResolutionError("Authenticated user is not provisioned")
            user_id = int(user_row[0])

            if normalized_idempotency_key:
                cur.execute(
                    _SELECT_REQUEST_BY_IDEMPOTENCY_SQL,
                    {
                        "user_id": user_id,
                        "idempotency_key": normalized_idempotency_key,
                    },
                )
                existing = cur.fetchone()
                if existing:
                    existing_request_id, existing_status = existing
                    if existing_status == "processing":
                        raise QuoteInProgressError(
                            "A quote generation request with this Idempotency-Key is still processing"
                        )
                    if existing_status == "completed":
                        cur.execute(
                            _SELECT_REPLAY_RESPONSE_SQL,
                            {"quote_request_id": int(existing_request_id)},
                        )
                        replay_row = cur.fetchone()
                        if replay_row:
                            return _build_replay_response(replay_row)

            quota_limit = _resolve_monthly_quote_limit(cur, user_id)
            used_after_reservation, quota_limit = _reserve_quote_quota_slot(
                cur=cur,
                user_id=user_id,
                period_key=period_key,
                period_start=period_start,
                period_end=period_end,
                limit_value=quota_limit,
            )
            quota_reserved = True

            cur.execute(
                _INSERT_QUOTE_REQUEST_SQL,
                {
                    "user_id": user_id,
                    "request_id": normalized_request_id,
                    "idempotency_key": normalized_idempotency_key,
                    "prompt": normalized_prompt,
                    "context_json": Json(normalized_context),
                    "output_format": normalized_output_format,
                },
            )
            inserted_request = cur.fetchone()
            quote_request_id = int(inserted_request[0])
        conn.commit()

    try:
        ai_response, latency_ms = _invoke_ai_agent(
            prompt=normalized_prompt,
            context=normalized_context,
            output_format=normalized_output_format,
            request_id=normalized_request_id,
        )
    except Exception as exc:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _MARK_QUOTE_REQUEST_FAILED_SQL,
                    {
                        "quote_request_id": quote_request_id,
                        "error_code": "AI_AGENT_ERROR",
                        "error_message": str(exc),
                    },
                )
                if quota_reserved:
                    cur.execute(
                        _RELEASE_MONTHLY_QUOTE_SLOT_SQL,
                        {
                            "user_id": user_id,
                            "period_key": period_key,
                        },
                    )
            conn.commit()
        raise

    quote_payload = ai_response.get("quote") if isinstance(ai_response.get("quote"), dict) else ai_response
    usage_payload = _ensure_dict(ai_response.get("usage"))
    meta_payload = _ensure_dict(ai_response.get("meta"))

    title = str(
        normalized_context.get("project_title")
        or normalized_context.get("project_type")
        or "Generated Quote"
    )
    currency = (
        normalized_context.get("currency")
        or quote_payload.get("currency")
        or meta_payload.get("currency")
    )
    subtotal = _extract_quote_amount(
        quote_payload,
        top_level_keys=("subtotal",),
        cost_summary_keys=("subtotal",),
    )
    tax = _extract_quote_amount(
        quote_payload,
        top_level_keys=("tax", "tax_amount"),
        cost_summary_keys=("tax", "tax_amount"),
    )
    discount = _extract_quote_amount(
        quote_payload,
        top_level_keys=("discount", "discount_amount"),
        cost_summary_keys=("discount", "discount_amount"),
    )
    total = _extract_quote_amount(
        quote_payload,
        top_level_keys=("total", "grand_total"),
        cost_summary_keys=("total", "grand_total"),
    )
    assumptions = quote_payload.get("assumptions")

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                _INSERT_AI_RUN_SQL,
                {
                    "quote_request_id": quote_request_id,
                    "provider": meta_payload.get("provider") or ai_response.get("provider"),
                    "model": meta_payload.get("model") or ai_response.get("model"),
                    "latency_ms": latency_ms,
                    "prompt_tokens": usage_payload.get("prompt_tokens"),
                    "completion_tokens": usage_payload.get("completion_tokens"),
                    "total_tokens": usage_payload.get("total_tokens"),
                    "cost_usd": _to_float(usage_payload.get("cost_usd")),
                    "raw_response_json": Json(ai_response),
                },
            )

            cur.execute(
                _INSERT_QUOTE_SQL,
                {
                    "quote_request_id": quote_request_id,
                    "user_id": user_id,
                    "title": title,
                    "currency": currency,
                    "subtotal": subtotal,
                    "tax": tax,
                    "discount": discount,
                    "total": total,
                    "assumptions_json": Json(assumptions) if assumptions is not None else None,
                    "quote_json": Json(quote_payload),
                },
            )
            inserted_quote = cur.fetchone()
            quote_id = int(inserted_quote[0])

            cur.execute(
                _MARK_QUOTE_REQUEST_COMPLETED_SQL,
                {"quote_request_id": quote_request_id},
            )
        conn.commit()

    return {
        "quote_request_id": quote_request_id,
        "quote_id": quote_id,
        "status": "completed",
        "idempotent_replay": False,
        "quote": quote_payload,
        "ai_run": {
            "provider": meta_payload.get("provider") or ai_response.get("provider"),
            "model": meta_payload.get("model") or ai_response.get("model"),
            "latency_ms": latency_ms,
            "prompt_tokens": usage_payload.get("prompt_tokens"),
            "completion_tokens": usage_payload.get("completion_tokens"),
            "total_tokens": usage_payload.get("total_tokens"),
            "cost_usd": _to_float(usage_payload.get("cost_usd")),
        },
        "quota": {
            "period_key": period_key,
            "quota_limit": quota_limit,
            "quota_used": used_after_reservation,
            "quota_remaining": max(quota_limit - used_after_reservation, 0),
        },
    }


def get_all_quotes_for_user(clerk_user_id: str) -> list[dict[str, Any]]:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_SELECT_USER_SQL, {"clerk_user_id": clerk_user_id})
            row = cur.fetchone()
            if not row:
                raise UserResolutionError(
                    f"No provisioned user found for clerk_user_id={clerk_user_id}"
                )
            user_id = int(row[0])

            cur.execute(_SELECT_ALL_QUOTES_FOR_USER_SQL, {"user_id": user_id})
            rows = cur.fetchall()

    return [
        {
            "quote_id": int(r[0]),
            "quote_request_id": int(r[1]),
            "title": r[2],
            "currency": r[3],
            "subtotal": float(r[4]) if r[4] is not None else None,
            "tax": float(r[5]) if r[5] is not None else None,
            "discount": float(r[6]) if r[6] is not None else None,
            "total": float(r[7]) if r[7] is not None else None,
            "assumptions": r[8],
            "quote": r[9],
            "created_at": r[10].isoformat() if r[10] else None,
            "request_status": r[11],
            "prompt": r[12],
        }
        for r in rows
    ]
