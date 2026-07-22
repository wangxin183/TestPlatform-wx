from __future__ import annotations

import json

from execution_runtime.navigation.path_cache import NavigationPathCache


def test_navigation_path_cache_round_trip_and_invalidate(tmp_path) -> None:
    cache = NavigationPathCache(tmp_path / "paths.json")
    actions = [
        {"tool": "tap", "arguments": {"locator": {"type": "text", "value": "漫画"}}},
        {
            "tool": "tap",
            "arguments": {"locator": {"type": "id", "value": "comic_card"}},
        },
    ]

    cache.save(
        app_id="com.iqiyi.acg",
        module="漫画阅读器",
        start_package="com.iqiyi.acg",
        start_activity=".MainActivity",
        actions=actions,
    )

    assert cache.load(
        app_id="com.iqiyi.acg",
        module="漫画阅读器",
        start_package="com.iqiyi.acg",
        start_activity=".MainActivity",
    ) == actions
    cache.invalidate(
        app_id="com.iqiyi.acg",
        module="漫画阅读器",
        start_package="com.iqiyi.acg",
        start_activity=".MainActivity",
    )
    assert cache.load(
        app_id="com.iqiyi.acg",
        module="漫画阅读器",
        start_package="com.iqiyi.acg",
        start_activity=".MainActivity",
    ) == []


def test_navigation_path_cache_corrupt_file_degrades_to_empty(tmp_path) -> None:
    path = tmp_path / "paths.json"
    path.write_text("{broken", encoding="utf-8")
    cache = NavigationPathCache(path)

    assert cache.load(
        app_id="app",
        module="搜索",
        start_package="app",
        start_activity=".Main",
    ) == []
    cache.save(
        app_id="app",
        module="搜索",
        start_package="app",
        start_activity=".Main",
        actions=[{"tool": "back", "arguments": {}}],
    )
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
