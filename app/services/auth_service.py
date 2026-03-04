import os

from clerk_backend_api import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions
from dotenv import load_dotenv
import httpx
from fastapi import Request

def extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None

    parts = authorization.strip().split(None, 1)
    if len(parts) != 2:
        return None

    scheme = parts[0].rstrip(":").lower()
    token = parts[1].strip()

    if scheme != "bearer" or not token:
        return None

    return token



def _build_httpx_request(request: Request, token: str) -> httpx.Request:
    headers = dict(request.headers)
    headers["authorization"] = f"Bearer {token}"

    return httpx.Request(
        method=request.method,
        url=str(request.url),
        headers=headers,
    )


def _authenticate_request_state(token: str, request: Request):
    load_dotenv()
    secret_key = os.getenv("PYTHON_APP_CLERK_SECRET_KEY") or os.getenv("CLERK_SECRET_KEY")
    if not secret_key:
        return None

    sdk = Clerk(bearer_auth=secret_key)
    httpx_request = _build_httpx_request(request, token)
    options = AuthenticateRequestOptions(secret_key=secret_key)

    try:
        return sdk.authenticate_request(httpx_request, options)
    except Exception:
        return None


def _extract_sub_from_claims(claims: object) -> str | None:
    if isinstance(claims, dict):
        value = claims.get("sub")
        return str(value) if value else None

    value = getattr(claims, "sub", None)
    return str(value) if value else None


def verify_token_with_clerk(token: str, request: Request) -> bool:
    request_state = _authenticate_request_state(token, request)
    if not request_state:
        return False

    return bool(getattr(request_state, "is_signed_in", False))


def is_user_signed_in(authorization: str | None, request: Request) -> bool:
    token = extract_bearer_token(authorization)
    if not token:
        return False

    return verify_token_with_clerk(token, request)


def get_authenticated_clerk_user_id(
    authorization: str | None, request: Request
) -> str | None:
    token = extract_bearer_token(authorization)
    if not token:
        return None

    request_state = _authenticate_request_state(token, request)
    if not request_state or not getattr(request_state, "is_signed_in", False):
        return None

    user_id = getattr(request_state, "user_id", None)
    if user_id:
        return str(user_id)

    claims_candidates = (
        getattr(request_state, "payload", None),
        getattr(request_state, "claims", None),
        getattr(request_state, "jwt_claims", None),
    )

    for claims in claims_candidates:
        sub = _extract_sub_from_claims(claims)
        if sub:
            return sub

    return None
