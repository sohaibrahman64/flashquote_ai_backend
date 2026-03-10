from fastapi import APIRouter, HTTPException, status

from app.services.template_service import get_all_templates

router = APIRouter(prefix="/api/templates", tags=["templates"])


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
