from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.services.auth_service import (
    get_authenticated_clerk_user_id,
    is_user_signed_in,
)
from app.services.settings_service import (
    UserResolutionError,
    get_user_settings,
    upsert_user_settings,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class UpdateSettingsPayload(BaseModel):
    workspace_name: str | None = Field(default=None)
    notification_email: bool = Field(default=True)
    timezone: Literal["UTC", "IST", "EST", "PST"] = Field(default="UTC")
    default_pricing_model: Literal["Fixed", "Hourly", "Milestone-based"] = Field(default="Fixed")
    currency: Literal["USD", "EUR", "INR", "GBP"] = Field(default="USD")
    default_quote_validity_days: int = Field(default=30, gt=0)


def _authenticate(
    authorization: str | None, request: Request
) -> str:
    if not is_user_signed_in(authorization, request):
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
    return clerk_user_id


@router.get("", status_code=status.HTTP_200_OK)
async def get_settings(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    clerk_user_id = _authenticate(authorization, request)

    try:
        settings = get_user_settings(clerk_user_id=clerk_user_id)
    except UserResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch settings",
        ) from exc

    return {"settings": settings}


@router.put("", status_code=status.HTTP_200_OK)
async def update_settings(
    request: Request,
    payload: UpdateSettingsPayload,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    clerk_user_id = _authenticate(authorization, request)

    try:
        settings = upsert_user_settings(
            clerk_user_id=clerk_user_id,
            workspace_name=payload.workspace_name,
            notification_email=payload.notification_email,
            timezone=payload.timezone,
            default_pricing_model=payload.default_pricing_model,
            currency=payload.currency,
            default_quote_validity_days=payload.default_quote_validity_days,
        )
    except UserResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update settings",
        ) from exc

    return {"settings": settings}
