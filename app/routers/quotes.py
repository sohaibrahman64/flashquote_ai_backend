from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.auth_service import (
    get_authenticated_clerk_user_id,
    is_user_signed_in,
)
from app.services.quotation_service import (
    InvalidQuoteRequestError,
    QuotaExceededError,
    QuoteInProgressError,
    UserResolutionError,
    generate_quotation_for_user,
)

router = APIRouter(prefix="/api/quotes", tags=["quotes"])


class GenerateQuotationPayload(BaseModel):
    prompt: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    output_format: str = Field(default="quote_draft_v1")


@router.post("/generate", status_code=status.HTTP_201_CREATED)
async def generate_quotation(
    request: Request,
    payload: GenerateQuotationPayload,
    authorization: str | None = Header(default=None, alias="Authorization"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
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
        result = generate_quotation_for_user(
            clerk_user_id=clerk_user_id,
            prompt=payload.prompt,
            context=payload.context,
            output_format=payload.output_format,
            idempotency_key=idempotency_key,
            request_id=x_request_id,
        )
    except InvalidQuoteRequestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except QuoteInProgressError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UserResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except QuotaExceededError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate quote",
        ) from exc

    if result.get("idempotent_replay"):
        return JSONResponse(status_code=status.HTTP_200_OK, content=result)

    return result
