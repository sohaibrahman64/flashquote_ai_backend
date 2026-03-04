# FlashQuote Backend

FastAPI backend service for user login persistence and subscription activation with Clerk-based authentication and PostgreSQL storage.

## Features

- FastAPI app with CORS support
- Clerk token verification for protected endpoints
- User login payload persistence into PostgreSQL
- Subscription activation flow with idempotency support
- Usage counter and usage event tracking for subscription lifecycle

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
    │   └── subscriptions.py
    └── services/
        ├── auth_service.py
        ├── user_storage_service.py
        └── subscription_service.py
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

## Database Notes

The service auto-creates these tables if missing:

- `user`
- `user_sessions`
- `subscriptions`
- `usage_counters`
- `usage_events`

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

## Error Semantics

- `400` when login payload is invalid (e.g., missing user id)
- `401` for unauthenticated subscription requests or unresolved authenticated user
- `409` when an active paid subscription already exists on another paid plan
- `422` for invalid/inactive or missing `plan_code`
- `500` for unexpected server/database failures

## Development Notes

- CORS origins are configured from `CORS_ALLOW_ORIGINS` (comma-separated), default `*`.
- When `*` is used, credentials are disabled by design in middleware configuration.
