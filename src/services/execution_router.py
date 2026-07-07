"""Execution type router — maps (test_type, platform_type) to executor + strategy.

Replaces the flat EXECUTOR_MAP dict in ExecutionStage with intelligent routing
that distinguishes App UI (iOS/Android, visual-first) from Web UI (Web/H5),
routes API to script generation, and enables performance/security script generation.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.models.models import TestCase


@dataclass(frozen=True)
class RouteResult:
    """Routing decision for a test case."""
    executor_name: str       # Name registered in ExecutorRegistry
    use_visual: bool         # Enable OCR + screenshot comparison for assertions
    subtype_label: str       # Feeds into LLM step-translation prompt selection


# ── Route table: (test_type, platform_type) → RouteResult ──
# Wildcard "*" in platform_type matches any platform for that test_type.
_ROUTE_TABLE: dict[tuple[str, str], RouteResult] = {
    # ── UI tests: distinguish Web UI vs App UI ──
    ("ui", "web"):     RouteResult("web",     True, "web_ui"),
    ("ui", "h5"):      RouteResult("web",     True, "web_ui"),
    ("ui", "ios"):     RouteResult("ios",     True, "app_ui"),
    ("ui", "android"): RouteResult("android", True, "app_ui"),

    # ── API tests: auto-generate script + execute ──
    ("api", "*"): RouteResult("api", False, "api"),

    # ── Performance: generate plan + locust script (no longer skipped!) ──
    ("performance", "*"): RouteResult("performance", False, "performance"),

    # ── Security: generate security test plan ──
    ("security", "*"): RouteResult("security", False, "security"),

    # ── Compatibility: cross-browser matrix ──
    ("compatibility", "*"): RouteResult("compatibility", True, "compatibility"),
}


class ExecutionRouter:
    """Route a test case to the appropriate executor + strategy.

    Usage::

        route = ExecutionRouter.route(test_case)
        executor = ExecutorRegistry.get(route.executor_name)
        system_prompt = ExecutionRouter.get_translation_prompt(route.subtype_label)
    """

    @classmethod
    def route(cls, test_case: TestCase) -> RouteResult:
        """Determine the execution strategy for a single test case.

        Looks up by (test_type, platform_type). Falls back to
        (test_type, "*") if the exact platform_type isn't in the table.
        A final fallback returns ("web", True, "web_ui").
        """
        test_type = (test_case.test_type or "ui").lower()
        platform = (test_case.platform_type or "").lower()

        # Exact match
        key = (test_type, platform)
        if key in _ROUTE_TABLE:
            return _ROUTE_TABLE[key]

        # Wildcard platform match
        wildcard_key = (test_type, "*")
        if wildcard_key in _ROUTE_TABLE:
            return _ROUTE_TABLE[wildcard_key]

        # Fallback: treat as web UI
        return RouteResult("web", True, "web_ui")

    @classmethod
    def list_routes(cls) -> dict[str, list[str]]:
        """Return all registered routes for debugging / introspection."""
        result: dict[str, list[str]] = {}
        for (tt, pt), rr in _ROUTE_TABLE.items():
            key = f"{tt}/{pt}"
            result[key] = [rr.executor_name, str(rr.use_visual), rr.subtype_label]
        return result
