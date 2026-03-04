from fastapi import APIRouter, Header, HTTPException, Request, status

from app.services.auth_service import is_user_signed_in
from app.services.user_storage_service import persist_user_login_payload

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/login")
async def login_user(
    request: Request,
    payload: dict,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    auth = payload.get("auth") or {}
    session_token = auth.get("sessionId")

    signed_in = is_user_signed_in(authorization, request)
    if not signed_in:
        return {"signed_in": False, "session_token": session_token}

    try:
        persist_user_login_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist user payload",
        ) from exc

    return {"signed_in": True, "session_token": session_token}
