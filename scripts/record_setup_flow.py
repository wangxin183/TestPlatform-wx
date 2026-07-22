#!/usr/bin/env python3
"""录制 Setup 流程：你在模拟器上操作，本脚本自动抓取页面变化。

用法:
  .venv/bin/python -m scripts.record_setup_flow --name login
  .venv/bin/python -m scripts.record_setup_flow --name search_comic --manual

默认自动模式：检测到 page_source 变化并稳定后自动存一帧。
手动模式：每步操作完后在终端按 Enter 存帧；输入 q 结束。

产物目录: storage/setup_recordings/<name>_<timestamp>/
  events.jsonl       每帧摘要
  frames/NNN.png     截图
  frames/NNN.xml     page_source
  recipe_draft.yaml  从可点击控件提炼的草稿步骤（需人工校对）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from execution_runtime.config import load_config
from execution_runtime.engine.appium_driver import build_driver


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_source(src: str) -> str:
    return hashlib.sha256((src or "").encode("utf-8")).hexdigest()[:16]


def _parse_interesting(source: str) -> list[dict]:
    if not source:
        return []
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        return []
    items: list[dict] = []
    for node in root.iter():
        a = node.attrib
        text = (a.get("text") or "").strip()
        desc = (a.get("content-desc") or "").strip()
        rid = (a.get("resource-id") or "").strip()
        clickable = (a.get("clickable") or "").lower() == "true"
        if not (text or desc or (rid and clickable)):
            continue
        if not clickable and not text and not desc:
            continue
        items.append(
            {
                "text": text,
                "content_desc": desc,
                "resource_id": rid,
                "class": a.get("class") or "",
                "clickable": clickable,
                "bounds": a.get("bounds") or "",
            }
        )
        if len(items) >= 80:
            break
    return items


def _suggest_steps(frames: list[dict]) -> list[dict]:
    """相邻帧之间，用「新出现的可点击文案/id」粗生成草稿步骤。"""
    steps: list[dict] = []
    prev_keys: set[str] = set()
    for fr in frames:
        keys = set()
        candidates: list[dict] = []
        for el in fr.get("elements") or []:
            key = f"{el.get('resource_id')}|{el.get('text')}|{el.get('content_desc')}"
            keys.add(key)
            if not el.get("clickable"):
                continue
            if key in prev_keys:
                continue
            candidates.append(el)
        # 取新出现的可点击控件前几个
        for el in candidates[:3]:
            if el.get("resource_id") and ":id/" in el["resource_id"]:
                short = el["resource_id"].split(":id/")[-1]
                steps.append(
                    {
                        "action": "tap",
                        "locator": {"type": "id", "value": short},
                        "note": el.get("text") or el.get("content_desc") or short,
                        "frame": fr.get("index"),
                    }
                )
            elif el.get("text"):
                steps.append(
                    {
                        "action": "tap",
                        "locator": {"type": "text", "value": el["text"][:40]},
                        "note": el["text"][:40],
                        "frame": fr.get("index"),
                    }
                )
            elif el.get("content_desc"):
                steps.append(
                    {
                        "action": "tap",
                        "locator": {
                            "type": "accessibility_id",
                            "value": el["content_desc"][:40],
                        },
                        "note": el["content_desc"][:40],
                        "frame": fr.get("index"),
                    }
                )
        prev_keys = keys
    # 去重连续相同 locator
    deduped: list[dict] = []
    last = None
    for s in steps:
        sig = json.dumps(s.get("locator"), ensure_ascii=False, sort_keys=True)
        if sig == last:
            continue
        deduped.append(s)
        last = sig
    return deduped


def _capture(driver, out_dir: Path, index: int, note: str = "") -> dict:
    frames = out_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    pkg = str(getattr(driver, "current_package", "") or "")
    act = str(getattr(driver, "current_activity", "") or "")
    try:
        source = driver.page_source or ""
    except Exception:
        source = ""
    shot = frames / f"{index:03d}.png"
    xml_path = frames / f"{index:03d}.xml"
    try:
        driver.get_screenshot_as_file(str(shot))
    except Exception:
        shot = Path("")
    xml_path.write_text(source, encoding="utf-8")
    elements = _parse_interesting(source)
    event = {
        "index": index,
        "ts": _utcnow(),
        "note": note,
        "package": pkg,
        "activity": act,
        "source_hash": _hash_source(source),
        "screenshot": str(shot) if shot else "",
        "page_source": str(xml_path),
        "elements": elements,
    }
    with (out_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
        # events 里不塞完整 elements 过大；另存 summary
        slim = {k: v for k, v in event.items() if k != "elements"}
        slim["element_count"] = len(elements)
        slim["top_texts"] = [
            e.get("text") or e.get("content_desc") or e.get("resource_id")
            for e in elements[:15]
            if e.get("text") or e.get("content_desc") or e.get("resource_id")
        ]
        fh.write(json.dumps(slim, ensure_ascii=False) + "\n")
    (frames / f"{index:03d}_elements.json").write_text(
        json.dumps(elements, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description="录制 Setup 操作流程")
    parser.add_argument("--name", default="login", help="录制名，如 login / search_comic")
    parser.add_argument(
        "--manual",
        action="store_true",
        help="手动模式：每步按 Enter 抓帧；默认自动检测 UI 变化",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=1.2,
        help="自动模式：页面稳定等待秒数",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=0.8,
        help="自动模式：轮询间隔秒数",
    )
    parser.add_argument(
        "--launch",
        action="store_true",
        default=True,
        help="启动被测 App（默认开启）",
    )
    parser.add_argument("--no-launch", action="store_true", help="不启动 App")
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = REPO / "storage" / "setup_recordings" / f"{args.name}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    print(f"连接 Appium {cfg.device.appium_url} / {cfg.device.udid} ...", flush=True)
    driver = build_driver(cfg)
    try:
        if args.launch and not args.no_launch:
            try:
                driver.activate_app(cfg.target_app.bundle_id)
                time.sleep(2)
            except Exception as exc:
                print(f"activate_app 警告: {exc}", flush=True)

        print("=" * 60, flush=True)
        print(f"录制已开始 → {out_dir}", flush=True)
        print(f"流程名: {args.name}", flush=True)
        if args.manual:
            print("手动模式：在模拟器操作一步后，回到此终端按 Enter 抓帧；输入 q 结束。", flush=True)
        else:
            print(
                "自动模式：请在模拟器上慢慢操作登录/搜索流程；"
                "页面变化稳定后会自动存帧。终端输入 q + Enter 结束。",
                flush=True,
            )
        print("=" * 60, flush=True)

        frames: list[dict] = []
        index = 0
        # 首帧
        ev = _capture(driver, out_dir, index, note="start")
        frames.append(ev)
        print(f"[{index:03d}] 起始页 activity={ev['activity']} hash={ev['source_hash']}", flush=True)
        last_hash = ev["source_hash"]
        pending_hash = None
        pending_since = 0.0

        if args.manual:
            while True:
                line = input("操作完按 Enter 抓帧（q 结束）> ").strip().lower()
                if line in {"q", "quit", "exit"}:
                    break
                index += 1
                ev = _capture(driver, out_dir, index, note=line or "step")
                frames.append(ev)
                print(
                    f"[{index:03d}] activity={ev['activity']} hash={ev['source_hash']} "
                    f"texts={ev.get('elements', [])[:0]}",
                    flush=True,
                )
                top = [
                    e.get("text") or e.get("content_desc")
                    for e in (ev.get("elements") or [])[:8]
                    if e.get("text") or e.get("content_desc")
                ]
                print(f"       top: {top}", flush=True)
        else:
            stop_file = out_dir / "STOP"
            print(f"提示：结束请创建空文件: touch {stop_file}", flush=True)
            print("      或对本进程发 Ctrl+C", flush=True)
            try:
                while not stop_file.exists():
                    time.sleep(args.poll)
                    try:
                        src = driver.page_source or ""
                    except Exception:
                        continue
                    h = _hash_source(src)
                    now = time.time()
                    if h != last_hash:
                        if h != pending_hash:
                            pending_hash = h
                            pending_since = now
                        elif now - pending_since >= args.settle:
                            index += 1
                            ev = _capture(driver, out_dir, index, note="auto")
                            frames.append(ev)
                            last_hash = h
                            pending_hash = None
                            top = [
                                e.get("text") or e.get("content_desc")
                                for e in (ev.get("elements") or [])[:8]
                                if e.get("text") or e.get("content_desc")
                            ]
                            print(
                                f"[{index:03d}] 自动抓帧 activity={ev['activity']} "
                                f"hash={h} top={top}",
                                flush=True,
                            )
            except KeyboardInterrupt:
                print("\n收到中断，正在收尾...", flush=True)
            if stop_file.exists():
                try:
                    stop_file.unlink()
                except Exception:
                    pass

        recipe = {
            "name": args.name,
            "recorded_at": _utcnow(),
            "out_dir": str(out_dir),
            "frame_count": len(frames),
            "draft_steps": _suggest_steps(frames),
            "notes": "草稿步骤仅供参考，请结合 frames/*.xml 校对后写入 setup recipes",
        }
        (out_dir / "recipe_draft.yaml").write_text(
            yaml.safe_dump(recipe, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        (out_dir / "summary.json").write_text(
            json.dumps(
                {
                    "name": args.name,
                    "frames": len(frames),
                    "activities": [f.get("activity") for f in frames],
                    "out_dir": str(out_dir),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"完成：共 {len(frames)} 帧 → {out_dir}", flush=True)
        print(f"请查看 recipe_draft.yaml 与 frames/", flush=True)
        return 0
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
