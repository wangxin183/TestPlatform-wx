"""前置条件与测试数据单测。"""

from __future__ import annotations

from execution_runtime.testdata import load_execution_testdata
from src.services.precondition_spec import (
    ensure_precondition_spec,
    infer_precondition_spec,
    normalize_precondition_spec,
    precondition_fingerprint,
)
from src.services.testcase_contract_compiler import (
    prepare_executable_case,
    score_assertion_quality,
)
from execution_runtime.config import RuntimeConfig, TargetApp
from execution_runtime.runner import _sort_cases_for_execution


def _cfg() -> RuntimeConfig:
    return RuntimeConfig(
        target_app=TargetApp(
            name="爱奇艺叭嗒",
            platform="android",
            bundle_id="com.iqiyi.acg",
        )
    )


def test_testdata_resolves_comic_and_users():
    data = load_execution_testdata()
    assert data.resolve_entry_title("comic.member_free") == "兽黑狂妃"
    assert data.resolve_entry_title("reader.horizontal") == "元尊"
    member = data.resolve_user("member")
    assert member is not None
    assert member.username == "18304071330"
    assert data.has_same_member_credentials() is True


def test_infer_precondition_from_chinese():
    spec = infer_precondition_spec(
        preconditions="已使用非会员账号登录 App",
        module="漫画阅读器",
        title="会员免费漫画会员条",
    )
    assert spec["login_state"] == "logged_in"
    assert spec["user_type"] == "non_member"
    assert spec["entry_context"] == "comic.member_free"


def test_fingerprint_groups_cases():
    cases = [
        {
            "case_id": "a",
            "module": "漫画阅读器",
            "precondition_spec": {
                "login_state": "logged_in",
                "user_type": "non_member",
                "entry_context": "comic.member_free",
            },
        },
        {
            "case_id": "b",
            "module": "搜索",
            "precondition_spec": {
                "login_state": "logged_in",
                "user_type": "non_member",
                "entry_context": "module_default",
            },
        },
        {
            "case_id": "c",
            "module": "漫画阅读器",
            "precondition_spec": {
                "login_state": "logged_in",
                "user_type": "non_member",
                "entry_context": "comic.member_free",
            },
        },
    ]
    ordered = _sort_cases_for_execution(cases)
    assert [c["case_id"] for c in ordered] == ["a", "c", "b"]


def test_prepare_sets_precondition_and_assertion_quality():
    prepared = prepare_executable_case(
        {
            "case_id": "c1",
            "title": "搜索指定漫画",
            "module": "搜索",
            "preconditions": "已登录非会员",
            "steps": [
                {
                    "step": 1,
                    "action": "在搜索框输入「航海王」",
                    "expected": "显示「航海王」搜索结果",
                },
            ],
        },
        _cfg(),
    )
    assert prepared["compile_status"] == "ok"
    assert prepared["precondition_spec"]["login_state"] == "logged_in"
    assert prepared["assertion_quality"] in {"strong", "adequate"}
    assert prepared["exec_script"]["precondition_spec"]


def test_score_assertion_quality_strong_with_quote_and_transition():
    quality = score_assertion_quality(
        [
            {
                "action_kind": "tap",
                "start_state": "reader_main",
                "expected_transition": "reader_main -> external:会员页",
                "postconditions": ["text_visible:开通会员", "page_changed"],
            }
        ]
    )
    assert quality == "strong"


def test_normalize_logged_out_forces_guest():
    spec = normalize_precondition_spec(
        {"login_state": "logged_out", "user_type": "member", "entry_context": "x"}
    )
    assert spec["user_type"] == "guest"
    assert spec["entry_context"] == "module_default"


def test_ensure_precondition_spec_prefers_explicit():
    spec = ensure_precondition_spec(
        {
            "precondition_spec": {
                "login_state": "logged_in",
                "user_type": "member",
                "entry_context": "anime.free",
            },
            "preconditions": "未登录",
        }
    )
    assert spec["user_type"] == "member"
    assert spec["entry_context"] == "anime.free"
    assert precondition_fingerprint(spec, "动画半播页").endswith("|动画半播页")


