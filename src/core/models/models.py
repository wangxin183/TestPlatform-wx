"""All 14 ORM models for the test platform.

Tables:
# Project            - Test project configuration
# Document           - Uploaded requirement/API documents
# Pipeline           - Workflow pipeline instance
# PipelineStageLog   - Per-stage structured output
# TestCase           - Generated test cases
# TestSuite          - Group of test cases for execution
# Execution          - Test execution run
# ExecutionResult    - Per-case execution outcome
# Defect             - Bugs found during execution
# Report            - Generated test reports
# RegressionCase    - Regression test cases
# Schedule          - Recurring pipeline execution schedules
# Environment       - Test environment configuration
# NotificationConfig- Alert/webhook notification settings - Regression test cases
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from src.core.models.base import Base, UUIDMixin, TimestampMixin, generate_uuid, utcnow


# ──────────────────────────────────────────────
# 1. Project
# ──────────────────────────────────────────────
class Project(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    name = Column(String(255), nullable=False)
    description = Column(Text)
    platform_type = Column(
        String(50), nullable=False
    )  # ios/android/web/h5/miniprogram/api
    platform_config = Column(JSON)
    status = Column(String(20), default="active")

    documents = relationship("Document", back_populates="project", cascade="all, delete-orphan")
    pipelines = relationship("Pipeline", back_populates="project", cascade="all, delete-orphan")
    test_cases = relationship("TestCase", back_populates="project", cascade="all, delete-orphan")
    test_suites = relationship("TestSuite", back_populates="project", cascade="all, delete-orphan")
    executions = relationship("Execution", back_populates="project", cascade="all, delete-orphan")
    defects = relationship("Defect", back_populates="project", cascade="all, delete-orphan")
    reports = relationship("Report", back_populates="project", cascade="all, delete-orphan")
    regression_cases = relationship("RegressionCase", back_populates="project", cascade="all, delete-orphan")
    schedules = relationship("Schedule", back_populates="project", cascade="all, delete-orphan")
    environments = relationship("Environment", back_populates="project", cascade="all, delete-orphan")
    notification_configs = relationship("NotificationConfig", back_populates="project", cascade="all, delete-orphan")


# ──────────────────────────────────────────────
# 2. Document
# ──────────────────────────────────────────────
class Document(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)
    filename = Column(String(500), nullable=False)
    file_type = Column(
        String(20), nullable=False
    )  # pdf/docx/md/openapi_json/openapi_yaml/txt/xlsx
    file_path = Column(String(1000), nullable=False)
    raw_text = Column(Text)
    parsed_content = Column(JSON)
    status = Column(String(20), default="uploaded")  # uploaded/parsing/parsed/failed
    error_message = Column(Text)

    project = relationship("Project", back_populates="documents")


# ──────────────────────────────────────────────
# 3. Pipeline
# ──────────────────────────────────────────────
class Pipeline(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "pipelines"

    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)
    current_stage = Column(String(30), nullable=False, default="pending")
    status = Column(
        String(20), default="pending"
    )  # pending/running/paused/completed/failed/cancelled
    context_snapshot = Column(JSON)
    celery_task_id = Column(String(255))
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    project = relationship("Project", back_populates="pipelines")
    stage_logs = relationship(
        "PipelineStageLog", back_populates="pipeline", cascade="all, delete-orphan"
    )
    test_cases = relationship("TestCase", back_populates="pipeline")
    test_suites = relationship("TestSuite", back_populates="pipeline")
    executions = relationship("Execution", back_populates="pipeline")
    reports = relationship("Report", back_populates="pipeline")
    regression_cases = relationship("RegressionCase", back_populates="pipeline")


# ──────────────────────────────────────────────
# 4. PipelineStageLog
# ──────────────────────────────────────────────
class PipelineStageLog(UUIDMixin, Base):
    __tablename__ = "pipeline_stage_logs"
    # Removed UNIQUE(pipeline_id, stage_name) to allow stage retries

    pipeline_id = Column(String(36), ForeignKey("pipelines.id"), nullable=False)
    stage_name = Column(String(30), nullable=False)
    status = Column(
        String(20), nullable=False
    )  # pending/running/completed/failed/skipped
    input_summary = Column(JSON)
    output_data = Column(JSON)
    error_message = Column(Text)
    log_file_path = Column(String(1000))
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    pipeline = relationship("Pipeline", back_populates="stage_logs")


# ──────────────────────────────────────────────
# 5. TestCase
# ──────────────────────────────────────────────
class TestCase(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "test_cases"

    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)
    pipeline_id = Column(String(36), ForeignKey("pipelines.id"), nullable=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    preconditions = Column(Text)
    steps = Column(JSON, nullable=False)  # [{step, action, expected}, ...]
    priority = Column(String(10), default="medium")  # critical/high/medium/low
    test_type = Column(
        String(30), nullable=False
    )  # ui/api/performance/security/compatibility
    tags = Column(JSON)  # ["smoke", "login", "payment"]
    platform_type = Column(String(50), nullable=False)
    status = Column(
        String(20), default="draft"
    )  # draft/pending_review/approved/rejected/deprecated
    review_comment = Column(Text)
    reviewed_by = Column(String(255))
    reviewed_at = Column(DateTime)
    notes = Column(Text)  # 备注 — manual notes from reviewers
    reject_reason = Column(String(50))  # steps_incomplete/description_vague/wrong_priority/wrong_type/duplicate/other
    ai_score = Column(Integer)  # AI review score 0-100
    ai_flags = Column(JSON)  # ["vague_expected", "insufficient_steps", ...]
    regenerated_from = Column(String(36))  # source test case id when regenerated
    directory_id = Column(String(36), ForeignKey("case_directories.id"), nullable=True)
    source = Column(String(20), default="manual")  # import/manual/auto
    test_plan_id = Column(String(36), nullable=True)  # FK to requirement_tasks.id when auto-generated
    # 独立用例生成模块追溯字段（与 project/pipeline 解耦）
    generation_id = Column(String(36), nullable=True, index=True)  # TCG-xxxx
    source_analysis_id = Column(String(36), nullable=True, index=True)  # RA-xxxx
    test_point_id = Column(String(36), nullable=True, index=True)  # TP-xxx
    # ready | semi | manual — App 执行半硬门禁用
    automation_level = Column(String(20), nullable=True, index=True)
    # 模块化可执行用例：NL 评审轨 + DSL/Agent 执行轨
    module = Column(String(100), nullable=True, index=True)
    exec_script = Column(JSON, nullable=True)
    compile_status = Column(String(20), nullable=True, default="pending", index=True)
    compile_errors = Column(JSON, nullable=True)
    execution_mode = Column(String(20), nullable=True, default="hybrid")
    step_contracts = Column(JSON, nullable=True)
    # {login_state, user_type, entry_context, notes}
    precondition_spec = Column(JSON, nullable=True)
    automation_block_reason = Column(String(500), nullable=True)
    assertion_quality = Column(String(20), nullable=True)  # strong|adequate|weak|none

    project = relationship("Project", back_populates="test_cases")
    pipeline = relationship("Pipeline", back_populates="test_cases")
    execution_results = relationship("ExecutionResult", back_populates="test_case")
    directory = relationship("TestCaseDirectory", back_populates="test_cases")



# ──────────────────────────────────────────────
# 5.5  TestCaseDirectory — 用例目录（用例库）
# ──────────────────────────────────────────────
class TestCaseDirectory(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "case_directories"

    parent_id = Column(String(36), ForeignKey("case_directories.id"), nullable=True)
    name = Column(String(255), nullable=False)
    sort_order = Column(Integer, default=0)

    parent = relationship("TestCaseDirectory", remote_side="TestCaseDirectory.id", back_populates="children")
    children = relationship("TestCaseDirectory", back_populates="parent", cascade="all, delete-orphan")
    test_cases = relationship("TestCase", back_populates="directory")

# ──────────────────────────────────────────────
# 6. TestSuite
# ──────────────────────────────────────────────
class TestSuite(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "test_suites"

    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)
    pipeline_id = Column(String(36), ForeignKey("pipelines.id"), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    test_case_ids = Column(JSON, nullable=False)  # [UUID, UUID, ...]

    project = relationship("Project", back_populates="test_suites")
    pipeline = relationship("Pipeline", back_populates="test_suites")
    executions = relationship("Execution", back_populates="test_suite")


# ──────────────────────────────────────────────
# 7. Execution
# ──────────────────────────────────────────────
class Execution(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "executions"

    test_suite_id = Column(String(36), ForeignKey("test_suites.id"), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)
    pipeline_id = Column(String(36), ForeignKey("pipelines.id"), nullable=True)
    executor_type = Column(
        String(30), nullable=False
    )  # web/ios/android/miniprogram/api
    status = Column(
        String(20), default="pending"
    )  # pending/running/completed/failed/cancelled
    total_cases = Column(Integer, default=0)
    passed_cases = Column(Integer, default=0)
    failed_cases = Column(Integer, default=0)
    error_cases = Column(Integer, default=0)
    max_retries = Column(Integer, default=2)
    config_override = Column(JSON)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    test_suite = relationship("TestSuite", back_populates="executions")
    project = relationship("Project", back_populates="executions")
    pipeline = relationship("Pipeline", back_populates="executions")
    results = relationship("ExecutionResult", back_populates="execution", cascade="all, delete-orphan")
    defects = relationship("Defect", back_populates="execution", cascade="all, delete-orphan")
    reports = relationship("Report", back_populates="execution")


# ──────────────────────────────────────────────
# 8. ExecutionResult
# ──────────────────────────────────────────────
class ExecutionResult(UUIDMixin, Base):
    __tablename__ = "execution_results"

    execution_id = Column(String(36), ForeignKey("executions.id"), nullable=False)
    test_case_id = Column(String(36), ForeignKey("test_cases.id"), nullable=False)
    attempt = Column(Integer, default=1)
    status = Column(String(20), nullable=False)  # passed/failed/error/skipped/generated
    duration_ms = Column(Float)
    error_message = Column(Text)
    failure_reason = Column(Text)  # LLM-analyzed root cause
    screenshot_path = Column(String(1000))
    step_results = Column(JSON)  # [{step, action, result, screenshot?}, ...]
    generated_script_path = Column(String(1000))  # path to generated API/perf script
    executed_at = Column(DateTime, default=utcnow)

    execution = relationship("Execution", back_populates="results")
    test_case = relationship("TestCase", back_populates="execution_results")
    defect = relationship("Defect", back_populates="execution_result", uselist=False)


# ──────────────────────────────────────────────
# 9. Defect
# ──────────────────────────────────────────────
class Defect(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "defects"

    execution_result_id = Column(
        String(36), ForeignKey("execution_results.id"), unique=True, nullable=False
    )
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)
    execution_id = Column(String(36), ForeignKey("executions.id"), nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    severity = Column(String(10), default="medium")  # critical/high/medium/low
    reproduction_steps = Column(JSON)
    evidence_paths = Column(JSON)  # [screenshot_path, video_path, ...]
    status = Column(
        String(20), default="open"
    )  # open/confirmed/in_progress/fixed/wont_fix

    execution_result = relationship("ExecutionResult", back_populates="defect")
    project = relationship("Project", back_populates="defects")
    execution = relationship("Execution", back_populates="defects")


# ──────────────────────────────────────────────
# 10. Report
# ──────────────────────────────────────────────
class Report(UUIDMixin, Base):
    __tablename__ = "reports"

    pipeline_id = Column(String(36), ForeignKey("pipelines.id"), nullable=True)
    execution_id = Column(String(36), ForeignKey("executions.id"), nullable=True)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)
    report_type = Column(
        String(30), nullable=False
    )  # execution/pipeline_summary/regression
    format = Column(String(10), default="html")  # html/json/pdf
    file_path = Column(String(1000))
    summary_json = Column(JSON)
    generated_at = Column(DateTime, default=utcnow)

    pipeline = relationship("Pipeline", back_populates="reports")
    execution = relationship("Execution", back_populates="reports")
    project = relationship("Project", back_populates="reports")


# ──────────────────────────────────────────────
# 11. RegressionCase
# 12. Schedule          - Recurring pipeline execution schedules
# 13. Environment       - Test environment configuration
# 14. NotificationConfig- Alert/webhook notification settings
# ──────────────────────────────────────────────
class RegressionCase(UUIDMixin, Base):
    __tablename__ = "regression_test_cases"

    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)
    pipeline_id = Column(String(36), ForeignKey("pipelines.id"), nullable=False)
    source_case_id = Column(String(36), ForeignKey("test_cases.id"), nullable=True)
    title = Column(String(500), nullable=False)
    steps = Column(JSON, nullable=False)
    priority = Column(String(10), default="high")
    selection_reason = Column(Text)
    created_at = Column(DateTime, default=utcnow)

    project = relationship("Project", back_populates="regression_cases")
    pipeline = relationship("Pipeline", back_populates="regression_cases")


# ──────────────────────────────────────────────
# 12. Schedule          - Recurring pipeline execution schedules
# Environment       - Test environment configuration
# NotificationConfig- Alert/webhook notification settings — recurring pipeline execution
# ──────────────────────────────────────────────
class Schedule(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "schedules"

    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)
    name = Column(String(255), nullable=False)
    cron_expression = Column(String(100), nullable=False)  # e.g. "0 2 * * *"
    document_ids = Column(JSON, default=list)              # docs to include
    platform_type = Column(String(50))                     # override platform
    enabled = Column(Boolean, default=True)
    last_run_at = Column(DateTime)
    last_run_status = Column(String(20))
    next_run_at = Column(DateTime)

    project = relationship("Project", back_populates="schedules")

# ──────────────────────────────────────────────
# 13. Environment — test environment configuration
# ──────────────────────────────────────────────
class Environment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "environments"

    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)
    name = Column(String(255), nullable=False)
    base_url = Column(String(1000))
    web_url = Column(String(1000))
    api_base_url = Column(String(1000))
    variables_json = Column(JSON)     # {"username": "admin", "password": "***"}
    headers_json = Column(JSON)       # {"Authorization": "Bearer xxx"}
    is_default = Column(Boolean, default=False)
    description = Column(Text)

    project = relationship("Project", back_populates="environments")


# ──────────────────────────────────────────────
# 14. NotificationConfig — alert/webhook settings
# ──────────────────────────────────────────────
class NotificationConfig(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "notification_configs"

    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)
    name = Column(String(255), nullable=False)
    channel = Column(String(30), nullable=False)  # webhook/email/feishu/dingtalk/wecom
    webhook_url = Column(String(1000))
    email_to = Column(String(500))
    events_json = Column(JSON)  # ["pipeline_completed", "pipeline_failed", "defect_critical"]
    enabled = Column(Boolean, default=True)

    project = relationship("Project", back_populates="notification_configs")


# ──────────────────────────────────────────────
# 15. ReviewSubmission — manually uploaded test cases for review
# ──────────────────────────────────────────────
class ReviewSubmission(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "review_submissions"

    batch_id = Column(String(36), nullable=False, index=True)
    source_format = Column(String(10), default="json")
    title = Column(String(500), nullable=False)
    description = Column(Text)
    preconditions = Column(Text)
    steps = Column(JSON, nullable=False)
    priority = Column(String(10), default="medium")
    test_type = Column(String(30), nullable=False)
    tags = Column(JSON)
    platform_type = Column(String(50))
    status = Column(String(20), default="pending_review")
    review_comment = Column(Text)
    reviewed_by = Column(String(255))
    reviewed_at = Column(DateTime)
    notes = Column(Text)
    reject_reason = Column(String(50))
    ai_score = Column(Integer)
    ai_flags = Column(JSON)


# ──────────────────────────────────────────────
# 16. RequirementTask — standalone requirement management
# ──────────────────────────────────────────────
class RequirementTask(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "requirement_tasks"

    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)
    name = Column(String(500), nullable=False)
    source_format = Column(String(20), nullable=False)  # json/md/docx/pdf/url
    source_url = Column(Text)
    file_path = Column(String(1000))
    char_count = Column(Integer, default=0)
    chunk_count = Column(Integer, default=0)
    chunk_status = Column(String(20), default="pending")  # pending/processing/completed
    req_count = Column(Integer, default=0)
    structured_file = Column(String(1000))
    test_plan_file = Column(String(1000))
    raw_text = Column(Text)
    status = Column(String(20), default="pending")  # pending/processing/completed/failed
    error_message = Column(Text)

    project = relationship("Project")
