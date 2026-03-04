from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.services.auth_service import (
    get_authenticated_clerk_user_id,
    is_user_signed_in,
)
from app.services.subscription_service import (
    InvalidPlanError,
    SubscriptionConflictError,
    UserResolutionError,
    subscribe_user_to_plan,
)

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe_user(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    signed_in = is_user_signed_in(authorization, request)
    if not signed_in:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthenticated request",
        )

    clerk_user_id = get_authenticated_clerk_user_id(authorization, request)
    if not clerk_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to resolve authenticated user",
        )

    try:
        result = subscribe_user_to_plan(
            clerk_user_id=clerk_user_id,
            plan_code=str(payload.get("plan_code") or ""),
            idempotency_key=(payload.get("idempotency_key") or None),
            source=(payload.get("source") or None),
            client_timestamp=(payload.get("client_timestamp") or None),
        )
    except InvalidPlanError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except SubscriptionConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UserResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to subscribe user",
        ) from exc

    if result.get("idempotent_replay"):
        return JSONResponse(status_code=status.HTTP_200_OK, content=result)

    return result
