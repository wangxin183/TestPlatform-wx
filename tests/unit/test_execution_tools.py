"""页面观察、状态匹配与统一动作工具网关单测。"""

from __future__ import annotations

import pytest

from execution_runtime.config import RuntimeConfig, TargetApp
from execution_runtime.tools.action_catalog import ACTION_CATALOG
from execution_runtime.tools.gateway import ToolGateway, ToolGatewayError
from execution_runtime.tools.observation import PageObserver, PageStateMatcher, StepGuard
from src.services.testcase_module_catalog import PageState, module_catalog


class FakeElement:
    text = "漫画"

    def __init__(self):
        self.clicked = False

    def click(self):
        self.clicked = True

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    def get_attribute(self, name):
        return ""


class FakeDriver:
    current_package = "com.iqiyi.acg"
    current_activity = ".comic.creader.AcgCReaderActivity"

    def __init__(self, source: str):
        self.page_source = source
        self.element = FakeElement()

    def find_elements(self, _by, _query):
        return [self.element]

    def get_screenshot_as_png(self):
        return b"fake-png"

    def back(self):
        pass

    def activate_app(self, _bundle_id):
        pass

    def terminate_app(self, _bundle_id):
        pass


READER_XML = """<hierarchy>
<node resource-id="reader_root" text="" clickable="false" enabled="true"/>
<node resource-id="fragment_read_real" text="" clickable="false" enabled="true"/>
<node resource-id="tv_episode_title_water_mark" text="1/145话 序章"
      content-desc="" clickable="false" enabled="true" displayed="true"/>
</hierarchy>"""


def test_action_catalog_exposes_read_and_write_tools():
    assert ACTION_CATALOG["observe_page"]["read_only"] is True
    assert ACTION_CATALOG["tap"]["parameters"]["locator"]["required"] is True
    assert ACTION_CATALOG["assert_text"]["parameters"]["value"]["required"] is True
    assert "recover_page" in ACTION_CATALOG
    assert ACTION_CATALOG["recover_page"]["read_only"] is False


def test_recover_page_backs_until_locator_visible():
    class RecoverDriver(FakeDriver):
        def __init__(self):
            super().__init__(
                '<hierarchy><node text="阅读器" resource-id="reader"/></hierarchy>'
            )
            self.backs = 0

        def back(self):
            self.backs += 1
            if self.backs >= 2:
                self.page_source = (
                    '<hierarchy><node text="我的" resource-id="mine" '
                    'enabled="true" displayed="true"/></hierarchy>'
                )

        def find_elements(self, _by, query):
            if "我的" in str(query) and self.backs >= 2:
                return [self.element]
            return []

        def activate_app(self, _bundle_id):
            pass

        def terminate_app(self, _bundle_id):
            pass

    gateway = ToolGateway(
        RecoverDriver(),
        RuntimeConfig(target_app=TargetApp(platform="android", bundle_id="com.iqiyi.acg")),
    )
    result = gateway.call(
        "recover_page",
        {"until": {"type": "text", "value": "我的"}, "max_backs": 3, "timeout": 1},
    )
    assert result["ok"] is True
    assert result["data"]["via"] == "back"
    assert result["data"]["backs"] == 2


def test_observer_and_matcher_accept_reader_page():
    observation = PageObserver(FakeDriver(READER_XML)).observe()
    state = module_catalog.require("漫画阅读器").page_states[0]
    match = PageStateMatcher().match(state, observation)
    assert match.matched is True
    assert observation.package == "com.iqiyi.acg"
    assert any(
        element.resource_id == "tv_episode_title_water_mark"
        for element in observation.elements
    )


def test_matcher_rejects_forbidden_payment_dialog():
    xml = READER_XML.replace(
        "</hierarchy>",
        '<node text="立即支付" resource-id="" clickable="true" enabled="true"/>'
        "</hierarchy>",
    )
    observation = PageObserver(FakeDriver(xml)).observe()
    state = module_catalog.require("漫画阅读器").page_states[0]
    match = PageStateMatcher().match(state, observation)
    assert match.matched is False
    assert match.forbidden_hits


def test_matcher_rejects_wrong_activity():
    class WrongActivityDriver(FakeDriver):
        current_activity = ".MainActivity"

    observation = PageObserver(WrongActivityDriver(READER_XML)).observe()
    state = PageState(
        id="reader_main",
        package="com.iqiyi.acg",
        activity=".comic.creader.AcgCReaderActivity",
        required_any=[{"type": "id", "value": "tv_episode_title_water_mark"}],
    )

    match = PageStateMatcher().match(state, observation)

    assert match.matched is False
    assert "activity 不匹配" in match.reason


def test_step_guard_detects_content_change():
    before = PageObserver(FakeDriver(READER_XML)).observe()
    after = PageObserver(FakeDriver(READER_XML.replace("1/145", "2/145"))).observe()
    result = StepGuard().verify_postconditions(
        {"postconditions": ["page_content_changed"]},
        before,
        after,
    )
    assert result.ok is True


def test_tool_gateway_rejects_non_catalog_tool():
    gateway = ToolGateway(
        FakeDriver(READER_XML),
        RuntimeConfig(target_app=TargetApp(platform="android", bundle_id="com.iqiyi.acg")),
    )
    with pytest.raises(ToolGatewayError):
        gateway.call("shell", {"command": "rm -rf /"})
    observed = gateway.call("observe_page", {})
    assert observed["ok"] is True
    assert observed["data"]["package"] == "com.iqiyi.acg"


def test_tool_gateway_accepts_agent_resource_id_locator_shorthand():
    driver = FakeDriver(READER_XML)
    gateway = ToolGateway(
        driver,
        RuntimeConfig(target_app=TargetApp(platform="android", bundle_id="com.iqiyi.acg")),
    )

    result = gateway.call(
        "assert_visible",
        {"locator": "resource-id:com.iqiyi.acg:id/tv_episode_title_water_mark"},
    )

    assert result["ok"] is True


def test_page_observation_agent_payload_is_compact():
    xml = "<hierarchy>" + "".join(
        f'<node resource-id=\"id_{i}\" text=\"文本{i}\" clickable=\"true\" '
        'enabled=\"true\" displayed=\"true\"/>'
        for i in range(100)
    ) + "</hierarchy>"
    observation = PageObserver(FakeDriver(xml)).observe()

    payload = observation.as_agent_dict(max_elements=20)

    assert len(payload["elements"]) == 20
    assert "source" not in payload
    assert set(payload["elements"][0]) <= {
        "text",
        "resource_id",
        "accessibility_id",
        "clickable",
    }
