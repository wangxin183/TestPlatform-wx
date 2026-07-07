from __future__ import annotations

import json


def test_pipeline_context_stage_metadata_roundtrip():
    from src.pipeline.context import PipelineContext

    ctx = PipelineContext(pipeline_id="p1", project_id="prj1")
    ctx.stage_attempts["generation"] = 2
    ctx.stage_idempotency["generation"] = "p1:generation:2"

    data = json.loads(ctx.to_json())
    assert data["stage_attempts"]["generation"] == 2
    assert data["stage_idempotency"]["generation"] == "p1:generation:2"

    restored = PipelineContext.from_json(ctx.to_json())
    assert restored.stage_attempts["generation"] == 2
    assert restored.stage_idempotency["generation"] == "p1:generation:2"

