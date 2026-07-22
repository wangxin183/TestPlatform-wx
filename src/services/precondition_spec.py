"""结构化前置条件 precondition_spec 归一化、推断与指纹。"""

from __future__ import annotations

from typing import Any

from execution_runtime.testdata import VALID_ENTRY_CONTEXTS, load_execution_testdata

LOGIN_STATES = frozenset({"logged_in", "logged_out"})
USER_TYPES = frozenset({"member", "non_member", "guest"})


def empty_precondition_spec() -> dict[str, str]:
    return {
        "login_state": "logged_in",
        "user_type": "non_member",
        "entry_context": "module_default",
        "notes": "",
    }


def normalize_precondition_spec(raw: Any) -> dict[str, str]:
    """归一化为标准字段；非法值回落默认。"""
    base = empty_precondition_spec()
    if not isinstance(raw, dict):
        return base
    login = str(raw.get("login_state") or "").strip().lower()
    user = str(raw.get("user_type") or "").strip().lower()
    entry = str(raw.get("entry_context") or "").strip() or "module_default"
    notes = str(raw.get("notes") or "").strip()

    if login not in LOGIN_STATES:
        login = "logged_in"
    if login == "logged_out":
        user = "guest"
    elif user not in {"member", "non_member"}:
        user = "non_member"
    if entry not in VALID_ENTRY_CONTEXTS:
        entry = "module_default"

    return {
        "login_state": login,
        "user_type": user,
        "entry_context": entry,
        "notes": notes,
    }


def infer_precondition_spec(
    *,
    preconditions: str = "",
    module: str = "",
    title: str = "",
) -> dict[str, str]:
    """从中文前置/标题尽力推断；不臆造入口作品。"""
    text = f"{preconditions}；{title}"
    login = "logged_in"
    user = "non_member"
    if any(k in text for k in ("未登录", "游客", "未登陆")):
        login = "logged_out"
        user = "guest"
    elif any(k in text for k in ("已登录", "登录后", "登录态")):
        login = "logged_in"

    if login == "logged_in":
        if "非会员" in text:
            user = "non_member"
        elif "会员" in text and "非会员" not in text:
            user = "member"

    entry = "module_default"
    comic_map = [
        ("会员免费", "comic.member_free"),
        ("会员折扣", "comic.member_discount"),
        ("单点付费", "comic.pay_per_episode"),
        ("等免", "comic.wait_free"),
    ]
    for keyword, ctx in comic_map:
        if keyword in text:
            entry = ctx
            break
    else:
        if "免费" in text and ("漫画" in text or "阅读器" in (module or "")):
            entry = "comic.free"
        elif "左右" in text or "水平翻页" in text:
            entry = "reader.horizontal"
        elif "上下" in text or "垂直翻页" in text:
            entry = "reader.vertical"
        elif "航海王" in text or ("动画" in text and "会员" in text):
            entry = "anime.member_free"
        elif "开心锤锤" in text:
            entry = "anime.free"

    return normalize_precondition_spec(
        {
            "login_state": login,
            "user_type": user,
            "entry_context": entry,
            "notes": "inferred",
        }
    )


def ensure_precondition_spec(case: dict[str, Any]) -> dict[str, str]:
    raw = case.get("precondition_spec")
    if isinstance(raw, dict) and (
        raw.get("login_state") or raw.get("user_type") or raw.get("entry_context")
    ):
        return normalize_precondition_spec(raw)
    return infer_precondition_spec(
        preconditions=str(case.get("preconditions") or ""),
        module=str(case.get("module") or ""),
        title=str(case.get("title") or ""),
    )


def precondition_fingerprint(spec: dict[str, str], module: str = "") -> str:
    s = normalize_precondition_spec(spec)
    return "|".join(
        [
            s["login_state"],
            s["user_type"],
            s["entry_context"],
            (module or "").strip(),
        ]
    )


def login_fingerprint(spec: dict[str, str]) -> str:
    """仅登录相关指纹：登录与搜索入口可独立复用。"""
    s = normalize_precondition_spec(spec)
    return f"{s['login_state']}|{s['user_type']}"


def entry_fingerprint(spec: dict[str, str], module: str = "") -> str:
    """仅内容入口指纹。"""
    s = normalize_precondition_spec(spec)
    return f"{s['entry_context']}|{(module or '').strip()}"


def validate_precondition_spec(spec: dict[str, str]) -> list[dict[str, Any]]:
    """返回 severity=blocking|manual 的问题列表。"""
    errors: list[dict[str, Any]] = []
    s = normalize_precondition_spec(spec)
    testdata = load_execution_testdata()
    entry = s["entry_context"]
    if entry != "module_default":
        title = testdata.resolve_entry_title(entry)
        if not title:
            errors.append(
                {
                    "code": "ENTRY_TESTDATA_MISSING",
                    "message": f"entry_context={entry} 在测试数据中无对应作品",
                    "severity": "blocking",
                }
            )
    if s["login_state"] == "logged_in":
        cred = testdata.resolve_user(s["user_type"])
        if cred is None or not cred.username:
            errors.append(
                {
                    "code": "USER_TESTDATA_MISSING",
                    "message": f"user_type={s['user_type']} 缺少测试账号配置",
                    "severity": "blocking",
                }
            )
    return errors


def humanize_precondition_spec(spec: dict[str, str]) -> str:
    s = normalize_precondition_spec(spec)
    login_zh = "已登录" if s["login_state"] == "logged_in" else "未登录"
    user_zh = {
        "member": "会员",
        "non_member": "非会员",
        "guest": "游客",
    }.get(s["user_type"], s["user_type"])
    entry = s["entry_context"]
    title = load_execution_testdata().resolve_entry_title(entry)
    entry_zh = entry if entry == "module_default" else f"{entry}（{title or '?'}）"
    parts = [f"登录态:{login_zh}", f"用户:{user_zh}", f"入口:{entry_zh}"]
    if s.get("notes") and s["notes"] != "inferred":
        parts.append(s["notes"])
    return "；".join(parts)