def test_testdata_loads_login_recipe_selectors():
    data = load_execution_testdata()
    assert data.selector("et_phone") is None  # key is phone_input
    assert data.selector("phone_input")["value"] == "et_phone"
    assert data.selector("password_input")["value"] == "et_pwd"
    assert data.login.auto_popup_on_mine_tab is True
    assert data.login.security_check_timeout_seconds >= 30


class _FakeEl:
    def __init__(self, text="", resource_id="", accessibility_id=""):
        self.text = text
        self.resource_id = resource_id
        self.accessibility_id = accessibility_id


class _FakeObs:
    def __init__(self, activity="", elements=None, source=""):
        self.activity = activity
        self.package = "com.iqiyi.acg"
        self.elements = elements or []
        self.source = source


def _obs_home():
    return _FakeObs(activity=".biz.cartoon.main.ComicsMainActivity", elements=[])


def _obs_sms(checked="false"):
    return _FakeObs(
        activity="org.qiyi.android.video.ui.account.lite.LiteAccountActivity",
        elements=[
            _FakeEl(text="登录后观影更流畅"),
            _FakeEl(resource_id="com.iqiyi.acg:id/et_phone"),
            _FakeEl(text="密码登录"),
        ],
        source=f'resource-id="com.iqiyi.acg:id/psdk_cb_protocol_info" checked="{checked}"',
    )


def _obs_pwd(checked="false"):
    return _FakeObs(
        activity="org.qiyi.android.video.ui.account.lite.LiteAccountActivity",
        elements=[
            _FakeEl(resource_id="com.iqiyi.acg:id/et_phone"),
            _FakeEl(resource_id="com.iqiyi.acg:id/et_pwd"),
            _FakeEl(text="短信登录"),
            _FakeEl(resource_id="com.iqiyi.acg:id/tv_login", text="登录"),
        ],
        source=f'resource-id="com.iqiyi.acg:id/psdk_cb_protocol_info" checked="{checked}"',
    )


def _obs_security():
    return _FakeObs(
        activity="org.qiyi.android.video.ui.account.inspection.PWebViewActivity",
        elements=[_FakeEl(text="安全检测")],
    )


def _obs_guest_mine():
    return _FakeObs(
        activity=".biz.cartoon.main.ComicsMainActivity",
        elements=[
            _FakeEl(resource_id="com.iqiyi.acg:id/avatar_view"),
            _FakeEl(
                resource_id="com.iqiyi.acg:id/nameTv",
                text="小伙伴，戳我登录",
            ),
            _FakeEl(text="我的"),
        ],
    )


def _obs_logged_in():
    return _FakeObs(
        activity=".biz.cartoon.main.ComicsMainActivity",
        elements=[
            _FakeEl(resource_id="com.iqiyi.acg:id/avatar_view"),
            _FakeEl(resource_id="com.iqiyi.acg:id/nameTv", text="测试号"),
        ],
    )


