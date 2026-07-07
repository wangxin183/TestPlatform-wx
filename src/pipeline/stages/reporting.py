"""Stage 7: Report generation — aggregates execution results into structured reports.

Exports reports in HTML, JSON, and optionally PDF formats.
Saves to storage/reports/{project_id}/ and creates DB records.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.models import Execution, ExecutionResult, Defect, Report, PipelineStageLog
from src.report.exporter import build_report_data, export_html, export_json, export_pdf
from src.pipeline.stages.base import AbstractStage, StageInput, StageOutput
from src.utils.logging_config import get_logger
from src.utils.stage_logger import get_stage_logger

logger = get_logger(__name__)


class ReportingStage(AbstractStage):
    stage_name = "reporting"

    @classmethod
    def required_context_fields(cls) -> list[str]:
        return ["execution_ids"]

    @classmethod
    def produced_context_fields(cls) -> list[str]:
        return ["report_ids"]

    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    async def execute(self, stage_input: StageInput) -> StageOutput:
        context = stage_input.context
        pid = stage_input.pipeline_id
        slog = get_stage_logger(pid, self.stage_name)
        slog.info(f"========== 报告生成阶段开始 ==========")
        slog.info(f"执行记录数: {len(context.execution_ids or [])}")
        
        execution_ids = context.execution_ids or []
        project_id = context.project_id

        # ── Fetch all execution results + defects ──
        all_results: list[dict] = []
        all_defects: list[dict] = []

        for eid in execution_ids:
            r = await self._db.execute(
                select(ExecutionResult).where(ExecutionResult.execution_id == eid)
            )
            for er in r.scalars().all():
                all_results.append(self._serialize_result(er))

            d = await self._db.execute(
                select(Defect).where(Defect.execution_id == eid)
            )
            for defect in d.scalars().all():
                all_defects.append(self._serialize_defect(defect))

        # ── Fetch stage logs for timeline ──
        stage_logs: list[dict] = []
        if context.pipeline_id:
            sl = await self._db.execute(
                select(PipelineStageLog)
                .where(PipelineStageLog.pipeline_id == context.pipeline_id)
                .order_by(PipelineStageLog.created_at)
            )
            for log in sl.scalars().all():
                stage_logs.append({
                    "stage_name": log.stage_name,
                    "status": log.status,
                    "started_at": log.started_at.isoformat() if log.started_at else None,
                    "completed_at": log.completed_at.isoformat() if log.completed_at else None,
                    "error_message": log.error_message,
                })

        total = len(all_results)
        passed = sum(1 for r in all_results if r["status"] == "passed")
        failed = sum(1 for r in all_results if r["status"] == "failed")
        errors = sum(1 for r in all_results if r["status"] == "error")
        
        slog.info(f"执行结果汇总: 总计={total}, 通过={passed}, 失败={failed}, 错误={errors}")

        generated_count = sum(1 for r in all_results if r["status"] == "generated")
        executable_total = max(total - generated_count, 1)

        summary = {
            "total_cases": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "generated": generated_count,
            "pass_rate": round(passed / executable_total * 100, 2),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # ── Build unified report data ──
        report_data = build_report_data(
            project_name=getattr(stage_input, "project_name", "") or project_id[:8],
            platform_type=context.project_config.get("platform_type", "") if context.project_config else "",
            pipeline_id=context.pipeline_id,
            execution_summary=summary,
            execution_results=all_results,
            defects=all_defects,
            stage_logs=stage_logs,
            performance_plan=context.performance_plan,
            security_plan=context.security_plan,
        )

        # ── Generate timestamp for file naming ──
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        report_ids: list[str] = []

        # HTML report
        html_path = f"reports/{project_id}/report_{ts}.html"
        try:
            await export_html(report_data, html_path)
            report = Report(
                pipeline_id=context.pipeline_id,
                project_id=context.project_id,
                report_type="execution",
                format="html",
                file_path=html_path,
                summary_json=summary,
            )
            self._db.add(report)
            await self._db.flush()
            report_ids.append(report.id)
            logger.info("report_html_saved", path=html_path, report_id=report.id)
        except Exception as exc:
            logger.error("report_html_failed", error=str(exc))

        # JSON report
        json_path = f"reports/{project_id}/report_{ts}.json"
        try:
            await export_json(report_data, json_path)
            report = Report(
                pipeline_id=context.pipeline_id,
                project_id=context.project_id,
                report_type="execution",
                format="json",
                file_path=json_path,
                summary_json=summary,
            )
            self._db.add(report)
            await self._db.flush()
            report_ids.append(report.id)
        except Exception as exc:
            logger.error("report_json_failed", error=str(exc))

        # PDF report (best-effort)
        pdf_path = f"reports/{project_id}/report_{ts}.pdf"
        try:
            pdf_saved = await export_pdf(report_data, pdf_path)
            report = Report(
                pipeline_id=context.pipeline_id,
                project_id=context.project_id,
                report_type="execution",
                format="pdf" if pdf_saved.endswith(".pdf") else "html",
                file_path=pdf_saved,
                summary_json=summary,
            )
            self._db.add(report)
            await self._db.flush()
            report_ids.append(report.id)
        except Exception as exc:
            logger.error("report_pdf_failed", error=str(exc))

        await self._db.commit()
        context.report_ids = report_ids
        
        slog.info(f"========== 报告生成阶段完成: 生成了{len(report_ids)}份报告 ==========")

        return StageOutput(
            stage_name=self.stage_name,
            status="completed",
            data={
                **summary,
                "report_count": len(report_ids),
                "formats": ["html", "json"] + (["pdf"] if any("pdf" in str(r) for r in report_ids) else []),
            },
        )

    # ══════════════════════════════
    # Serializers
    # ══════════════════════════════

    @staticmethod
    def _serialize_result(er: ExecutionResult) -> dict:
        return {
            "id": er.id,
            "test_case_id": er.test_case_id,
            "attempt": er.attempt,
            "status": er.status,
            "duration_ms": er.duration_ms,
            "error_message": er.error_message,
            "failure_reason": er.failure_reason,
            "screenshot_path": er.screenshot_path,
            "step_results": er.step_results,
            "generated_script_path": getattr(er, "generated_script_path", None),
            "executed_at": er.executed_at.isoformat() if er.executed_at else None,
        }

    @staticmethod
    def _serialize_defect(d: Defect) -> dict:
        return {
            "id": d.id,
            "title": d.title,
            "description": d.description,
            "severity": d.severity,
            "status": d.status,
            "reproduction_steps": d.reproduction_steps,
            "evidence_paths": d.evidence_paths,
        }
