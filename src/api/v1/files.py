"""File download endpoint — serves files from storage/ by relative path."""

from io import BytesIO

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from src.utils.file_storage import read

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/download")
async def download_file(path: str = Query(..., description="Relative path within storage/")):
    """Download a file from the storage directory by relative path."""
    content = await read(path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    filename = path.split("/")[-1]
    return StreamingResponse(
        BytesIO(content),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