class _LoginFakeGateway:
    """按登录配方阶段返回页面观察，供 ensure_login_state 单测。"""

    def __init__(
        self,
        *,
        with_security: bool = True,
        start: str = "home",
        backs_to_home: int = 1,
    ):
        self.phase = start
        self.with_security = with_security
        self.calls = []
        self.protocol_checked = False
        self._security_seen = 0
        self._back_count = 0
        self.backs_to_home = backs_to_home
        self.observer = self
        self.executor = self
        self.cfg = type(
            "Cfg",
            (),
            {"target_app": type("App", (), {"bundle_id": "com.iqiyi.acg"})()},
        )()

    def observe(self):
        if self.phase == "launcher":
            return _FakeObs(activity=".NexusLauncherActivity", elements=[])
        if self.phase == "reader":
            return _FakeObs(
                activity=".comic.creader.AcgCReaderActivity",
                elements=[
                    _FakeEl(resource_id="com.iqiyi.acg:id/comic_info_root_layout")
                ],
            )
        if self.phase == "home":
            return _obs_home()
        if self.phase == "guest_mine":
            return _obs_guest_mine()
        if self.phase == "sms":
            return _obs_sms()
        if self.phase == "pwd":
            return _obs_pwd("true" if self.protocol_checked else "false")
        if self.phase == "security":
            self._security_seen += 1
            if self._security_seen >= 2:
                self.phase = "done"
                return _obs_logged_in()
            return _obs_security()
        if self.phase == "done":
            return _obs_logged_in()
        return _obs_home()

    def execute(self, step):
        """兼容 _exec_step → gateway.executor.execute(step)。"""
        args = {
            "locator": step.locator.model_dump() if step.locator else None,
            "value": step.value,
            "until": step.until.model_dump() if step.until else None,
            "timeout": step.timeout,
        }
        return self.call(step.action, args)

    def call(self, tool, arguments=None):
        args = dict(arguments or {})
        self.calls.append((tool, args))
        loc = args.get("locator") or {}
        val = str(loc.get("value") or "")

        if tool == "launch_app":
            self.phase = "home"
        elif tool == "terminate_app":
            self.phase = "launcher"
        elif tool == "back":
            self._back_count += 1
            if self.phase == "reader" and self._back_count >= self.backs_to_home:
                self.phase = "home"
        elif tool == "wait":
            until = args.get("until") or {}
            until_val = str(until.get("value") or "")
            if until_val == "et_phone" and self.phase != "sms":
                raise RuntimeError("wait until et_phone timeout")
            if until_val in {"我的", "tv_menu_item_title"} and self.phase not in {
                "home",
                "guest_mine",
                "done",
                "launcher",
            }:
                raise RuntimeError("wait until 我的 timeout")
        elif tool == "tap":
            if val in {"我的", "tv_menu_item_title"} and self.phase in {
                "home",
                "launcher",
            }:
                self.phase = "guest_mine"
            elif val in {
                "小伙伴，戳我登录",
                "nameTv",
                "avatar_view",
                "点击登录",
                "立即登录",
            }:
                self.phase = "sms"
            elif val in {"密码登录", "other_lite_pwd_sms_login_layout"}:
                self.phase = "pwd"
            elif "psdk_cb_protocol" in val:
                self.protocol_checked = True
            elif val == "tv_login":
                self.phase = "security" if self.with_security else "done"
        return {"ok": True}


def test_phone_mask_matches():
    from execution_runtime.setup.precondition import _phone_mask_matches

    assert _phone_mask_matches("183****1330", "18304071330") is True
    assert _phone_mask_matches("183****1330", "18304071331") is False
    assert _phone_mask_matches("18304071330", "18304071330") is True


def test_security_check_scale_and_parse():
    from execution_runtime.setup.security_check import (
        _parse_plan,
        _repair_vision_json_text,
        _scale_from_norm1000,
        _scale_point,
    )

    clicks, confirm = _parse_plan(
        {
            "clicks": [[100, 200], [300, 400]],
            "confirm": [500, 600],
        }
    )
    assert clicks == [(100, 200), (300, 400)]
    assert confirm == (500, 600)
    repaired = _repair_vision_json_text(
        '{"clicks":[{"x": 496, 752}],"confirm":{"x": 540, 820}}'
    )
    assert '"y": 752' in repaired and '"y": 820' in repaired
    assert _scale_point(100, 200, img_w=1080, img_h=2400, win_w=1080, win_h=2400) == (
        100,
        200,
    )
    scaled, conf = _scale_from_norm1000(
        [(500, 833)], (504, 833), img_w=1080, img_h=2400
    )
    assert scaled == [(540, 1999)]
    assert conf == (544, 1999)


