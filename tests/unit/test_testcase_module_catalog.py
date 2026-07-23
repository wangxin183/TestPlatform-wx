"""ACN 模块目录与页面状态契约单测。"""

from __future__ import annotations

from src.core.models.models import TestCase as ORMTestCase
from src.services.testcase_generation_service import _serialize_db_case
from src.services.testcase_module_catalog import (
    ACNModuleCatalog,
    canonicalize_module_name,
)


def test_catalog_contains_all_summary_modules():
    catalog = ACNModuleCatalog.load()
    assert catalog.names == [
        "Push",
        "个人主页",
        "动漫频道",
        "动画半播页",
        "图文帖详情页",
        "圈子",
        "安装启动",
        "我的",
        "搜索",
        "消息",
        "漫单详情页",
        "漫画详情页",
        "漫画阅读器",
        "漫荒详情页",
        "短视频横屏播放",
        "短视频竖屏播放",
        "社区",
        "管控演练",
        "视频帖子详情页",
        "追更",
        "长图帖详情页",
    ]


def test_canonicalize_owner_suffix_and_alias():
    catalog = ACNModuleCatalog.load()
    assert canonicalize_module_name("漫画阅读器（潘媛）") == "漫画阅读器"
    assert catalog.resolve("阅读器-左右翻页") == "漫画阅读器"
    assert catalog.resolve("进入漫画tab查看作品") == "动漫频道"


def test_manga_reader_uses_page_anchor_without_invented_detail_route():
    module = ACNModuleCatalog.load().get("漫画阅读器")
    assert module is not None
    assert "点击漫画卡片可直接进入漫画阅读器" in module.entry_nl
    assert module.entry_steps == []
    assert module.page_states
    state = module.page_states[0]
    assert state.id == "reader_main"
    assert state.package == "com.iqiyi.acg"
    assert state.activity == ".comic.creader.AcgCReaderActivity"
    assert {"type": "id", "value": "reader_root"} in state.required_all
    assert {"type": "id", "value": "fragment_read_real"} in state.required_all
    assert state.required_any
    member_state = next(
        item for item in module.page_states if item.id == "external:会员页"
    )
    assert member_state.activity == "com.iqiyi.vipcashier.activity.PhonePayActivity"
    assert {"type": "id", "value": "vip_gold_page"} in member_state.required_all


def test_unknown_module_is_rejected():
    catalog = ACNModuleCatalog.load()
    assert catalog.get("不存在模块") is None
    assert catalog.resolve("完全无法映射的场景") == ""


def test_testcase_has_module_and_dual_execution_fields():
    columns = ORMTestCase.__table__.columns
    for name in (
        "module",
        "exec_script",
        "compile_status",
        "compile_errors",
        "execution_mode",
        "step_contracts",
    ):
        assert name in columns


def test_all_general_testcase_serializers_expose_execution_fields():
    case = ORMTestCase(
        title="模块用例",
        steps=[{"step": 1, "action": "点击", "expected": "显示"}],
        test_type="ui",
        platform_type="android",
        module="搜索",
        compile_status="agent_required",
        compile_errors=[{"code": "AMBIGUOUS_TARGET", "message": "目标不唯一"}],
        execution_mode="agent",
        step_contracts=[{"step": 1, "start_state": "search_main"}],
    )
    data = _serialize_db_case(case)
    assert data["module"] == "搜索"
    assert data["compile_status"] == "agent_required"
    assert data["execution_mode"] == "agent"
    assert data["step_contracts"][0]["start_state"] == "search_main"
