"""需求分析服务：修订基线 / 测试点 prompt 构建测试。"""

from __future__ import annotations

from src.services.requirement_analysis_service import RequirementAnalysisService


def test_build_analysis_prompt_includes_revision_baseline() -> None:
    svc = RequirementAnalysisService.__new__(RequirementAnalysisService)
    prompt = svc._build_analysis_prompt(
        skill_body="# skill\n{knowledge_context}",
        doc_md="# 需求\n登录要支持验证码",
        knowledge_context="",
        platform_type="ios",
        custom_prompt="",
        revision_baseline={
            "previous_analysis_json": {
                "functional_requirements": [{"id": "FR-001", "description": "登录"}],
            },
            "previous_review_json": {
                "analysis_defects": [{"type": "hallucination", "target": "FR-099"}],
            },
            "human_comment": "请删除幻觉项并补齐登录超时",
            "human_corrections": [],
            "extra_feedback": "",
        },
    )
    assert "修订基线" in prompt
    assert "请删除幻觉项并补齐登录超时" in prompt
    assert "FR-001" in prompt
    assert "hallucination" in prompt


def test_build_testpoint_prompt_contains_fr_json() -> None:
    svc = RequirementAnalysisService.__new__(RequirementAnalysisService)
    prompt = svc._build_testpoint_prompt(
        skill_body="# tp skill",
        doc_md="# doc",
        analysis_json_str='{"functional_requirements":[{"id":"FR-001"}]}',
    )
    assert "定稿需求拆解" in prompt
    assert "FR-001" in prompt
    assert "test_points" in prompt