def test_solve_security_check_mocked(monkeypatch):
    from execution_runtime.setup import security_check as sc
    from execution_runtime.testdata import load_execution_testdata

    data = load_execution_testdata()
    data.login.security_check_max_attempts = 1

    class El:
        def __init__(self, text="", resource_id=""):
            self.text = text
            self.resource_id = resource_id
            self.accessibility_id = ""

    class ObsSec:
        activity = "org.qiyi.android.video.ui.account.inspection.PWebViewActivity"
        package = "com.iqiyi.acg"
        elements = []
        source = "安全检测 请在下图依次点击"

    class ObsDone:
        activity = ".biz.cartoon.main.ComicsMainActivity"
        package = "com.iqiyi.acg"
        elements = [El(text="测试号", resource_id="com.iqiyi.acg:id/nameTv")]
        source = ""

    class Gw:
        def __init__(self):
            self.done = False
            self.taps = []
            self.driver = self
            self.executor = self
            self.observer = self

        def observe(self):
            return ObsDone() if self.done else ObsSec()

        def get_screenshot_as_png(self):
            import io

            from PIL import Image

            buf = io.BytesIO()
            Image.new("RGB", (1080, 2400), color=(40, 40, 40)).save(buf, format="PNG")
            return buf.getvalue()

        def get_window_size(self):
            return {"width": 1080, "height": 2400}

        def execute(self, step):
            if step.action == "tap_xy":
                self.taps.append((step.x, step.y))
                if len(self.taps) >= 3:
                    self.done = True
                return "ok"
            raise RuntimeError("locator not found")

    async def fake_ask(png, *, img_w, img_h, model, gateway=None):
        # qwen3-vl-plus 使用 0~1000 相对坐标
        return {
            "clicks": [[300, 650], [600, 720]],
            "confirm": [500, 830],
        }

    monkeypatch.setattr(sc, "_ask_vision", fake_ask)
    gw = Gw()
    warnings = sc.solve_security_check(gw, data)
    assert any("自动通过" in w for w in warnings)
    assert len(gw.taps) >= 3


def test_ensure_login_skips_when_already_logged_in():
    from execution_runtime.setup.precondition import ensure_login_state

    data = load_execution_testdata()
    gw = _LoginFakeGateway()
    gw.phase = "done"
    warnings = ensure_login_state(
        gw,
        {
            "login_state": "logged_in",
            "user_type": "member",
            "entry_context": "module_default",
        },
        testdata=data,
    )
    assert any("同号" in w for w in warnings)
    assert not any(c[0] == "input" for c in gw.calls)


def test_ensure_login_password_flow_and_optional_security():
    from execution_runtime.setup.precondition import ensure_login_state

    data = load_execution_testdata()
    data.login.security_check_timeout_seconds = 8
    gw = _LoginFakeGateway(with_security=True)
    warnings = ensure_login_state(
        gw,
        {
            "login_state": "logged_in",
            "user_type": "member",
            "entry_context": "module_default",
        },
        testdata=data,
    )
    tools = [c[0] for c in gw.calls]
    assert "tap" in tools
    assert "input" in tools
    assert "clear" in tools
    assert gw.phase == "done"
    assert any("未自动弹出" in w for w in warnings)
    assert any("安全检测" in w for w in warnings)


def test_ensure_login_without_security_check():
    from execution_runtime.setup.precondition import ensure_login_state

    data = load_execution_testdata()
    data.login.security_check_timeout_seconds = 5
    gw = _LoginFakeGateway(with_security=False)
    warnings = ensure_login_state(
        gw,
        {
            "login_state": "logged_in",
            "user_type": "member",
            "entry_context": "module_default",
        },
        testdata=data,
    )
    assert gw.phase == "done"
    assert not any("安全检测" in w for w in warnings)


def test_ensure_app_launched_from_launcher():
    from execution_runtime.setup.precondition import ensure_app_launched

    data = load_execution_testdata()
    gw = _LoginFakeGateway(start="launcher")
    warnings = ensure_app_launched(gw, data)
    assert any("launch_app" in w for w in warnings)
    assert gw.phase == "home"
    assert any(c[0] == "launch_app" for c in gw.calls)


