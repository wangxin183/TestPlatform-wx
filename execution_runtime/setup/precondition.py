"""按 precondition_spec 执行登录态与内容入口 Setup。

登录与搜索必须分开：
- run_login_setup：拉起 App →「我的」登录 → 回到主频道
- run_entry_setup：主频道搜索 → 输入数据集作品名 → 动画/漫画 Tab → 阅读

登录配方：storage/setup_recordings/login_20260721_221510
搜索配方：storage/setup_recordings/search_comic_anime_20260722_015509
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from execution_runtime.testdata import ExecutionTestdata, load_execution_testdata
from src.services.precondition_spec import (
    ensure_precondition_spec,
    normalize_precondition_spec,
    precondition_fingerprint,
)


@dataclass
class SetupResult:
    ok: bool
    fingerprint: str
    warnings: list[str] = field(default_factory=list)
    message: str = ""
    entry_title: str | None = None


class PreconditionSetupError(RuntimeError):
    pass


def _call(gateway, tool: str, arguments: dict[str, Any] | None = None) -> dict:
    return gateway.call(tool, arguments or {})


def _exec_step(gateway, action: str, **kwargs: Any) -> str:
    """Setup 直调 StepExecutor，绕过 Agent 写操作「必须唯一且 enabled」校验。

    登录半屏记住手机号时 et_phone / tv_login 常为 enabled=False，Gateway 会误拒。
    """
    from execution_runtime.dsl.models import Step

    step = Step.model_validate({"action": action, "description": "precondition_setup", **kwargs})
    return gateway.executor.execute(step)


def _observe(gateway):
    return gateway.observer.observe()


def _blob(obs) -> str:
    parts = [obs.activity or "", obs.package or "", obs.source or ""]
    for el in obs.elements:
        parts.append(el.text or "")
        parts.append(el.resource_id or "")
        parts.append(el.accessibility_id or "")
    return " ".join(parts)


def _has_id(obs, resource_id: str) -> bool:
    rid = resource_id.split("/")[-1]
    return any(rid in (el.resource_id or "") for el in obs.elements) or (
        rid in (obs.source or "")
    )


def _has_text(obs, text: str) -> bool:
    if text in (obs.source or ""):
        return True
    return any(text == (el.text or "") or text in (el.text or "") for el in obs.elements)


def _activity_has(obs, substr: str) -> bool:
    return bool(substr) and substr in (obs.activity or "")


def _is_login_sheet(obs, data: ExecutionTestdata) -> bool:
    cfg = data.login
    if _activity_has(obs, cfg.login_activity_substr):
        return True
    if _has_id(obs, "et_phone"):
        return True
    title = data.selector("login_sheet_title")
    if title and _has_text(obs, title["value"]):
        return True
    return False


def _is_password_mode(obs) -> bool:
    return _has_id(obs, "et_pwd") or _has_text(obs, "短信登录")


def _is_security_check(obs, data: ExecutionTestdata) -> bool:
    cfg = data.login
    if _activity_has(obs, cfg.security_activity_substr):
        return True
    title = data.selector("security_check_title")
    if title and _has_text(obs, title["value"]):
        return True
    return "安全检测" in _blob(obs)


def _element_text_by_id(obs, resource_id: str) -> str:
    rid = resource_id.split("/")[-1]
    for el in obs.elements:
        if rid in (el.resource_id or ""):
            return (el.text or "").strip()
    return ""


def _is_guest_name(text: str, data: ExecutionTestdata) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return any(m in t for m in data.login.guest_name_markers)


def _is_logged_in(obs, data: ExecutionTestdata) -> bool:
    """已登录：我的页有真实昵称（非「戳我登录」），且不在登录半屏/安全检测。

    注意：未登录我的页同样有 avatar_view / nameTv，不能仅凭控件 id 判定。
    """
    if _is_login_sheet(obs, data) or _is_security_check(obs, data):
        return False
    blob = _blob(obs)
    if "退出登录" in blob or "退出账号" in blob:
        return True
    name_id = (data.selector("logged_in_name") or {}).get("value") or "nameTv"
    name_text = _element_text_by_id(obs, name_id)
    if name_text and not _is_guest_name(name_text, data):
        return True
    return False


def _protocol_checked(obs) -> bool:
    src = obs.source or ""
    m = re.search(
        r'resource-id="[^"]*psdk_cb_protocol_info"[^>]*checked="(true|false)"',
        src,
    )
    if m:
        return m.group(1) == "true"
    m2 = re.search(
        r'checked="(true|false)"[^>]*resource-id="[^"]*psdk_cb_protocol_info"',
        src,
    )
    return bool(m2 and m2.group(1) == "true")


def _safe_tap(gateway, locator: dict[str, str] | None, *, optional: bool = False) -> bool:
    if not locator:
        return False
    try:
        _exec_step(gateway, "tap", locator=locator)
        return True
    except Exception:
        if optional:
            return False
        raise


def _locator_visible(
    gateway,
    locator: dict[str, str],
    *,
    timeout: int = 3,
) -> bool:
    """短超时探测元素是否可见，避免默认 20s 拖慢恢复。"""
    try:
        _exec_step(gateway, "wait", until=locator, timeout=timeout)
        return True
    except Exception:
        return False


def _tap_with_recovery(
    gateway,
    locator: dict[str, str],
    *,
    data: ExecutionTestdata,
    warnings: list[str],
    label: str = "目标元素",
    max_backs: int = 3,
    probe_timeout: int = 3,
) -> None:
    """点击目标；找不到则 recover_page（回退最多 max_backs 次，必要时重拉）。"""
    from execution_runtime.tools.recovery import recover_page

    def _exists(loc: dict[str, str], timeout: int) -> bool:
        return _locator_visible(gateway, loc, timeout=timeout)

    def _execute(action: str, **kwargs: Any) -> Any:
        return _exec_step(gateway, action, **kwargs)

    result = recover_page(
        exists=_exists,
        execute=_execute,
        until=locator,
        max_backs=max_backs,
        relaunch=True,
        probe_timeout=probe_timeout,
        settle_seconds=float(max(2, int(data.login.app_ready_timeout_seconds) // 10)),
    )
    warnings.extend(result.warnings)
    if not result.ok:
        raise PreconditionSetupError(
            f"定位不到{label}（{result.message or 'recover_page 失败'}）"
        )
    if result.via == "back":
        warnings.append(f"回退后已定位{label}")
    elif result.via == "relaunch":
        warnings.append(f"重拉 App 后已定位{label}")
    _exec_step(gateway, "tap", locator=locator, timeout=probe_timeout)


def _is_app_foreground(obs, bundle_id: str) -> bool:
    pkg = (obs.package or "").strip()
    act = obs.activity or ""
    if bundle_id and pkg == bundle_id:
        return "Launcher" not in act and "NexusLauncher" not in act
    return "iqiyi.acg" in pkg or act.startswith(".biz.") or "acg" in act.lower()


def ensure_app_launched(
    gateway,
    testdata: ExecutionTestdata | None = None,
) -> list[str]:
    """前置：确保目标 App 在前台（不在桌面/闪屏）。"""
    warnings: list[str] = []
    data = testdata or load_execution_testdata()
    cfg = getattr(gateway, "cfg", None)
    target = getattr(cfg, "target_app", None) if cfg is not None else None
    bundle_id = str(getattr(target, "bundle_id", None) or "com.iqiyi.acg")
    obs = _observe(gateway)
    if not _is_app_foreground(obs, bundle_id):
        warnings.append(f"App 未在前台（activity={obs.activity}），执行 launch_app")
        _exec_step(gateway, "launch_app")

    timeout = max(5, int(data.login.app_ready_timeout_seconds))
    deadline = time.time() + timeout
    while time.time() < deadline:
        obs = _observe(gateway)
        act = obs.activity or ""
        if _is_app_foreground(obs, bundle_id) and "Splash" not in act:
            return warnings
        time.sleep(1.0)

    obs = _observe(gateway)
    if not _is_app_foreground(obs, bundle_id) or "Splash" in (obs.activity or ""):
        raise PreconditionSetupError(
            f"拉起 App 超时（activity={obs.activity}, package={obs.package}）"
        )
    return warnings


def _open_mine_tab(
    gateway,
    data: ExecutionTestdata,
    warnings: list[str] | None = None,
) -> None:
    """打开「我的」；阅读器等无底栏页会先回退/重拉再点。"""
    notes = warnings if warnings is not None else []
    my_tab = data.selector("my_tab") or {"type": "text", "value": "我的"}
    _tap_with_recovery(
        gateway,
        my_tab,
        data=data,
        warnings=notes,
        label="「我的」",
    )
    _exec_step(gateway, "wait", timeout=2)


def _ensure_login_sheet(gateway, data: ExecutionTestdata, warnings: list[str]) -> None:
    """点「我的」后等待自动弹窗；未弹出则点昵称「小伙伴，戳我登录」等入口。"""
    obs = _observe(gateway)
    if _is_login_sheet(obs, data):
        return

    if data.login.auto_popup_on_mine_tab:
        try:
            _exec_step(
                gateway,
                "wait",
                until=data.selector("phone_input")
                or {"type": "id", "value": "et_phone"},
                timeout=4,
            )
        except Exception:
            pass
        if _is_login_sheet(_observe(gateway), data):
            return

    warnings.append("我的 tab 未自动弹出登录半屏，尝试点击页面内登录入口")
    guest_entry = data.selector("guest_login_entry")
    name_id = data.selector("logged_in_name") or {"type": "id", "value": "nameTv"}
    fallbacks = [
        guest_entry,
        name_id,
        {"type": "text", "value": "点击登录"},
        {"type": "text", "value": "立即登录"},
        data.selector("logged_in_avatar") or {"type": "id", "value": "avatar_view"},
    ]
    for loc in fallbacks:
        if not loc:
            continue
        if _safe_tap(gateway, loc, optional=True):
            _exec_step(gateway, "wait", timeout=2)
            if _is_login_sheet(_observe(gateway), data):
                return

    if not _is_login_sheet(_observe(gateway), data):
        raise PreconditionSetupError("无法打开登录半屏（自动弹窗与兜底入口均失败）")


def _switch_to_password_login(gateway, data: ExecutionTestdata) -> None:
    obs = _observe(gateway)
    if _is_password_mode(obs):
        return
    layout = data.selector("switch_to_password_login_layout")
    text_loc = data.selector("switch_to_password_login") or {
        "type": "text",
        "value": "密码登录",
    }
    if not _safe_tap(gateway, layout, optional=True):
        _exec_step(gateway, "tap", locator=text_loc)
    _exec_step(gateway, "wait", timeout=1)
    if not _is_password_mode(_observe(gateway)):
        raise PreconditionSetupError("切换到密码登录失败")


def _phone_mask_matches(displayed: str, username: str) -> bool:
    """记住手机号常显示为 183****1330，与完整号比对。"""
    shown = (displayed or "").strip()
    phone = (username or "").strip()
    if not shown or not phone:
        return False
    if "*" not in shown:
        return shown == phone
    if len(shown) != len(phone):
        return False
    return all(a == "*" or a == b for a, b in zip(shown, phone))


def _fill_credentials(gateway, data: ExecutionTestdata, username: str, password: str) -> None:
    phone = data.selector("phone_input") or {"type": "id", "value": "et_phone"}
    pwd = data.selector("password_input") or {"type": "id", "value": "et_pwd"}
    phone_clear = data.selector("phone_clear") or {
        "type": "id",
        "value": "img_delete_b",
    }

    obs = _observe(gateway)
    phone_text = _element_text_by_id(obs, phone["value"])
    need_phone = not _phone_mask_matches(phone_text, username)
    if need_phone:
        _safe_tap(gateway, phone_clear, optional=True)
        _exec_step(gateway, "tap", locator=phone)
        try:
            _exec_step(gateway, "clear", locator=phone)
        except Exception:
            pass
        try:
            _exec_step(gateway, "input", locator=phone, value=username)
        except Exception as exc:
            _safe_tap(gateway, phone_clear, optional=True)
            try:
                _exec_step(gateway, "input", locator=phone, value=username)
            except Exception:
                raise PreconditionSetupError(
                    f"无法填写手机号（当前展示={phone_text!r}）: {exc}"
                ) from exc

    _exec_step(gateway, "tap", locator=pwd)
    try:
        _exec_step(gateway, "clear", locator=pwd)
    except Exception:
        pass
    _exec_step(gateway, "input", locator=pwd, value=password)

    if not _protocol_checked(_observe(gateway)):
        agree = data.selector("agree_protocol") or {
            "type": "id",
            "value": "psdk_cb_protocol_info",
        }
        _exec_step(gateway, "tap", locator=agree)
        _exec_step(gateway, "wait", timeout=1)

    submit = data.selector("password_login_submit") or {
        "type": "id",
        "value": "tv_login",
    }
    _exec_step(gateway, "tap", locator=submit)


def _wait_login_success(
    gateway,
    data: ExecutionTestdata,
    warnings: list[str],
) -> None:
    """等待登录成功；若出现安全检测则用 Vision 自动点选（非每次必有）。"""
    from execution_runtime.setup.security_check import solve_security_check

    timeout = max(5, int(data.login.security_check_timeout_seconds))
    deadline = time.time() + timeout
    saw_security = False
    solved = False

    while time.time() < deadline:
        obs = _observe(gateway)
        if _is_logged_in(obs, data):
            if saw_security:
                warnings.append("安全检测已通过，登录成功")
            return
        if _is_security_check(obs, data):
            if not saw_security:
                warnings.append("出现安全检测，调用 Vision 自动点选")
                saw_security = True
            if not solved and data.login.security_check_solver in {
                "qwen_maas",
                "vision",
                "qwen_vl",
            }:
                extra = solve_security_check(gateway, data)
                warnings.extend(extra)
                solved = True
                continue
        time.sleep(1.5)

    obs = _observe(gateway)
    if _is_logged_in(obs, data):
        return
    if saw_security or _is_security_check(obs, data):
        raise PreconditionSetupError(
            f"登录卡在安全检测超过 {timeout}s（Vision 未通过或未配置 DASHSCOPE_API_KEY）"
        )
    raise PreconditionSetupError(
        f"登录后未检测到已登录态（activity={obs.activity}）"
    )


def ensure_login_state(
    gateway,
    spec: dict[str, str],
    testdata: ExecutionTestdata | None = None,
) -> list[str]:
    """尽量对齐登录态；同号会员不严格阻断。返回 warnings。"""
    warnings: list[str] = []
    data = testdata or load_execution_testdata()
    s = normalize_precondition_spec(spec)
    obs = _observe(gateway)

    if s["login_state"] == "logged_out":
        if _is_logged_in(obs, data):
            try:
                _open_mine_tab(gateway, data, warnings)
                logout = data.selector("logout_button")
                if logout:
                    _exec_step(gateway, "tap", locator=logout)
            except Exception as exc:  # noqa: BLE001
                raise PreconditionSetupError(f"退出登录失败: {exc}") from exc
            if _is_logged_in(_observe(gateway), data):
                raise PreconditionSetupError("目标未登录，但退出后仍显示已登录态")
        return warnings

    # logged_in
    if _is_logged_in(obs, data):
        pass
    else:
        cred = data.resolve_user(s["user_type"])
        if cred is None or not cred.username:
            raise PreconditionSetupError(f"缺少 {s['user_type']} 测试账号")
        try:
            _open_mine_tab(gateway, data, warnings)
            if _is_logged_in(_observe(gateway), data):
                # 点我的后发现已登录（会话仍在）
                pass
            else:
                _ensure_login_sheet(gateway, data, warnings)
                _switch_to_password_login(gateway, data)
                _fill_credentials(gateway, data, cred.username, cred.password)
                _wait_login_success(gateway, data, warnings)
        except PreconditionSetupError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PreconditionSetupError(f"登录流程失败: {exc}") from exc

    if data.has_same_member_credentials() and s["user_type"] in {
        "member",
        "non_member",
    }:
        warnings.append(
            "测试数据会员/非会员同号：跳过严格会员身份校验（precondition_warning）"
        )
    return warnings


def _entry_tab_label(entry_context: str) -> str:
    """comic/reader → 漫画；anime → 动画。"""
    ctx = (entry_context or "").strip()
    if ctx.startswith("anime."):
        return "动画"
    return "漫画"


def _first_bounds_for_id(source: str, resource_id: str) -> tuple[int, int, int, int] | None:
    """从 page_source 取第一个匹配 resource-id 的 bounds。"""
    rid = resource_id.split("/")[-1]
    patterns = [
        rf'resource-id="[^"]*{re.escape(rid)}"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        rf'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*resource-id="[^"]*{re.escape(rid)}"',
    ]
    for pat in patterns:
        m = re.search(pat, source or "")
        if m:
            return tuple(int(m.group(i)) for i in range(1, 5))  # type: ignore[return-value]
    return None


def _wait_until(gateway, predicate, *, timeout: float = 12.0, interval: float = 0.6) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate(_observe(gateway)):
            return True
        time.sleep(interval)
    return False


def _tap_first_result_read(gateway, data: ExecutionTestdata) -> None:
    """点击搜索结果首条「立即阅读/开始阅读」。

    录制可见按钮 text 常 clickable=false，优先文案/id，失败则按 bounds tap_xy。
    """
    candidates: list[dict[str, str]] = []
    for key in ("search_result_read", "search_result_read_alt", "search_result_read_id"):
        loc = data.selector(key)
        if loc:
            candidates.append(loc)
    for text in ("立即阅读", "开始阅读", "立即观看"):
        candidates.append({"type": "text", "value": text})
    candidates.append({"type": "id", "value": "result_common_one_btn_left_text"})

    seen: set[str] = set()
    for loc in candidates:
        key = f"{loc.get('type')}:{loc.get('value')}"
        if key in seen:
            continue
        seen.add(key)
        if _safe_tap(gateway, loc, optional=True):
            return

    obs = _observe(gateway)
    bounds = _first_bounds_for_id(obs.source or "", "result_common_one_btn_left_text")
    if not bounds:
        bounds = _first_bounds_for_id(obs.source or "", "result_common_one_btn_left_layout")
    if bounds:
        x = (bounds[0] + bounds[2]) // 2
        y = (bounds[1] + bounds[3]) // 2
        _exec_step(gateway, "tap_xy", x=x, y=y)
        return
    raise PreconditionSetupError("搜索结果未找到「立即阅读/开始阅读」按钮")


def _has_search_result_page(obs) -> bool:
    """搜索结果页（带「全部」Tab 或结果卡片），区别于首页底栏「动画/漫画」。"""
    if _has_text(obs, "全部") and (
        _has_text(obs, "动画") or _has_text(obs, "漫画")
    ):
        return True
    if _has_id(obs, "result_common_one_btn_left_text"):
        return True
    if _has_id(obs, "result_common_list_item1"):
        return True
    if _has_text(obs, "立即阅读") or _has_text(obs, "开始阅读"):
        return True
    return False


def _first_suggest_bounds(
    source: str, *, preferred_text: str | None = None
) -> tuple[int, int, int, int] | None:
    """解析搜索联想词 suggest_text 的 bounds；优先精确匹配 preferred_text。"""
    nodes = re.findall(
        r'<[^>]*resource-id="[^"]*suggest_text"[^>]*>',
        source or "",
    )
    if not nodes:
        nodes = re.findall(
            r'<[^>]*text="[^"]*"[^>]*resource-id="[^"]*suggest_text"[^>]*>',
            source or "",
        )

    def _bounds_of(node: str) -> tuple[int, int, int, int] | None:
        m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        if not m:
            return None
        return tuple(int(m.group(i)) for i in range(1, 5))  # type: ignore[return-value]

    if preferred_text:
        for node in nodes:
            tm = re.search(r'text="([^"]*)"', node)
            if tm and tm.group(1) == preferred_text:
                b = _bounds_of(node)
                if b:
                    return b
    for node in nodes:
        b = _bounds_of(node)
        if b:
            return b
    return None


def _adb_keyevent(gateway, keycode: int) -> bool:
    import shutil
    import subprocess

    adb = shutil.which("adb")
    if not adb:
        return False
    cmd = [adb]
    cfg = getattr(gateway, "cfg", None)
    if cfg is not None:
        try:
            device = getattr(cfg, "device", None)
            udid = str(getattr(device, "udid", "") or "").strip()
            if udid:
                cmd.extend(["-s", udid])
        except Exception:
            pass
    cmd.extend(["shell", "input", "keyevent", str(int(keycode))])
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=8)
        return True
    except Exception:
        return False


def _submit_search_query(gateway, title: str, warnings: list[str]) -> None:
    """输入后提交搜索：点第一个关联词，失败则按键盘回车。"""
    if _has_search_result_page(_observe(gateway)):
        return

    _wait_until(
        gateway,
        lambda obs: _has_id(obs, "suggest_text") or _has_search_result_page(obs),
        timeout=6,
    )
    if _has_search_result_page(_observe(gateway)):
        return

    bounds = _first_suggest_bounds(_observe(gateway).source or "", preferred_text=title)
    if not bounds:
        bounds = _first_suggest_bounds(_observe(gateway).source or "")
    if bounds:
        x = (bounds[0] + bounds[2]) // 2
        y = (bounds[1] + bounds[3]) // 2
        _exec_step(gateway, "tap_xy", x=x, y=y)
        warnings.append(f"已点击搜索关联词（优先「{title}」）")
        _exec_step(gateway, "wait", timeout=2)
        if _wait_until(gateway, _has_search_result_page, timeout=8):
            return

    if _adb_keyevent(gateway, 66):  # KEYCODE_ENTER
        warnings.append("关联词未生效，已按键盘回车提交搜索")
        _exec_step(gateway, "wait", timeout=2)
        if _wait_until(gateway, _has_search_result_page, timeout=8):
            return

    raise PreconditionSetupError(
        f"搜索「{title}」后未能提交（关联词/回车均未进入结果页）"
    )


def ensure_entry_context(
    gateway,
    spec: dict[str, str],
    *,
    module: str,
    testdata: ExecutionTestdata | None = None,
) -> SetupResult:
    """按 entry_context 从数据集取搜索词，搜索并进入内容。

    录制流程（search_comic_anime_20260722_015509）:
      拉起 App → 点搜索 → 输入作品名 → 点关联词/回车 → 点「动画/漫画」Tab → 点首条阅读按钮
    搜索词由 comics/animes/reader_page_mode 按 entry_context 动态解析。
    """
    data = testdata or load_execution_testdata()
    s = normalize_precondition_spec(spec)
    fp = precondition_fingerprint(s, module)
    title = data.resolve_entry_title(s["entry_context"])
    warnings: list[str] = []

    if s["entry_context"] == "module_default" or not title:
        return SetupResult(
            ok=True,
            fingerprint=fp,
            message="module_default：由 prepare_module_session 处理入口",
            entry_title=None,
        )

    search_input = data.selector("search_input") or {"type": "id", "value": "input_box"}
    tab_label = _entry_tab_label(s["entry_context"])
    tab_key = "search_tab_anime" if tab_label == "动画" else "search_tab_comic"
    tab_loc = data.selector(tab_key) or {"type": "text", "value": tab_label}

    try:
        # 实机：im_search 可定位但点击无效；search_flipper 才能进搜索页。
        # 每次点击后必须校验是否进入，不能把「点到了」当成「打开了」。
        entry_candidates: list[dict[str, str]] = []
        for key in ("search_entry", "search_entry_flipper", "search_entry_icon"):
            loc = data.selector(key)
            if loc and loc not in entry_candidates:
                entry_candidates.append(loc)
        for loc in (
            {"type": "id", "value": "search_flipper"},
            {"type": "id", "value": "search_container"},
            {"type": "id", "value": "im_search"},
            {"type": "text", "value": "搜索"},
        ):
            if loc not in entry_candidates:
                entry_candidates.append(loc)

        opened = False
        used_entry: dict[str, str] | None = None
        for loc in entry_candidates:
            if not _safe_tap(gateway, loc, optional=True):
                continue
            _exec_step(gateway, "wait", timeout=1)
            if _wait_until(
                gateway,
                lambda obs: _has_id(obs, "input_box")
                or _activity_has(obs, "AcgSearchActivity")
                or _has_id(obs, search_input["value"]),
                timeout=4,
            ):
                opened = True
                used_entry = loc
                break
        if not opened:
            raise PreconditionSetupError(
                "未能进入搜索页（已尝试 search_flipper/im_search 等入口）"
            )
        if used_entry:
            warnings.append(
                f"已通过搜索入口打开搜索页: {used_entry.get('type')}={used_entry.get('value')}"
            )

        _exec_step(gateway, "tap", locator=search_input)
        try:
            _exec_step(gateway, "clear", locator=search_input)
        except Exception:
            pass
        _exec_step(gateway, "input", locator=search_input, value=title)
        warnings.append(f"搜索词已从数据集注入: {s['entry_context']} →「{title}」")
        _exec_step(gateway, "wait", timeout=1)
        _submit_search_query(gateway, title, warnings)

        if not _wait_until(gateway, _has_search_result_page, timeout=10):
            raise PreconditionSetupError(f"搜索「{title}」后未出现结果页")

        _exec_step(gateway, "tap", locator=tab_loc)
        warnings.append(f"已切换搜索结果 Tab：「{tab_label}」")
        _exec_step(gateway, "wait", timeout=2)

        _tap_first_result_read(gateway, data)
        _exec_step(gateway, "wait", timeout=2)

        obs = _observe(gateway)
        if not (
            _activity_has(obs, "AcgCReaderActivity")
            or _activity_has(obs, "NormalVideoActivity")
            or _activity_has(obs, "Reader")
            or _activity_has(obs, "Player")
            or _activity_has(obs, "Video")
            or _has_id(obs, "comic_info_root_layout")
        ):
            warnings.append(
                f"已点击阅读按钮，但未确认进入内容页（activity={obs.activity}）"
            )
        else:
            warnings.append(f"已进入内容页 activity={obs.activity}")
    except PreconditionSetupError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PreconditionSetupError(
            f"按作品「{title}」进入失败 ({s['entry_context']}): {exc}"
        ) from exc

    return SetupResult(
        ok=True,
        fingerprint=fp,
        message=f"已搜索并进入作品「{title}」（{tab_label}）",
        entry_title=title,
        warnings=warnings,
    )


def _has_search_entry(obs) -> bool:
    return _has_id(obs, "im_search") or _has_id(obs, "search_flipper") or _has_id(
        obs, "search_container"
    )


def _goto_home_with_search(
    gateway,
    data: ExecutionTestdata,
    *,
    prefer_anime: bool = False,
) -> list[str]:
    """离开阅读器/「我的」/搜索页，回到带搜索入口的主频道（漫画/动画）。"""
    warnings: list[str] = []
    obs = _observe(gateway)
    if _has_search_entry(obs) and not _activity_has(obs, "AcgSearchActivity"):
        return warnings

    # 阅读器/详情/搜索页没有底栏，先 back 几次
    for _ in range(5):
        obs = _observe(gateway)
        if _has_search_entry(obs) and not _activity_has(obs, "AcgSearchActivity"):
            return warnings
        act = obs.activity or ""
        if any(
            x in act
            for x in (
                "AcgCReaderActivity",
                "Reader",
                "Player",
                "AcgSearchActivity",
                "PhonePayActivity",
            )
        ) or _has_id(obs, "comic_info_root_layout"):
            try:
                _exec_step(gateway, "back")
            except Exception:
                break
            _exec_step(gateway, "wait", timeout=1)
            warnings.append(f"已从 {act or '内容页'} 返回")
            continue
        break

    tabs: list[dict[str, str]] = []
    anime = data.selector("home_tab_anime") or {"type": "text", "value": "动画"}
    comic = data.selector("home_tab_comic") or {"type": "text", "value": "漫画"}
    if prefer_anime:
        tabs.extend([anime, comic])
    else:
        tabs.extend([comic, anime])

    for loc in tabs:
        if _safe_tap(gateway, loc, optional=True):
            _exec_step(gateway, "wait", timeout=1)
            obs = _observe(gateway)
            if _has_search_entry(obs):
                warnings.append(f"已回到主频道「{loc.get('value')}」准备搜索")
                return warnings

    # 仍无入口时再试一次漫画
    if _safe_tap(gateway, comic, optional=True):
        _exec_step(gateway, "wait", timeout=1)
    if not _has_search_entry(_observe(gateway)):
        raise PreconditionSetupError("未能回到带搜索入口的首页（im_search）")
    warnings.append("已回到主频道准备搜索")
    return warnings


def run_login_setup(
    gateway,
    case: dict[str, Any] | Any,
    *,
    module: str = "",
) -> SetupResult:
    """仅登录 Setup：拉起 App → 对齐登录态 → 回到可搜索首页。不执行搜索。"""
    payload = _case_payload(case, module)
    module_name = module or str(payload.get("module") or "")
    spec = ensure_precondition_spec({**payload, "module": module_name})
    data = load_execution_testdata()
    warnings = ensure_app_launched(gateway, data)
    warnings.extend(ensure_login_state(gateway, spec, testdata=data))
    prefer_anime = str(spec.get("entry_context") or "").startswith("anime.")
    warnings.extend(
        _goto_home_with_search(gateway, data, prefer_anime=prefer_anime)
    )
    fp = precondition_fingerprint(spec, module_name)
    return SetupResult(
        ok=True,
        fingerprint=fp,
        message="登录 Setup 完成（未执行搜索入口）",
        warnings=warnings,
    )


def run_entry_setup(
    gateway,
    case: dict[str, Any] | Any,
    *,
    module: str = "",
) -> SetupResult:
    """仅内容入口 Setup：回到首页搜索区 → 按 entry_context 搜索进入。不执行登录。"""
    payload = _case_payload(case, module)
    module_name = module or str(payload.get("module") or "")
    spec = ensure_precondition_spec({**payload, "module": module_name})
    data = load_execution_testdata()
    prefer_anime = str(spec.get("entry_context") or "").startswith("anime.")
    warnings = _goto_home_with_search(gateway, data, prefer_anime=prefer_anime)
    result = ensure_entry_context(gateway, spec, module=module_name, testdata=data)
    result.warnings = warnings + list(result.warnings)
    return result


def _case_payload(case: dict[str, Any] | Any, module: str) -> dict[str, Any]:
    if hasattr(case, "model_dump"):
        return case.model_dump()
    if isinstance(case, dict):
        return case
    return {
        "precondition_spec": getattr(case, "precondition_spec", None),
        "preconditions": getattr(case, "preconditions", ""),
        "module": getattr(case, "module", module),
        "title": getattr(case, "title", ""),
    }


def run_precondition_setup(
    gateway,
    case: dict[str, Any] | Any,
    *,
    module: str = "",
) -> SetupResult:
    """编排 Setup：登录流程与搜索流程分开执行。

    1) run_login_setup（我的 / 登录 / 回首页）
    2) run_entry_setup（搜索 / Tab / 阅读）— entry=module_default 时跳过搜索
    """
    payload = _case_payload(case, module)
    module_name = module or str(payload.get("module") or "")
    spec = ensure_precondition_spec({**payload, "module": module_name})

    login = run_login_setup(gateway, payload, module=module_name)
    entry = run_entry_setup(gateway, payload, module=module_name)
    return SetupResult(
        ok=login.ok and entry.ok,
        fingerprint=precondition_fingerprint(spec, module_name),
        message=f"{login.message}；{entry.message}",
        entry_title=entry.entry_title,
        warnings=list(login.warnings) + list(entry.warnings),
    )
