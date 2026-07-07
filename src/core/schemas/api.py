"""Pydantic request/response models for API validation."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ── Projects ──

class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    platform_type: str = Field(..., min_length=1)
    platform_config: Optional[dict] = None


class UpdateProjectRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    platform_type: Optional[str] = None
    platform_config: Optional[dict] = None
    status: Optional[str] = None


# ── Pipelines ──

class CreatePipelineRequest(BaseModel):
    custom_prompt: str = ""
    document_ids: List[str] = Field(..., min_length=1)