def test_ensure_login_recovers_from_reader_by_back():
    """EXE-0016：卡在阅读器时点「我的」失败，回退后应能继续登录。"""
    from execution_runtime.setup.precondition import ensure_login_state

    data = load_execution_testdata()
    data.login.security_check_timeout_seconds = 5
    gw = _LoginFakeGateway(with_security=False, start="reader", backs_to_home=1)
    warnings = ensure_login_state(
        gw,
        {
            "login_state": "logged_in",
            "user_type": "member",
            "entry_context": "module_default",
        },
        testdata=data,
    )
    assert gw.phase == "done"
    assert any("回退" in w for w in warnings)
    assert any(c[0] == "back" for c in gw.calls)
    assert not any(c[0] == "terminate_app" for c in gw.calls)


def test_ensure_login_relaunch_after_backs_exhausted():
    """回退 3 次仍找不到「我的」时，杀掉 App 重拉。"""
    from execution_runtime.setup.precondition import ensure_login_state

    data = load_execution_testdata()
    data.login.security_check_timeout_seconds = 5
    # 回退永远回不到首页，触发 terminate + launch
    gw = _LoginFakeGateway(with_security=False, start="reader", backs_to_home=99)
    warnings = ensure_login_state(
        gw,
        {
            "login_state": "logged_in",
            "user_type": "member",
            "entry_context": "module_default",
        },
        testdata=data,
    )
    assert gw.phase == "done"
    assert sum(1 for c in gw.calls if c[0] == "back") >= 3
    assert any(c[0] == "terminate_app" for c in gw.calls)
    assert any(c[0] == "launch_app" for c in gw.calls)
    assert any("杀掉 App" in w or "重新拉起" in w for w in warnings)


def test_login_and_entry_fingerprints_are_independent():
    from src.services.precondition_spec import entry_fingerprint, login_fingerprint

    a = {
        "login_state": "logged_in",
        "user_type": "member",
        "entry_context": "anime.member_free",
    }
    b = {
        "login_state": "logged_in",
        "user_type": "member",
        "entry_context": "comic.member_free",
    }
    assert login_fingerprint(a) == login_fingerprint(b)
    assert entry_fingerprint(a, "动画") != entry_fingerprint(b, "漫画阅读器")


def test_run_entry_setup_does_not_open_mine():
    from execution_runtime.setup.precondition import run_entry_setup

    data = load_execution_testdata()
    taps: list[str] = []

    class Obs:
        def __init__(self):
            self.activity = ".biz.cartoon.main.ComicsMainActivity"
            self.package = "com.iqiyi.acg"
            self.source = (
                'resource-id="com.iqiyi.acg:id/im_search" text="漫画" text="动画" '
                'text="立即阅读" resource-id="com.iqiyi.acg:id/input_box" '
                'resource-id="com.iqiyi.acg:id/result_common_one_btn_left_text" '
                'bounds="[10,10][20,20]"'
            )
            self.elements = []

    class Gw:
        def __init__(self):
            self.executor = self
            self.observer = self
            self.stage = "home"

        def observe(self):
            return Obs()

        def execute(self, step):
            val = getattr(step.locator, "value", None) if step.locator else None
            if step.action == "tap" and val:
                taps.append(val)
            if step.action == "input":
                taps.append(f"input:{step.value}")
            return "ok"

    result = run_entry_setup(
        Gw(),
        {
            "precondition_spec": {
                "login_state": "logged_in",
                "user_type": "member",
                "entry_context": "module_default",
            },
            "module": "搜索",
        },
        module="搜索",
    )
    assert result.ok
    assert "我的" not in taps


