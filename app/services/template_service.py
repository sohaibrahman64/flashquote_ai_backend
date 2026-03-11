import os
from typing import Any

import psycopg
from psycopg.types.json import Json
from dotenv import load_dotenv


_CREATE_TEMPLATES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS templates (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    budget_range TEXT NOT NULL,
    summary TEXT NOT NULL,
    modules INTEGER NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    preset JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


_SEED_TEMPLATE_SQL = """
INSERT INTO templates (name, category, budget_range, summary, modules, is_default, preset)
VALUES (%(name)s, %(category)s, %(budget_range)s, %(summary)s, %(modules)s, %(is_default)s, %(preset)s)
ON CONFLICT (name) DO NOTHING;
"""


_SELECT_ALL_TEMPLATES_SQL = """
SELECT id, name, category, budget_range, summary, modules, is_default, preset, created_at
FROM templates
ORDER BY id;
"""


_INSERT_TEMPLATE_SQL = """
INSERT INTO templates (name, category, budget_range, summary, modules, is_default, preset)
VALUES (%(name)s, %(category)s, %(budget_range)s, %(summary)s, %(modules)s, %(is_default)s, %(preset)s)
RETURNING id, name, category, budget_range, summary, modules, is_default, preset, created_at;
"""


_SEED_DATA: list[dict[str, Any]] = [
    {
        "name": "Web App MVP",
        "category": "Web App",
        "budget_range": "$2,000 - $5,000",
        "summary": "Landing page, auth, dashboard, and admin basics.",
        "modules": 8,
        "is_default": True,
        "preset": {
            "clientName": "",
            "projectTitle": "Web App MVP",
            "scopeSummary": "Landing page, user auth, dashboard, profile settings, and admin panel with basic analytics.",
            "timeline": "6 weeks",
            "budget": "$3,500",
            "pricingModel": "Milestone-based",
            "terms": "40% upfront, 30% midpoint, 30% on handover",
        },
    },
    {
        "name": "Mobile App Launch",
        "category": "Mobile App",
        "budget_range": "$3,000 - $7,000",
        "summary": "Cross-platform app with API integration and release support.",
        "modules": 10,
        "is_default": True,
        "preset": {
            "clientName": "",
            "projectTitle": "Mobile App Launch",
            "scopeSummary": "React Native app, auth, profile, push notifications, backend API integration, and app store submission support.",
            "timeline": "8 weeks",
            "budget": "$5,200",
            "pricingModel": "Fixed",
            "terms": "50% upfront, 50% before production release",
        },
    },
    {
        "name": "Maintenance Retainer",
        "category": "Maintenance",
        "budget_range": "$800 - $2,000",
        "summary": "Monthly bug fixes, performance checks, and minor enhancements.",
        "modules": 5,
        "is_default": True,
        "preset": {
            "clientName": "",
            "projectTitle": "Monthly Maintenance Retainer",
            "scopeSummary": "Bug fixes, dependency updates, uptime monitoring, monthly performance optimization, and minor feature requests.",
            "timeline": "Monthly",
            "budget": "$1,200",
            "pricingModel": "Hourly",
            "terms": "Billed monthly with 20-hour cap",
        },
    },
]


def _ensure_table_and_seed(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TEMPLATES_TABLE_SQL)
            for template in _SEED_DATA:
                cur.execute(
                    _SEED_TEMPLATE_SQL,
                    {
                        "name": template["name"],
                        "category": template["category"],
                        "budget_range": template["budget_range"],
                        "summary": template["summary"],
                        "modules": template["modules"],
                        "is_default": template["is_default"],
                        "preset": Json(template["preset"]),
                    },
                )
        conn.commit()


class DuplicateTemplateError(RuntimeError):
    pass


def _row_to_dict(r: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": int(r[0]),
        "name": r[1],
        "category": r[2],
        "budget_range": r[3],
        "summary": r[4],
        "modules": r[5],
        "is_default": r[6],
        "preset": r[7],
        "created_at": r[8].isoformat() if r[8] else None,
    }


def create_template(
    name: str,
    category: str,
    budget_range: str,
    summary: str,
    modules: int,
    preset: dict[str, Any],
) -> dict[str, Any]:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    _ensure_table_and_seed(database_url)

    try:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _INSERT_TEMPLATE_SQL,
                    {
                        "name": name,
                        "category": category,
                        "budget_range": budget_range,
                        "summary": summary,
                        "modules": modules,
                        "is_default": False,
                        "preset": Json(preset),
                    },
                )
                row = cur.fetchone()
            conn.commit()
    except psycopg.errors.UniqueViolation as exc:
        raise DuplicateTemplateError(
            f"A template with the name '{name}' already exists"
        ) from exc

    return _row_to_dict(row)


def get_all_templates() -> list[dict[str, Any]]:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    _ensure_table_and_seed(database_url)

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_SELECT_ALL_TEMPLATES_SQL)
            rows = cur.fetchall()

    return [_row_to_dict(r) for r in rows]
