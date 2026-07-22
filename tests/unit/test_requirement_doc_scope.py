"""文档净化与 FR 范围校验单测。"""

from __future__ import annotations

from pathlib import Path

from src.services.requirement_evidence import (
    extract_chapter3_modules,
    extract_requirement_modules,
    validate_analysis_scope,
)
from src.utils.document_converter import convert_to_markdown
from src.utils.document_sanitizer import validate_upload_document


def test_ra0011_clean_doc_passes_upload_check():
    md = Path(
        "/Users/xiguawang/TestPlatform-wx/storage/requirement_analyses/RA-0011/"
        "爱奇艺叭嗒 App 产品需求文档.docx.md"
    ).read_text(encoding="utf-8")
    report = validate_upload_document(md)
    assert report.blocked is False
    modules = extract_chapter3_modules(md)
    assert "登录与注册" in modules
    assert "漫画阅读" in modules
    assert "首页" not in modules


def test_ra0012_polluted_docx_blocked_at_upload():
    docx = Path(
        "/Users/xiguawang/TestPlatform-wx/storage/documents/"
        "84b22d0d-c4a4-4991-8da8-2ea0b8d98a5b/爱奇艺叭嗒 App 产品需求文档.docx"
    ).read_bytes()
    md = convert_to_markdown(docx, filename="x.docx", file_type="docx")
    report = validate_upload_document(md)
    assert report.blocked is True
    assert "AI" in report.block_reason or "污染" in report.block_reason


def test_scope_rejects_fr_module_not_in_chapter3():
    doc = """## 三、核心功能详细需求
### 3.1 登录与注册模块
## 3.1.2 详细功能要求
默认：手机号登录
### 3.2 漫画阅读模块
## 3.2.2 详细功能要求
高清阅读
"""
    analysis = {
        "functional_requirements": [
            {
                "id": "FR-001",
                "module": "登录与注册",
                "source_evidence": ["原文摘录：默认：手机号登录"],
            },
            {
                "id": "FR-002",
                "module": "首页",
                "source_evidence": ["原文摘录：搜索框支持关键词搜索"],
            },
        ]
    }
    report = validate_analysis_scope(doc, analysis)
    assert report.ok is False
    assert any("FR-002" in e for e in report.errors)


def test_scope_accepts_modules_from_any_numbered_chapter():
    doc = """## 二、核心功能详细需求
### 2.2 漫画阅读器模块
## 2.2.2 详细功能要求
漫画阅读器新增开通会员条
漫画阅读器新增追更按钮
"""
    analysis = {
        "functional_requirements": [
            {
                "id": "FR-001",
                "module": "漫画阅读器",
                "source_evidence": ["原文摘录：漫画阅读器新增开通会员条"],
            }
        ]
    }
    assert extract_requirement_modules(doc) == ["漫画阅读器"]
    report = validate_analysis_scope(doc, analysis)
    assert report.ok is True


def test_unstructured_document_uses_exact_evidence_instead_of_failing_scope():
    doc = """# 小版本需求
用户点击追更按钮后加入追更列表。
"""
    analysis = {
        "functional_requirements": [
            {
                "id": "FR-001",
                "module": "追更",
                "source_evidence": ["原文摘录：用户点击追更按钮后加入追更列表。"],
            }
        ]
    }
    report = validate_analysis_scope(doc, analysis)
    assert report.ok is True
    assert report.allowed_modules == []


def test_unstructured_document_rejects_knowledge_only_fr():
    doc = "# 小版本需求\n新增追更按钮。"
    analysis = {
        "functional_requirements": [
            {
                "id": "FR-001",
                "module": "会员",
                "source_evidence": ["原文摘录：会员支付成功后自动续费"],
            }
        ]
    }
    report = validate_analysis_scope(doc, analysis)
    assert report.ok is False
    assert any("FR-001" in error and "原文" in error for error in report.errors)


def test_nfr_without_document_evidence_is_rejected():
    doc = "# 功能需求\n新增作品简介。"
    analysis = {
        "functional_requirements": [],
        "non_functional_requirements": [
            {
                "id": "NFR-001",
                "category": "performance",
                "description": "页面 P95 小于 500ms",
                "source_evidence": ["知识库标准：P95 小于 500ms"],
            }
        ],
    }
    report = validate_analysis_scope(doc, analysis)
    assert report.ok is False
    assert any("NFR-001" in error for error in report.errors)


def test_requirement_analyzer_skill_is_dynamic_and_document_only():
    skill = Path(
        "/Users/xiguawang/TestPlatform-wx/.agents/skills/"
        "requirement-analyzer/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "全文动态识别" in skill
    assert "知识库内容不得进入 FR/NFR" in skill
    assert "只能从 **「三、核心功能详细需求" not in skill