def test_ensure_entry_context_search_flow():
    """录制流程：搜索 → 输入数据集作品名 → Tab → 立即阅读。"""
    from execution_runtime.setup.precondition import ensure_entry_context

    data = load_execution_testdata()
    actions: list[str] = []

    class Obs:
        def __init__(self, activity="", source="", elements=None):
            self.activity = activity
            self.package = "com.iqiyi.acg"
            self.source = source
            self.elements = elements or []

    class El:
        def __init__(self, text="", resource_id=""):
            self.text = text
            self.resource_id = resource_id
            self.accessibility_id = ""

    class Gw:
        def __init__(self):
            self.stage = "home"
            self.executor = self
            self.observer = self

        def observe(self):
            if self.stage == "home":
                return Obs(
                    ".biz.cartoon.main.ComicsMainActivity",
                    'resource-id="com.iqiyi.acg:id/im_search" '
                    'resource-id="com.iqiyi.acg:id/search_flipper"',
                    [
                        El(resource_id="com.iqiyi.acg:id/im_search"),
                        El(resource_id="com.iqiyi.acg:id/search_flipper"),
                    ],
                )
            if self.stage == "search":
                return Obs(
                    ".searchcomponent.AcgSearchActivity",
                    'resource-id="com.iqiyi.acg:id/input_box"',
                    [El(resource_id="com.iqiyi.acg:id/input_box")],
                )
            if self.stage == "suggest":
                return Obs(
                    ".searchcomponent.AcgSearchActivity",
                    '<android.widget.EditText text="航海王" '
                    'resource-id="com.iqiyi.acg:id/input_box" bounds="[40,100][500,160]" />'
                    '<android.widget.TextView text="航海王" '
                    'resource-id="com.iqiyi.acg:id/suggest_text" '
                    'bounds="[40,200][400,260]" />',
                    [
                        El(resource_id="com.iqiyi.acg:id/input_box", text="航海王"),
                        El(resource_id="com.iqiyi.acg:id/suggest_text", text="航海王"),
                    ],
                )
            if self.stage == "results":
                return Obs(
                    ".searchcomponent.AcgSearchActivity",
                    'text="全部" text="动画" text="漫画" text="立即阅读" '
                    'resource-id="com.iqiyi.acg:id/result_common_one_btn_left_text" '
                    'bounds="[427,673][555,719]"',
                    [
                        El(text="全部"),
                        El(text="动画"),
                        El(text="漫画"),
                        El(text="立即阅读", resource_id="com.iqiyi.acg:id/result_common_one_btn_left_text"),
                    ],
                )
            return Obs(
                ".comic.creader.AcgCReaderActivity",
                'resource-id="com.iqiyi.acg:id/comic_info_root_layout"',
                [El(resource_id="com.iqiyi.acg:id/comic_info_root_layout")],
            )

        def execute(self, step):
            actions.append(f"{step.action}:{getattr(step.locator, 'value', None) or step.value or ''}")
            # 实机：im_search 点了无效，只有 search_flipper 能进搜索页
            if step.action == "tap" and step.locator and step.locator.value in {
                "search_flipper",
                "搜索",
            }:
                self.stage = "search"
            elif step.action == "tap" and step.locator and step.locator.value == "im_search":
                pass
            elif step.action == "input":
                self.stage = "suggest"
            elif step.action == "tap_xy":
                self.stage = "results"
            elif step.action == "tap" and step.locator and step.locator.value in {
                "动画",
                "漫画",
                "立即阅读",
                "开始阅读",
            }:
                if step.locator.value in {"立即阅读", "开始阅读"}:
                    self.stage = "reader"
            return "ok"

    gw = Gw()
    result = ensure_entry_context(
        gw,
        {
            "login_state": "logged_in",
            "user_type": "member",
            "entry_context": "anime.member_free",
        },
        module="动画",
        testdata=data,
    )
    assert result.ok
    assert result.entry_title == "航海王"
    assert any("search_flipper" in a for a in actions)
    assert any(a.startswith("input:") for a in actions)
    assert any(a.startswith("tap_xy:") for a in actions)
    assert any(a.endswith(":动画") or ":动画" in a for a in actions)
    assert any("立即阅读" in a or "开始阅读" in a for a in actions)
    assert any("数据集注入" in w and "航海王" in w for w in result.warnings)
    assert any("关联词" in w for w in result.warnings)
    # 确认输入值来自 animes.member_free
    assert data.resolve_entry_title("anime.member_free") == "航海王"
