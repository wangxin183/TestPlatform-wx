"""Agent CLI — 封装 Claude Code 和 Codex 的命令行调用。

统一封装 subprocess 调用、JSON 提取、超时控制和错误处理。
用于需求分析节点中 Claude Code（执行分析）和 Codex（独立审查）的调用。

Usage:
    from src.services.agent_cli import AgentCLI

    cli = AgentCLI()
    result = await cli.claude(prompt="分析需求文档...", workdir="/tmp/work")
    json_data = cli.extract_json(result)
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ============================================================
# 配置常量
# ============================================================

DEFAULT_TIMEOUT_SECONDS = 600  # 10 分钟，需求分析可能较长
DEFAULT_CODE_BLOCK_TIMEOUT = 300  # 5 分钟
BINARY_LOOKUP = {
    "claude": "claude",
    "codex": "codex",
}


@dataclass
class CLICallResult:
    """CLI 调用结果"""
    success: bool
    raw_output: str = ""
    error: str = ""
    exit_code: int = -1
    latency_ms: int = 0
    meta: dict = field(default_factory=dict)  # 额外元数据（如 token 信息）


@dataclass
class JSONExtractResult:
    """JSON 提取结果"""
    success: bool
    data: dict | list | None = None
    error: str = ""
    extract_method: str = ""  # 使用的提取方法


# ============================================================
# AgentCLI — 核心类
# ============================================================

class AgentCLI:
    """封装 Claude Code CLI 和 Codex CLI 的调用。

    每个实例可持有独立的配置（如不同的 timeout、workdir）。
    """

    def __init__(
        self,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        workdir: str | None = None,
    ):
        self.timeout = timeout_seconds
        self.workdir = workdir
        self._verify_binaries()

    def _verify_binaries(self) -> None:
        """验证 CLI 二进制文件可用，记录不可用的命令"""
        for name, bin_name in BINARY_LOOKUP.items():
            if shutil.which(bin_name) is None:
                logger.warning("cli_binary_missing", name=name, binary=bin_name)
            else:
                logger.debug("cli_binary_found", name=name, binary=bin_name)

    # ---- Token 估算 ----

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """估算文本的 token 数量（Claude 模型启发式方法）。

        中文/日文/韩文字符：约 1.5 token/字符
        英文/其他字符：约 0.3 token/字符（约 3-4 字符/token）

        采用保守估计（略微高估），避免上下文窗口溢出。
        """
        if not text:
            return 0

        cjk = 0
        other = 0
        for ch in text:
            # CJK 统一汉字 + 扩展区段 + 兼容区段
            if ('一' <= ch <= '鿿' or   # CJK Unified Ideographs
                '㐀' <= ch <= '䶿' or    # CJK Extension A
                '豈' <= ch <= '﫿'):     # CJK Compatibility Ideographs
                cjk += 1
            else:
                other += 1

        return int(cjk * 1.5 + other * 0.3)

    @staticmethod
    def dynamic_timeout(estimated_tokens: int) -> int:
        """根据 prompt token 数动态计算超时时间（秒）。

        基于 DeepSeek API 实际延迟测量（Claude 通过 Anthropic-compatible 协议调用）：
        - efforLevel=xhigh 下，基础往返 ~8s，中等分析 ~73s，标准分析 ~354s
        - 留 50% 余量应对 API 队列延迟波动
        - < 5000 tokens（小文档）：180 秒（实测最小 ~126s）
        - 5000-30000 tokens（中等文档）：600 秒（实测最小 ~354s）
        - >= 30000 tokens（大文档）：900 秒
        """
        if estimated_tokens < 5000:
            return 180
        elif estimated_tokens < 30000:
            return 600
        else:
            return 900

    # ---- 公共调用接口 ----

    async def claude(
        self,
        prompt: str,
        workdir: str | None = None,
        extra_args: list[str] | None = None,
        timeout: int | None = None,
    ) -> CLICallResult:
        """调用 Claude Code CLI 执行分析。

        Args:
            prompt: 传给 Claude 的完整提示词（含 system_prompt + user_prompt）
            workdir: 工作目录，默认使用实例级别 workdir
            extra_args: 传递给 `claude` 命令的额外参数列表
            timeout: 超时秒数，默认使用实例级别 timeout

        Returns:
            CLICallResult（success=True 且 raw_output 包含 Claude 的文本回复）
        """
        cmd = ["claude", "-p", prompt]
        if extra_args:
            cmd.extend(extra_args)

        return await self._run_command(
            cmd=cmd,
            workdir=workdir or self.workdir,
            timeout=timeout if timeout is not None else self.timeout,
            agent_name="claude",
        )

    async def codex(
        self,
        prompt: str,
        workdir: str | None = None,
        extra_args: list[str] | None = None,
        timeout: int | None = None,
    ) -> CLICallResult:
        """调用 Codex CLI 执行审查。

        Codex 通过 stdin 管道接收 prompt，而非命令行参数。
        使用 `echo "prompt" | codex exec --skip-git-repo-check` 模式。

        Args:
            prompt: 传给 Codex 的完整提示词
            workdir: 工作目录，默认使用实例级别 workdir
            extra_args: 传递给 codex exec 的额外参数列表
            timeout: 超时秒数，默认使用实例级别 timeout

        Returns:
            CLICallResult
        """
        cmd = ["codex", "exec", "--skip-git-repo-check"]
        if extra_args:
            cmd.extend(extra_args)

        return await self._run_command(
            cmd=cmd,
            workdir=workdir or self.workdir,
            timeout=timeout if timeout is not None else self.timeout,
            agent_name="codex",
            stdin_input=prompt,
        )

    # ---- JSON 提取 ----

    @staticmethod
    def extract_json(raw_output: str) -> JSONExtractResult:
        """从 LLM 文本输出中提取 JSON。

        按优先级尝试多种提取策略：
        1. 直接解析整个文本（含自动 JSON 修复）
        2. 提取 ```json ... ``` 代码块
        3. 提取 ``` ... ``` 代码块
        4. 匹配最外层 { ... } 或 [ ... ]

        Args:
            raw_output: LLM 的原始文本输出

        Returns:
            JSONExtractResult（success=True 且 data 为解析后的 dict/list）
        """
        if not raw_output or not raw_output.strip():
            return JSONExtractResult(
                success=False,
                error="输出内容为空",
                extract_method="none",
            )

        text = raw_output.strip()

        # 预处理：修复 Codex 输出中常见的 JSON 格式问题
        text = AgentCLI._repair_json_text(text)

        # 策略 1：直接解析整个文本
        result = AgentCLI._try_parse_json(text)
        if result.success:
            result.extract_method = "direct_parse"
            return result

        # 策略 2：提取 ```json ... ``` 代码块
        json_block_match = re.search(
            r"```\s*json\s*\n(.*?)\n```", text, re.DOTALL
        )
        if json_block_match:
            result = AgentCLI._try_parse_json(json_block_match.group(1).strip())
            if result.success:
                result.extract_method = "json_fence"
                return result

        # 策略 3：提取 ``` ... ``` 代码块（无语言标记）
        generic_block_match = re.search(
            r"```\s*\n(.*?)\n```", text, re.DOTALL
        )
        if generic_block_match:
            result = AgentCLI._try_parse_json(generic_block_match.group(1).strip())
            if result.success:
                result.extract_method = "generic_fence"
                return result

        # 策略 4：匹配最外层 JSON 对象 { ... }
        obj_match = AgentCLI._find_outermost_brace(text, "{", "}")
        if obj_match:
            result = AgentCLI._try_parse_json(obj_match)
            if result.success:
                result.extract_method = "brace_object_match"
                return result

        # 策略 5：匹配最外层 JSON 数组 [ ... ]
        arr_match = AgentCLI._find_outermost_brace(text, "[", "]")
        if arr_match:
            result = AgentCLI._try_parse_json(arr_match)
            if result.success:
                result.extract_method = "brace_array_match"
                return result

        return JSONExtractResult(
            success=False,
            error="无法从输出中提取有效 JSON，已尝试 5 种策略",
            extract_method="all_failed",
        )

    # ---- 内部方法 ----

    async def _run_command(
        self,
        cmd: list[str],
        workdir: str | None,
        timeout: int,
        agent_name: str,
        stdin_input: str | None = None,
    ) -> CLICallResult:
        """执行 CLI 命令的底层方法，含超时和异常处理。

        Args:
            cmd: 命令列表
            workdir: 工作目录
            timeout: 超时秒数
            agent_name: Agent 名称（用于日志）
            stdin_input: 可选，通过 stdin 管道的输入文本（用于 Codex）
        """
        cwd = workdir or str(Path.cwd())
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
            latency = int(
                (asyncio.get_event_loop().time() - _start_time()) * 1000
            )

            logger.info(
                f"{agent_name}_cli_done",
                exit_code=proc.returncode,
                stdout_len=len(stdout),
                stderr_len=len(stderr),
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
                    meta={"stderr": stderr, "stdout": stdout},
                )

            return CLICallResult(
                success=True,
                raw_output=stdout,
                exit_code=0,
                meta={"stderr": stderr},
            )

        except asyncio.TimeoutError:
            logger.error(f"{agent_name}_cli_timeout", timeout=timeout)
            return CLICallResult(
                success=False,
                error=f"命令执行超时（{timeout}秒）",
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
                error=f"调用异常: {str(exc)}",
            )

    @staticmethod
    def _try_parse_json(text: str) -> JSONExtractResult:
        """安全地尝试解析 JSON 字符串。"""
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

    @staticmethod
    def _repair_json_text(text: str) -> str:
        """修复 LLM 输出 JSON 中字符串值内的未转义 ASCII 双引号。

        Codex 在中文引号中输出 ASCII "（U+0022），会破坏 JSON 解析。
        通过词法分析识别 JSON 字符串值边界，只修复值内部的 "。
        """
        import re

        has_cjk = any('一' <= c <= '鿿' for c in text)
        if not has_cjk:
            return text

        # JSON 结构字符（在这些字符前的 " 是结构定界符，不修复）
        STRUCTURAL_FOLLOW_CHARS = set(',:}]')

        result = []
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
                    # 找到字符串值内部的 "，检查后面是不是 JSON 结构字符
                    j = i + 1
                    while j < n and text[j] in ' \t\n\r':
                        j += 1
                    if j < n and text[j] in STRUCTURAL_FOLLOW_CHARS:
                        # JSON 结构定界符，字符串正常结束
                        result.append(ch)
                        in_string = False
                    else:
                        # 内容中的引号，替换为右弯引号 "
                        result.append('”')
                else:
                    result.append(ch)
            else:
                if ch == '"':
                    in_string = True
                result.append(ch)

            i += 1

        return ''.join(result)

    @staticmethod
    def _find_outermost_brace(text: str, open_c: str, close_c: str) -> str | None:
        """找到最外层匹配的括号内容。

        从文本中定位第一个 open_c，然后通过计数器找到对应的 close_c，
        确保提取完整的 JSON 结构。
        """
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
                    return text[start : i + 1]

        return None


def _start_time() -> float:
    """获取事件循环当前时间（秒），用于延迟计算。"""
    try:
        return asyncio.get_event_loop().time()
    except RuntimeError:
        import time
        return time.monotonic()
