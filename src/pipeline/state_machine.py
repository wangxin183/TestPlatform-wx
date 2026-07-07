"""Pipeline finite state machine using the transitions library.

States:
  pending → ingestion → parsing → analysis → generation → review
                                                             ├─ approve → execution → reporting → regression → completed
                                                             └─ reject  → generation (loop back)

Any state: on_error → failed, on_cancel → cancelled
Any state except pending/completed/failed/cancelled: on_pause → paused → on_resume → (previous)
"""

from __future__ import annotations

from transitions import Machine

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

STATES = [
    "pending",
    "ingestion",
    "parsing",
    "analysis",
    "generation",
    "review",
    "execution",
    "reporting",
    "regression",
    "completed",
    "failed",
    "cancelled",
    "paused",
]

# States that are considered "active" (can be paused/cancelled)
ACTIVE_STATES = [
    "ingestion",
    "parsing",
    "analysis",
    "generation",
    "review",
    "execution",
    "reporting",
    "regression",
]

# Linear progression of stages
STAGE_ORDER = [
    "pending",
    "ingestion",
    "parsing",
    "analysis",
    "generation",
    "review",
    "execution",
    "reporting",
    "regression",
    "completed",
]


class PipelineStateMachine:
    """Manages the lifecycle of a single pipeline run."""

    def __init__(self, pipeline_id: str):
        self.pipeline_id = pipeline_id
        self._previous_state: str | None = None

        self.machine = Machine(
            model=self,
            states=STATES,
            initial="pending",
            send_event=True,
            queued=True,
        )

        # --- Forward progression ---
        self.machine.add_transition(
            trigger="start",
            source="pending",
            dest="ingestion",
            before="_log_transition",
            after="_on_start",
        )

        self.machine.add_transition(
            trigger="advance",
            source="ingestion",
            dest="parsing",
            conditions=["_stage_success"],
            before="_log_transition",
        )

        self.machine.add_transition(
            trigger="advance",
            source="parsing",
            dest="analysis",
            conditions=["_stage_success"],
            before="_log_transition",
        )

        self.machine.add_transition(
            trigger="advance",
            source="analysis",
            dest="generation",
            conditions=["_stage_success"],
            before="_log_transition",
        )

        self.machine.add_transition(
            trigger="advance",
            source="generation",
            dest="review",
            conditions=["_stage_success"],
            before="_log_transition",
        )

        self.machine.add_transition(
            trigger="advance",
            source="review",
            dest="execution",
            conditions=["_review_approved"],
            before="_log_transition",
        )

        self.machine.add_transition(
            trigger="advance",
            source="execution",
            dest="reporting",
            conditions=["_stage_success"],
            before="_log_transition",
        )

        self.machine.add_transition(
            trigger="advance",
            source="reporting",
            dest="regression",
            conditions=["_stage_success"],
            before="_log_transition",
        )

        self.machine.add_transition(
            trigger="advance",
            source="regression",
            dest="completed",
            conditions=["_stage_success"],
            before="_log_transition",
        )

        # --- Review loop-back ---
        self.machine.add_transition(
            trigger="reject",
            source="review",
            dest="generation",
            before="_log_transition",
        )

        # --- Error / Cancel / Pause (from all active states) ---
        for state in ACTIVE_STATES:
            self.machine.add_transition(
                trigger="error",
                source=state,
                dest="failed",
                before="_log_transition",
            )
            self.machine.add_transition(
                trigger="cancel",
                source=state,
                dest="cancelled",
                before="_log_transition",
            )
            self.machine.add_transition(
                trigger="pause",
                source=state,
                dest="paused",
                before="_on_pause",
            )

        self.machine.add_transition(
            trigger="resume",
            source="paused",
            dest=None,  # dynamically set in _on_resume
            before="_on_resume",
        )

        # --- Stage result tracking ---
        self._stage_success_flag = True
        self._review_approved_flag = False

    # ---- Conditions ----

    def _stage_success(self, event) -> bool:
        return self._stage_success_flag

    def _review_approved(self, event) -> bool:
        return self._review_approved_flag

    # ---- Callbacks ----

    def _log_transition(self, event) -> None:
        logger.info(
            "pipeline_state_transition",
            pipeline_id=self.pipeline_id,
            from_state=event.transition.source,
            to_state=event.transition.dest,
            trigger=event.kwargs.get("trigger", "unknown"),
        )

    def _on_start(self, event) -> None:
        logger.info("pipeline_started", pipeline_id=self.pipeline_id)

    def _on_pause(self, event) -> None:
        self._previous_state = event.transition.source
        logger.info(
            "pipeline_paused",
            pipeline_id=self.pipeline_id,
            previous_state=self._previous_state,
        )

    def _on_resume(self, event) -> None:
        if self._previous_state and self._previous_state in STATES:
            event.model.state = self._previous_state
            logger.info(
                "pipeline_resumed",
                pipeline_id=self.pipeline_id,
                resumed_state=self._previous_state,
            )
        else:
            event.model.state = "pending"

    # ---- Public helpers ----

    def set_stage_success(self, success: bool) -> None:
        self._stage_success_flag = success

    def set_review_approved(self, approved: bool) -> None:
        self._review_approved_flag = approved

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def is_terminal(self) -> bool:
        return self.state in ("completed", "failed", "cancelled")
