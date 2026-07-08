"""cli_shared — CLI 子进程执行、Token 估算、JSON 提取等无状态共享工具。

原来分散在 `src/services/agent_cli.py:AgentCLI` 中的静态方法在此集中，
供多种智能体后端（Claude Code / Codex 等）以及独立业务模块复用。

设计原则：
- 全部函数式，无实例状态；便于测试与并发。
- 返回值保持与旧 `AgentCLI` 完全一致（`CLICallResult` / `JSONExtractResult`）。
- 旧 `AgentCLI` 保留静态方法作为 re-export 兼容层，避免 import 断裂。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_CODE_BLOCK_TIMEOUT = 300


@dataclass
class CLICallResult:
    """CLI 命令执行结果。"""

    success: bool
    raw_output: str = ""
    error: str = ""
    exit_code: int = -1
    latency_ms: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class JSONExtractResult:
    """从文本中提取 JSON 的结果。"""

    success: bool
    data: Union[dict, list, None] = None
    error: str = ""
    extract_method: str = ""


def estimate_tokens(text: str) -> int:
    """启发式估算文本 token 数（中文 1.5/字，其他 0.3/字）。"""
    if not text:
        return 0

    cjk = 0
    other = 0
    for ch in text:
        if ('\u4e00' <= ch <= '\u9fff' or
                '\u3400' <= ch <= '\u4dbf' or
                '\uf900' <= ch <= '\ufaff'):
            cjk += 1
        else:
            other += 1

    return int(cjk * 1.5 + other * 0.3)


def dynamic_timeout(estimated_tokens: int) -> int:
    """根据 prompt token 数动态计算超时时间（秒）。"""
    if estimated_tokens < 5000:
        return 180
    if estimated_tokens < 30000:
        return 600
    return 900


async def run_cli_command(
    cmd: list[str],
    workdir: Optional[str],
    timeout: int,
    agent_name: str,
    stdin_input: Optional[str] = None,
) -> CLICallResult:
    """以子进程方式执行一次 CLI 命令，含超时与异常归一。

    Args:
        cmd: 命令 argv 列表。
        workdir: 子进程工作目录，None 时使用当前进程 cwd。
        timeout: 超时秒数。
        agent_name: 日志前缀（如 "claude" / "codex" / "cli"），仅用于结构化日志。
        stdin_input: 可选 stdin 输入文本（例如 codex 需要通过管道注入 prompt）。
    """
    cwd = workdir or str(Path.cwd())
    start = _loop_time()
    logger.info(
        f"{agent_name}_cli_start",
        cmd=" ".join(cmd),
        cwd=cwd,
        timeout=timeout,
        stdin_len=len(stdin_input) if stdin_input else 0,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if stdin_input else None,
            cwd=cwd,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(
                input=stdin_input.encode("utf-8") if stdin_input else None
            ),
            timeout=timeout,
        )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        latency_ms = int((_loop_time() - start) * 1000)

        logger.info(
            f"{agent_name}_cli_done",
            exit_code=proc.returncode,
            stdout_len=len(stdout),
            stderr_len=len(stderr),
            latency_ms=latency_ms,
        )

        if proc.returncode != 0:
            logger.warning(
                f"{agent_name}_cli_nonzero_exit",
                exit_code=proc.returncode,
                stderr=stderr[:500],
            )
            return CLICallResult(
                success=False,
                raw_output=stdout,
                error=f"退出码 {proc.returncode}: {stderr[:300]}",
                exit_code=proc.returncode,
                latency_ms=latency_ms,
                meta={"stderr": stderr, "stdout": stdout},
            )

        return CLICallResult(
            success=True,
            raw_output=stdout,
            exit_code=0,
            latency_ms=latency_ms,
            meta={"stderr": stderr},
        )

    except asyncio.TimeoutError:
        latency_ms = int((_loop_time() - start) * 1000)
        logger.error(f"{agent_name}_cli_timeout", timeout=timeout, latency_ms=latency_ms)
        return CLICallResult(
            success=False,
            error=f"命令执行超时（{timeout}秒）",
            latency_ms=latency_ms,
        )
    except FileNotFoundError:
        logger.error(f"{agent_name}_cli_not_found", cmd=cmd[0])
        return CLICallResult(
            success=False,
            error=f"未找到命令 '{cmd[0]}'，请确认已安装",
        )
    except Exception as exc:
        logger.error(f"{agent_name}_cli_error", error=str(exc))
        return CLICallResult(
            success=False,
            error=f"调用异常: {exc}",
        )


def extract_json(raw_output: str) -> JSONExtractResult:
    """从 LLM/CLI 原始文本中按 5 种策略依次提取 JSON。"""
    if not raw_output or not raw_output.strip():
        return JSONExtractResult(success=False, error="输出内容为空", extract_method="none")

    text = repair_json_text(raw_output.strip())

    result = _try_parse_json(text)
    if result.success:
        result.extract_method = "direct_parse"
        return result

    json_block_match = re.search(r"```\s*json\s*\n(.*?)\n```", text, re.DOTALL)
    if json_block_match:
        result = _try_parse_json(json_block_match.group(1).strip())
        if result.success:
            result.extract_method = "json_fence"
            return result

    generic_block_match = re.search(r"```\s*\n(.*?)\n```", text, re.DOTALL)
    if generic_block_match:
        result = _try_parse_json(generic_block_match.group(1).strip())
        if result.success:
            result.extract_method = "generic_fence"
            return result

    obj_match = _find_outermost_brace(text, "{", "}")
    if obj_match:
        result = _try_parse_json(obj_match)
        if result.success:
            result.extract_method = "brace_object_match"
            return result

    arr_match = _find_outermost_brace(text, "[", "]")
    if arr_match:
        result = _try_parse_json(arr_match)
        if result.success:
            result.extract_method = "brace_array_match"
            return result

    return JSONExtractResult(
        success=False,
        error="无法从输出中提取有效 JSON，已尝试 5 种策略",
        extract_method="all_failed",
    )


# 常见“agent 把 JSON 写到磁盘”的文件名候选；按优先级排序
DEFAULT_ARTIFACT_CANDIDATES: tuple[str, ...] = (
    "test_points_output.json",
    "self_heal_corrected_output_compact.json",
    "self_heal_corrected_output.json",
    "corrected_output.json",
    "analysis_output.json",
)


def recover_json_from_workdir(
    workdir: str | Path | None,
    *,
    raw_output: str = "",
    preferred_names: list[str] | tuple[str, ...] | None = None,
    require_key: str | None = None,
) -> JSONExtractResult:
    """当 stdout 无 JSON 时，尝试从 workdir 中的落盘文件恢复。

    Cursor 等 Agent 在输出过长时可能写成「JSON 已写入 xxx.json」而不返回 JSON。
    此函数按优先级扫描候选文件，并解析 raw_output 里提到的路径。
    """
    if not workdir:
        return JSONExtractResult(
            success=False,
            error="workdir 为空，无法从落盘文件恢复",
            extract_method="artifact_recover_skipped",
        )

    base = Path(workdir)
    if not base.exists() or not base.is_dir():
        return JSONExtractResult(
            success=False,
            error=f"workdir 不存在: {workdir}",
            extract_method="artifact_recover_skipped",
        )

    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        try:
            resolved = str(path.resolve())
        except Exception:
            resolved = str(path)
        if resolved in seen:
            return
        if path.exists() and path.is_file():
            seen.add(resolved)
            candidates.append(path)

    for name in preferred_names or DEFAULT_ARTIFACT_CANDIDATES:
        _add(base / name)

    # raw_output 里可能提到相对/绝对路径
    if raw_output:
        for m in re.finditer(
            r"[`'\"]?((?:storage/)?[^\s`'\"，。；;\n]+?\.json)[`'\"]?",
            raw_output,
        ):
            mentioned = m.group(1).strip()
            p = Path(mentioned)
            if not p.is_absolute():
                # 优先相对 workdir，其次相对仓库根（workdir 的上两级常见）
                _add(base / mentioned)
                _add(base / Path(mentioned).name)
                # 若路径形如 storage/requirement_analyses/RA-xxx/xxx.json
                parts = Path(mentioned).parts
                if "requirement_analyses" in parts:
                    try:
                        idx = parts.index(base.name)
                        rel = Path(*parts[idx + 1 :])
                        _add(base / rel)
                    except Exception:
                        pass
            else:
                _add(p)

    # 兜底：workdir 下近期生成的 json（排除任务状态文件）
    skip_names = {
        "task_state.json",
        f"{base.name}.json",  # 已合并的分析结果，不作为 agent raw 代理
    }
    try:
        recent = sorted(
            (
                p for p in base.glob("*.json")
                if p.name not in skip_names and not p.name.startswith("review_")
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for p in recent[:8]:
            _add(p)
    except Exception:
        pass

    errors: list[str] = []
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            errors.append(f"{path.name}: read_error={exc}")
            continue

        result = extract_json(text)
        if not result.success:
            errors.append(f"{path.name}: {result.error}")
            continue

        data = result.data
        # 诊断包装结构：{"diagnosis":..., "corrected_output": {...}}
        if isinstance(data, dict) and "corrected_output" in data and isinstance(
            data.get("corrected_output"), dict
        ):
            data = data["corrected_output"]

        if require_key and (not isinstance(data, dict) or require_key not in data):
            errors.append(f"{path.name}: missing_key={require_key}")
            continue

        logger.info(
            "json_recovered_from_artifact",
            path=str(path),
            require_key=require_key or "",
        )
        return JSONExtractResult(
            success=True,
            data=data,
            extract_method=f"artifact_file:{path.name}",
        )

    return JSONExtractResult(
        success=False,
        error="落盘 JSON 恢复失败: " + ("; ".join(errors[:5]) if errors else "无候选文件"),
        extract_method="artifact_recover_failed",
    )


def repair_json_text(text: str) -> str:
    """修复 LLM 输出 JSON 中字符串值内的未转义 ASCII 双引号。

    Codex 有时会在中文引号中输出 ASCII "，破坏 JSON 解析。此处词法分析
    识别 JSON 字符串边界，仅将值内部的非结束 " 替换为右弯引号 "。
    """
    has_cjk = any('\u4e00' <= c <= '\u9fff' for c in text)
    if not has_cjk:
        return text

    STRUCTURAL_FOLLOW_CHARS = set(',:}]')

    result: list[str] = []
    in_string = False
    escape_next = False
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue

        if in_string:
            if ch == '\\':
                result.append(ch)
                escape_next = True
            elif ch == '"':
                j = i + 1
                while j < n and text[j] in ' \t\n\r':
                    j += 1
                if j < n and text[j] in STRUCTURAL_FOLLOW_CHARS:
                    result.append(ch)
                    in_string = False
                else:
                    result.append('\u201d')
            else:
                result.append(ch)
        else:
            if ch == '"':
                in_string = True
            result.append(ch)

        i += 1

    return ''.join(result)


def _try_parse_json(text: str) -> JSONExtractResult:
    try:
        data = json.loads(text)
        if isinstance(data, (dict, list)):
            return JSONExtractResult(success=True, data=data)
        return JSONExtractResult(
            success=False,
            error=f"JSON 解析成功但类型为 {type(data).__name__}，期望 dict 或 list",
        )
    except json.JSONDecodeError as exc:
        return JSONExtractResult(success=False, error=str(exc))


def _find_outermost_brace(text: str, open_c: str, close_c: str) -> Optional[str]:
    start = text.find(open_c)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        ch = text[i]

        if escape_next:
            escape_next = False
            continue

        if ch == "\\" and in_string:
            escape_next = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    return None


def _loop_time() -> float:
    """获取事件循环当前时间（秒），用于延迟计算。"""
    try:
        return asyncio.get_event_loop().time()
    except RuntimeError:
        import time
        return time.monotonic()
