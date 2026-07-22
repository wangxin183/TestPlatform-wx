"""执行测试数据加载与 entry_context / 用户解析。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TESTDATA_PATH = _REPO_ROOT / "config" / "execution_testdata.yaml"

VALID_ENTRY_CONTEXTS = frozenset(
    {
        "module_default",
        "comic.member_free",
        "comic.member_discount",
        "comic.pay_per_episode",
        "comic.free",
        "comic.wait_free",
        "anime.member_free",
        "anime.free",
        "reader.horizontal",
        "reader.vertical",
    }
)


@dataclass
class UserCred:
    username: str = ""
    password: str = ""


@dataclass
class LoginConfig:
    auto_popup_on_mine_tab: bool = True
    security_check_timeout_seconds: int = 90
    app_ready_timeout_seconds: int = 30
    success_activity_substr: str = "ComicsMainActivity"
    login_activity_substr: str = "LiteAccountActivity"
    security_activity_substr: str = "PWebViewActivity"
    security_check_solver: str = "qwen_maas"
    security_check_model: str = "qwen3-vl-plus"
    security_check_max_attempts: int = 2
    guest_name_markers: list[str] = field(
        default_factory=lambda: [
            "戳我登录",
            "点击登录",
            "请登录",
            "立即登录",
            "登录/注册",
        ]
    )


@dataclass
class ExecutionTestdata:
    users: dict[str, UserCred] = field(default_factory=dict)
    comics: dict[str, str] = field(default_factory=dict)
    animes: dict[str, str] = field(default_factory=dict)
    reader_page_mode: dict[str, str] = field(default_factory=dict)
    selectors: dict[str, dict[str, str]] = field(default_factory=dict)
    login: LoginConfig = field(default_factory=LoginConfig)
    path: Path = DEFAULT_TESTDATA_PATH

    def selector(self, key: str) -> dict[str, str] | None:
        return self.selectors.get(key)

    def resolve_user(self, user_type: str) -> UserCred | None:
        key = {
            "member": "member",
            "non_member": "non_member",
            "guest": "",
        }.get(user_type, user_type)
        if not key:
            return None
        return self.users.get(key)

    def resolve_entry_title(self, entry_context: str) -> str | None:
        ctx = (entry_context or "").strip() or "module_default"
        if ctx == "module_default":
            return None
        if ctx.startswith("comic."):
            return self.comics.get(ctx.split(".", 1)[1])
        if ctx.startswith("anime."):
            return self.animes.get(ctx.split(".", 1)[1])
        if ctx.startswith("reader."):
            return self.reader_page_mode.get(ctx.split(".", 1)[1])
        return None

    def has_same_member_credentials(self) -> bool:
        member = self.users.get("member")
        non_member = self.users.get("non_member")
        if not member or not non_member:
            return False
        return (
            member.username == non_member.username
            and member.password == non_member.password
        )


def load_execution_testdata(path: Path | None = None) -> ExecutionTestdata:
    target = path or DEFAULT_TESTDATA_PATH
    raw: dict[str, Any] = {}
    if target.exists():
        with target.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

    users: dict[str, UserCred] = {}
    for key, val in (raw.get("users") or {}).items():
        if isinstance(val, dict):
            users[str(key)] = UserCred(
                username=str(val.get("username") or ""),
                password=str(val.get("password") or ""),
            )

    selectors: dict[str, dict[str, str]] = {}
    for key, val in (raw.get("selectors") or {}).items():
        if isinstance(val, dict) and val.get("type") and val.get("value"):
            selectors[str(key)] = {
                "type": str(val["type"]),
                "value": str(val["value"]),
            }

    login_raw = raw.get("login") or {}
    markers = login_raw.get("guest_name_markers")
    if not isinstance(markers, list) or not markers:
        markers = ["戳我登录", "点击登录", "请登录", "立即登录", "登录/注册"]
    login = LoginConfig(
        auto_popup_on_mine_tab=bool(login_raw.get("auto_popup_on_mine_tab", True)),
        security_check_timeout_seconds=int(
            login_raw.get("security_check_timeout_seconds") or 90
        ),
        app_ready_timeout_seconds=int(
            login_raw.get("app_ready_timeout_seconds") or 30
        ),
        success_activity_substr=str(
            login_raw.get("success_activity_substr") or "ComicsMainActivity"
        ),
        login_activity_substr=str(
            login_raw.get("login_activity_substr") or "LiteAccountActivity"
        ),
        security_activity_substr=str(
            login_raw.get("security_activity_substr") or "PWebViewActivity"
        ),
        security_check_solver=str(
            login_raw.get("security_check_solver") or "qwen_maas"
        ),
        security_check_model=str(
            login_raw.get("security_check_model") or "qwen3-vl-plus"
        ),
        security_check_max_attempts=int(
            login_raw.get("security_check_max_attempts") or 2
        ),
        guest_name_markers=[str(x) for x in markers if str(x).strip()],
    )

    return ExecutionTestdata(
        users=users,
        comics={str(k): str(v) for k, v in (raw.get("comics") or {}).items()},
        animes={str(k): str(v) for k, v in (raw.get("animes") or {}).items()},
        reader_page_mode={
            str(k): str(v) for k, v in (raw.get("reader_page_mode") or {}).items()
        },
        selectors=selectors,
        login=login,
        path=target,
    )
