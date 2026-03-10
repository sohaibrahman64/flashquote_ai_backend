# FlashQuote Backend

FastAPI backend service for user login persistence, subscription activation, and AI-based quote generation with Clerk-based authentication and PostgreSQL storage.

## Features

- FastAPI app with CORS support
- Clerk token verification for protected endpoints
- User login payload persistence into PostgreSQL
- Subscription activation flow with idempotency support
- Usage counter and usage event tracking for subscription lifecycle
- AI quote generation endpoint with prompt forwarding to an AI agent
- Quote request, AI run, and generated quote persistence

## Tech Stack

- Python
- FastAPI
- `clerk-backend-api`
- PostgreSQL via `psycopg`
- `python-dotenv`

## Project Structure

```text
.
├── main.py
├── requirements.txt
└── app/
    ├── routers/
    │   ├── users.py
  │   ├── subscriptions.py
  │   └── quotes.py
    └── services/
        ├── auth_service.py
        ├── user_storage_service.py
    ├── subscription_service.py
    └── quotation_service.py
```

## Environment Variables

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql://username:password@localhost:5432/your_db
CLERK_SECRET_KEY=sk_test_xxx
# Optional alias used by code before CLERK_SECRET_KEY:
# PYTHON_APP_CLERK_SECRET_KEY=sk_test_xxx

# Optional CORS config (comma-separated). Default is "*"
# CORS_ALLOW_ORIGINS=http://localhost:3000,http://localhost:5173

# AI Agent endpoint consumed by /api/quotes/generate
AI_AGENT_URL=https://your-ai-agent.example.com/generate
# Optional bearer key forwarded to AI agent
# AI_AGENT_API_KEY=your_ai_agent_api_key
```

## Installation

```bash
pip install -r requirements.txt
```

> Note: To run locally with `uvicorn`, ensure it is installed in your environment.

```bash
pip install uvicorn
```

## Run the API

```bash
uvicorn main:app --reload
```

App base URL: `http://127.0.0.1:8000`

## API Endpoints

### Health / Root

- `GET /`
- Response:

```json
{ "message": "Hello World" }
```

### User Login Persistence

- `POST /api/users/login`
- Purpose: verifies auth token with Clerk and stores/updates user + session records.
- Header:
  - `Authorization: Bearer <token>`
- Body (example):

```json
{
  "auth": {
    "sessionId": "sess_123",
    "userId": "user_123"
  },
  "user": {
    "id": "user_123",
    "username": "jane",
    "firstName": "Jane",
    "lastName": "Doe",
    "fullName": "Jane Doe",
    "primaryEmailAddress": "jane@example.com",
    "imageUrl": "https://...",
    "createdAt": "2026-03-04T10:00:00Z",
    "updatedAt": "2026-03-04T10:00:00Z"
  }
}
```

- Success response:

```json
{ "signed_in": true, "session_token": "sess_123" }
```

- If token is missing/invalid:

```json
{ "signed_in": false, "session_token": "sess_123" }
```

### Subscribe User to Plan

- `POST /api/subscriptions/subscribe`
- Purpose: activates or updates a user subscription and initializes monthly usage counters.
- Header:
  - `Authorization: Bearer <token>`
- Body:

```json
{
  "plan_code": "PRO",
  "idempotency_key": "unique-request-key-001",
  "source": "web_checkout",
  "client_timestamp": "2026-03-04T10:15:00Z"
}
```

- Success (`201 Created` for new processing, `200 OK` for idempotent replay):

```json
{
  "subscription_status": "active",
  "plan_code": "PRO",
  "quota_limit": 100,
  "quota_used": 0,
  "quota_remaining": 100,
  "current_period_start": "2026-03-04T10:15:00+00:00",
  "current_period_end": "2026-04-03T10:15:00+00:00",
  "idempotent_replay": false
}
```

### Generate AI Quote Draft

- `POST /api/quotes/generate`
- Purpose: validates authenticated user, extracts `prompt` from payload, forwards it to the configured AI Agent, and stores request/run/quote records.
- Headers:
  - `Authorization: Bearer <clerk_token>`
  - `Idempotency-Key: <unique-request-key>` (optional but recommended)
  - `X-Request-Id: <trace-id>` (optional)
- Body (example):

```json
{
  "prompt": "Project: Web App MVP\nClient: Acme Retail\nScope: Landing page, user auth, dashboard, profile settings, and admin panel with basic analytics.\nTimeline: 6 weeks\nBudget: $3,500\nPricing: Milestone Based\nTerms: 40% upfront, 30% midpoint, 30% on handover",
  "context": {
    "client_name": "Acme Retail",
    "project_type": "Web App MVP",
    "currency": "USD",
    "default_pricing_model": "Fixed",
    "project_title": "Web App MVP",
    "scope_summary": "Landing page, user auth, dashboard, profile settings, and admin panel with basic analytics.",
    "timeline": "6 weeks",
    "budget": "$3,500",
    "pricing_model": "Fixed",
    "terms": "40% upfront, 30% midpoint, 30% on handover"
  },
  "output_format": "quote_draft_v1"
}
```

- Success response (`201 Created`, or `200 OK` if idempotent replay):

```json
{
  "quote_request_id": 101,
  "quote_id": 501,
  "status": "completed",
  "idempotent_replay": false,
  "quote": {
    "currency": "USD",
    "subtotal": 3200,
    "tax": 0,
    "discount": 0,
    "total": 3200,
    "assumptions": []
  },
  "ai_run": {
    "provider": "openai",
    "model": "gpt-4.1",
    "latency_ms": 842,
    "prompt_tokens": 640,
    "completion_tokens": 410,
    "total_tokens": 1050,
    "cost_usd": 0.021
  }
}
```

#### AI Agent Response Contract

`AI_AGENT_URL` should return a JSON object. Recommended shape:

```json
{
  "quote": {
    "currency": "USD",
    "subtotal": 3200,
    "tax": 0,
    "discount": 0,
    "total": 3200,
    "assumptions": []
  },
  "usage": {
    "prompt_tokens": 640,
    "completion_tokens": 410,
    "total_tokens": 1050,
    "cost_usd": 0.021
  },
  "meta": {
    "provider": "openai",
    "model": "gpt-4.1"
  }
}
```

## Database Notes

The service auto-creates these tables if missing:

- `user`
- `user_sessions`
- `subscriptions`
- `usage_counters`
- `usage_events`
- `quote_requests`
- `ai_runs`
- `quotes`

### Required Pre-existing Table

`plans` must exist and contain at least:

- `id`
- `code`
- `monthly_quote_limit`
- `is_active`

The subscribe flow resolves `plan_code` from this table and rejects invalid/inactive plans.

## Auth Behavior

- Bearer token is extracted from `Authorization` header.
- Clerk request authentication is used to determine `is_signed_in`.
- `/api/subscriptions/subscribe` requires a signed-in user and a resolvable Clerk user ID.
- `/api/quotes/generate` requires a signed-in user and a resolvable Clerk user ID.

## Error Semantics

- `400` when login payload is invalid (e.g., missing user id)
- `401` for unauthenticated subscription requests or unresolved authenticated user
- `409` when an active paid subscription already exists on another paid plan
- `422` for invalid/inactive or missing `plan_code`
- `400` when quote payload is invalid (e.g., missing prompt)
- `409` when same `Idempotency-Key` is already processing
- `500` for unexpected server/database failures

## Development Notes

- CORS origins are configured from `CORS_ALLOW_ORIGINS` (comma-separated), default `*`.
- When `*` is used, credentials are disabled by design in middleware configuration.
