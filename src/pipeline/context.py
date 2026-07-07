"""Pipeline context — typed data bus shared across pipeline stages.

Serialized to JSON for database persistence and pause/resume support.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineContext:
    """Data bus that passes through all pipeline stages.

    Each stage reads what it needs from context and writes its output back.
    The entire context is serialized to JSON and stored in pipelines.context_snapshot.
    """

    pipeline_id: str
    project_id: str
    project_config: dict[str, Any] = field(default_factory=dict)
    document_ids: list[str] = field(default_factory=list)

    # Populated by each stage:
    raw_texts: dict[str, str] | None = None           # ingestion → parsing
    parsed_requirements: list[dict] | None = None     # parsing → analysis
    analysis_report: dict | None = None               # analysis → generation
    generated_test_cases: list[dict] | None = None    # generation → review
    approved_test_case_ids: list[str] | None = None   # review → execution
    execution_ids: list[str] | None = None            # execution → reporting
    report_ids: list[str] | None = None               # reporting → regression
    regression_case_ids: list[str] | None = None      # regression → completed

    # User-provided custom prompt for requirement analysis
    custom_prompt: str | None = None

    # Generated test plan (populated by parsing stage)
    test_plan_md: str | None = None
    test_plan_file: str | None = None

    # Performance & security plans (produced by analysis, consumed by reporting)
    performance_plan: dict | None = None
    security_plan: dict | None = None

    # Generated script paths (populated by execution stage)
    performance_scripts: list[str] | None = None   # paths to generated Locust scripts
    api_scripts: list[str] | None = None            # paths to generated API scripts

    # Optional review feedback (populated on reject)
    review_feedback: str | None = None

    # Stage execution metadata (for idempotency/tracing; persisted in context snapshot JSON)
    stage_attempts: dict[str, int] = field(default_factory=dict)
    stage_idempotency: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str | dict) -> PipelineContext:
        if isinstance(data, dict):
            d = data
        else:
            d = json.loads(data)
        return cls(**d)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__
