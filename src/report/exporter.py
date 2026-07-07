"""Report exporters — HTML, PDF, and JSON report generation.

HTML reports use Jinja2 templates with inline CSS for portability.
PDF is generated from HTML via WeasyPrint (optional, falls back to HTML).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from src.core.config import settings
from src.utils.file_storage import save as save_file
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STORAGE_REPORTS = Path(settings.storage.reports_dir)


def _fmt_date(val: str | None) -> str:
    if not val:
        return "-"
    try:
        return val[:19].replace("T", " ")
    except Exception:
        return str(val)


def _fmt_pct(val: float) -> str:
    return f"{val:.1f}%"


def _severity_color(severity: str) -> str:
    return {
        "critical": "#dc3545",
        "high": "#fd7e14",
        "medium": "#ffc107",
        "low": "#28a745",
    }.get(severity, "#6c757d")


def _status_badge(status: str) -> str:
    return {
        "passed": '<span class="badge badge-passed">通过</span>',
        "failed": '<span class="badge badge-failed">失败</span>',
        "error": '<span class="badge badge-error">错误</span>',
        "skipped": '<span class="badge badge-skipped">跳过</span>',
    }.get(status, f'<span class="badge">{status}</span>')


_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=True,
)
_jinja_env.filters["fmt_date"] = _fmt_date
_jinja_env.filters["fmt_pct"] = _fmt_pct
_jinja_env.filters["severity_color"] = _severity_color
_jinja_env.filters["status_badge"] = _status_badge


# ══════════════════════════════════════
# Data builder
# ══════════════════════════════════════

def build_report_data(
    *,
    project_name: str = "",
    platform_type: str = "",
    pipeline_id: str = "",
    execution_summary: dict[str, Any] | None = None,
    execution_results: list[dict[str, Any]] | None = None,
    defects: list[dict[str, Any]] | None = None,
    stage_logs: list[dict[str, Any]] | None = None,
    performance_plan: dict[str, Any] | None = None,
    security_plan: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    results = execution_results or []
    _defects = defects or []

    total = len(results)
    passed = sum(1 for r in results if r.get("status") == "passed")
    failed = sum(1 for r in results if r.get("status") == "failed")
    error = sum(1 for r in results if r.get("status") == "error")
    skipped = sum(1 for r in results if r.get("status") == "skipped")

    by_severity: dict[str, int] = {}
    for d in _defects:
        sev = d.get("severity", "medium")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    by_type: dict[str, dict[str, int]] = {}
    for r in results:
        tt = r.get("test_type", "unknown")
        if tt not in by_type:
            by_type[tt] = {"total": 0, "passed": 0, "failed": 0, "error": 0}
        by_type[tt]["total"] += 1
        by_type[tt][r.get("status", "error")] += 1

    total_duration_ms = sum(r.get("duration_ms", 0) or 0 for r in results)

    timeline: list[dict] = []
    if stage_logs:
        for log in stage_logs:
            duration = None
            if log.get("started_at") and log.get("completed_at"):
                try:
                    a = datetime.fromisoformat(log["started_at"])
                    b = datetime.fromisoformat(log["completed_at"])
                    duration = f"{(b - a).total_seconds():.1f}s"
                except Exception:
                    pass
            timeline.append({
                "stage_name": log.get("stage_name", ""),
                "status": log.get("status", ""),
                "duration": duration,
                "error": log.get("error_message"),
            })

    return {
        "project_name": project_name,
        "platform_type": platform_type,
        "pipeline_id": pipeline_id,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "summary": execution_summary or {},
        "total": total,
        "passed": passed,
        "failed": failed,
        "error": error,
        "skipped": skipped,
        "pass_rate": round(passed / total * 100, 2) if total > 0 else 0,
        "total_duration_ms": total_duration_ms,
        "total_duration_s": round(total_duration_ms / 1000, 1),
        "defects": _defects,
        "defects_by_severity": by_severity,
        "results": results,
        "by_test_type": by_type,
        "timeline": timeline,
        "performance_plan": performance_plan,
        "security_plan": security_plan,
    }


# ══════════════════════════════════════
# Exporters
# ══════════════════════════════════════

async def export_html(report_data: dict[str, Any], output_path: str) -> str:
    template = _jinja_env.get_template("execution_report.html")
    html = template.render(**report_data)
    await save_file(output_path, html.encode("utf-8"))
    logger.info("report_html_exported", path=output_path)
    return output_path


async def export_json(report_data: dict[str, Any], output_path: str) -> str:
    json_str = json.dumps(report_data, ensure_ascii=False, indent=2, default=str)
    await save_file(output_path, json_str.encode("utf-8"))
    logger.info("report_json_exported", path=output_path)
    return output_path


async def export_pdf(report_data: dict[str, Any], output_path: str) -> str:
    try:
        from weasyprint import HTML as WeasyHTML
        template = _jinja_env.get_template("execution_report.html")
        html = template.render(**report_data)
        WeasyHTML(string=html).write_pdf(output_path)
        logger.info("report_pdf_exported", path=output_path)
        return output_path
    except ImportError:
        logger.warning("weasyprint_not_installed", fallback="html")
        html_path = output_path.replace(".pdf", ".html")
        return await export_html(report_data, html_path)
    except Exception as exc:
        logger.error("pdf_export_failed", error=str(exc))
        html_path = output_path.replace(".pdf", ".html")
        return await export_html(report_data, html_path)
