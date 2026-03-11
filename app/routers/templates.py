from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.services.auth_service import (
    get_authenticated_clerk_user_id,
    is_user_signed_in,
)
from app.services.template_service import (
    DuplicateTemplateError,
    create_template,
    get_all_templates,
)

router = APIRouter(prefix="/api/templates", tags=["templates"])


class CreateTemplatePayload(BaseModel):
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    budget_range: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    modules: int = Field(gt=0)
    preset: dict[str, Any]


@router.get("", status_code=status.HTTP_200_OK)
async def list_templates():
    try:
        templates = get_all_templates()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch templates",
        ) from exc

    return {"templates": templates}


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_template(
    request: Request,
    payload: CreateTemplatePayload,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
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

    try:
        template = create_template(
            name=payload.name,
            category=payload.category,
            budget_range=payload.budget_range,
            summary=payload.summary,
            modules=payload.modules,
            preset=payload.preset,
        )
    except DuplicateTemplateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create template",
        ) from exc

    return {"template": template}
