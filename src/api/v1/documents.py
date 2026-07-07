"""Document CRUD API endpoints."""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.models import Document
from src.utils.file_storage import save

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])


@router.post("")
async def upload_document(
    project_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()

    # Determine file type from extension
    ext = Path(file.filename).suffix.lower()
    type_map = {
        ".pdf": "pdf", ".docx": "docx", ".doc": "doc", ".md": "md", ".txt": "txt",
        ".json": "openapi_json", ".yaml": "openapi_yaml", ".yml": "openapi_yaml",
        ".xlsx": "xlsx",
    }
    file_type = type_map.get(ext, "txt")

    # Save to storage
    rel_path = f"documents/{project_id}/{file.filename}"
    await save(rel_path, content)

    doc = Document(
        project_id=project_id,
        filename=file.filename,
        file_type=file_type,
        file_path=rel_path,
        status="uploaded",
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    return {"success": True, "data": _serialize(doc), "error": None}


@router.get("")
async def list_documents(
    project_id: str,
    status: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(Document).where(Document.project_id == project_id)
    if status:
        query = query.where(Document.status == status)

    result = await db.execute(query)
    docs = result.scalars().all()
    return {"success": True, "data": [_serialize(d) for d in docs], "error": None}


@router.get("/{document_id}")
async def get_document(project_id: str, document_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        return {"success": False, "data": None, "error": "Not found"}
    return {"success": True, "data": _serialize(doc), "error": None}


@router.delete("/{document_id}")
async def delete_document(project_id: str, document_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        return {"success": False, "data": None, "error": "Not found"}
    await db.delete(doc)
    await db.commit()
    return {"success": True, "data": None, "error": None}


def _serialize(d: Document) -> dict:
    return {
        "id": d.id,
        "project_id": d.project_id,
        "filename": d.filename,
        "file_type": d.file_type,
        "file_path": d.file_path,
        "status": d.status,
        "error_message": d.error_message,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }
