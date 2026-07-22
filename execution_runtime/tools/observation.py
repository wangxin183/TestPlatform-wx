"""页面结构化观察、模块状态匹配与步骤后置条件校验。"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any

from src.services.testcase_module_catalog import PageState


@dataclass(frozen=True)
class ElementObservation:
    text: str = ""
    resource_id: str = ""
    accessibility_id: str = ""
    class_name: str = ""
    clickable: bool = False
    enabled: bool = True
    displayed: bool = True


@dataclass(frozen=True)
class PageObservation:
    package: str
    activity: str
    elements: list[ElementObservation]
    source_hash: str
    screenshot_hash: str = ""
    source: str = field(default="", repr=False)

    def as_dict(self, *, include_source: bool = False) -> dict[str, Any]:
        data = {
            "package": self.package,
            "activity": self.activity,
            "elements": [asdict(element) for element in self.elements],
            "source_hash": self.source_hash,
            "screenshot_hash": self.screenshot_hash,
        }
        if include_source:
            data["source"] = self.source
        return data

    def as_agent_dict(self, *, max_elements: int = 40) -> dict[str, Any]:
        elements = []
        for element in self.elements:
            if not (
                element.text
                or element.resource_id
                or element.accessibility_id
                or element.clickable
            ):
                continue
            compact = {
                "text": element.text,
                "resource_id": element.resource_id,
                "accessibility_id": element.accessibility_id,
                "clickable": element.clickable,
            }
            elements.append({key: value for key, value in compact.items() if value})
            if len(elements) >= max(1, max_elements):
                break
        return {
            "package": self.package,
            "activity": self.activity,
            "source_hash": self.source_hash,
            "elements": elements,
        }


@dataclass(frozen=True)
class StateMatch:
    matched: bool
    missing_all: list[dict[str, Any]] = field(default_factory=list)
    required_any_matched: bool = True
    forbidden_hits: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)


class PageObserver:
    def __init__(self, driver) -> None:
        self.driver = driver

    def observe(self) -> PageObservation:
        source = self._safe_source()
        elements = self._parse_elements(source)
        screenshot = self._safe_screenshot()
        return PageObservation(
            package=str(getattr(self.driver, "current_package", "") or ""),
            activity=str(getattr(self.driver, "current_activity", "") or ""),
            elements=elements,
            source_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
            screenshot_hash=hashlib.sha256(screenshot).hexdigest() if screenshot else "",
            source=source,
        )

    def _safe_source(self) -> str:
        try:
            return str(self.driver.page_source or "")
        except Exception:
            return ""

    def _safe_screenshot(self) -> bytes:
        try:
            return self.driver.get_screenshot_as_png() or b""
        except Exception:
            return b""

    @staticmethod
    def _parse_elements(source: str) -> list[ElementObservation]:
        if not source:
            return []
        try:
            root = ET.fromstring(source)
        except ET.ParseError:
            return []
        elements: list[ElementObservation] = []
        for node in root.iter():
            attrs = node.attrib
            if not attrs:
                continue
            elements.append(
                ElementObservation(
                    text=str(
                        attrs.get("text")
                        or attrs.get("label")
                        or attrs.get("value")
                        or ""
                    ),
                    resource_id=str(
                        attrs.get("resource-id") or attrs.get("resourceId") or ""
                    ),
                    accessibility_id=str(
                        attrs.get("content-desc") or attrs.get("name") or ""
                    ),
                    class_name=str(attrs.get("class") or attrs.get("type") or ""),
                    clickable=_as_bool(attrs.get("clickable")),
                    enabled=_as_bool(attrs.get("enabled"), default=True),
                    displayed=_as_bool(
                        attrs.get("displayed") or attrs.get("visible"),
                        default=True,
                    ),
                )
            )
        return elements


class PageStateMatcher:
    def match(self, state: PageState, observation: PageObservation) -> StateMatch:
        if state.package and observation.package != state.package:
            return StateMatch(
                matched=False,
                reason=f"package 不匹配: {observation.package} != {state.package}",
            )
        if state.activity and observation.activity != state.activity:
            return StateMatch(
                matched=False,
                reason=f"activity 不匹配: {observation.activity} != {state.activity}",
            )
        missing_all = [
            selector
            for selector in state.required_all
            if not self.selector_exists(selector, observation)
        ]
        required_any_matched = (
            not state.required_any
            or any(
                self.selector_exists(selector, observation)
                for selector in state.required_any
            )
        )
        forbidden_hits = [
            selector
            for selector in state.forbidden_any
            if self.selector_exists(selector, observation)
        ]
        matched = not missing_all and required_any_matched and not forbidden_hits
        reasons: list[str] = []
        if missing_all:
            reasons.append("缺少 required_all")
        if not required_any_matched:
            reasons.append("未命中 required_any")
        if forbidden_hits:
            reasons.append("命中 forbidden_any")
        return StateMatch(
            matched=matched,
            missing_all=missing_all,
            required_any_matched=required_any_matched,
            forbidden_hits=forbidden_hits,
            reason="；".join(reasons),
        )

    def selector_exists(
        self,
        selector: dict[str, Any],
        observation: PageObservation,
    ) -> bool:
        selector_type = str(selector.get("type") or "")
        value = str(selector.get("value") or "")
        if not selector_type or not value:
            return False
        for element in observation.elements:
            if selector_type == "text" and element.text == value:
                return True
            if selector_type in {"text_contains", "name"} and value in element.text:
                return True
            if selector_type == "id" and (
                element.resource_id == value
                or element.resource_id.endswith(f":id/{value}")
            ):
                return True
            if selector_type == "accessibility_id" and element.accessibility_id == value:
                return True
        return False


class StepGuard:
    def can_execute(
        self,
        state: PageState,
        observation: PageObservation,
        *,
        target_text: str = "",
    ) -> GuardResult:
        match = PageStateMatcher().match(state, observation)
        reasons = [] if match.matched else [match.reason or "页面状态不匹配"]
        if target_text:
            candidates = [
                element
                for element in observation.elements
                if target_text in element.text or target_text in element.accessibility_id
            ]
            actionable = [
                element
                for element in candidates
                if element.displayed and element.enabled
            ]
            if len(actionable) != 1:
                reasons.append(f"目标元素数量不是 1: {len(actionable)}")
        return GuardResult(ok=not reasons, reasons=reasons)

    def verify_postconditions(
        self,
        contract: dict[str, Any],
        before: PageObservation,
        after: PageObservation,
    ) -> GuardResult:
        reasons: list[str] = []
        for condition in contract.get("postconditions") or []:
            if condition in {"page_content_changed", "page_indicator_changed"}:
                if before.source_hash == after.source_hash:
                    reasons.append(f"{condition}: 页面结构未变化")
            elif str(condition).startswith("text_visible:"):
                value = str(condition).split(":", 1)[1]
                if not any(
                    value in element.text or value in element.accessibility_id
                    for element in after.elements
                ):
                    if value not in (after.source or ""):
                        reasons.append(f"text_visible: 未找到「{value}」")
            elif str(condition).startswith("text_absent:"):
                value = str(condition).split(":", 1)[1]
                if any(
                    value in element.text or value in element.accessibility_id
                    for element in after.elements
                ) or value in (after.source or ""):
                    reasons.append(f"text_absent: 仍可见「{value}」")
            elif condition == "expected_state_visible":
                if not after.elements:
                    reasons.append("expected_state_visible: 页面无可观察元素")
        return GuardResult(ok=not reasons, reasons=reasons)


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).lower() in {"true", "1", "yes"}
