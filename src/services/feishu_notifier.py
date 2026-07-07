"""飞书通知服务 — 通过 Webhook 发送消息卡片。

支持三种消息卡片：
1. 审查完成通知（含分析摘要 + 跳转平台按钮）
2. 任务失败通知（含失败原因 + 重试/补充意见重试/结束三个按钮）
3. 人工审核结果通知

Webhook URL 从配置或环境变量读取。

Usage:
    from src.services.feishu_notifier import FeishuNotifier

    notifier = FeishuNotifier()
    await notifier.notify_review_complete(
        analysis_id="RA-0001",
        score=85,
        fr_count=12,
        issues=["登录超时场景未覆盖", "弱网环境未覆盖"],
        platform_url="http://localhost:8999/requirement-analysis?id=RA-0001",
    )
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ============================================================
# 配置常量
# ============================================================

FEISHU_WEBHOOK_URL = os.environ.get(
    "FEISHU_WEBHOOK_URL",
    "https://www.feishu.cn/flow/api/trigger-webhook/b31175fa62dee32c5b2da9215e83410e",
)

CARD_TEMPLATE_BLUE = "blue"
CARD_TEMPLATE_RED = "red"
CARD_TEMPLATE_GREEN = "green"

# 默认 API 基础 URL（用于生成卡片中的跳转链接）
DEFAULT_PLATFORM_BASE_URL = os.environ.get(
    "PLATFORM_BASE_URL", "http://localhost:8999"
)


@dataclass
class NotifyResult:
    """通知发送结果"""
    success: bool
    message: str = ""


# ============================================================
# FeishuNotifier
# ============================================================

class FeishuNotifier:
    """通过飞书 Webhook 发送消息卡片通知。"""

    def __init__(
        self,
        webhook_url: str | None = None,
        platform_base_url: str | None = None,
    ):
        self.webhook_url = webhook_url or FEISHU_WEBHOOK_URL
        self.platform_base_url = platform_base_url or DEFAULT_PLATFORM_BASE_URL

    # ============================================================
    # 公共接口
    # ============================================================

    async def notify_review_complete(
        self,
        analysis_id: str,
        score: int,
        fr_count: int = 0,
        nfr_count: int = 0,
        tp_count: int = 0,
        issues: list[str] | None = None,
        filename: str = "",
    ) -> NotifyResult:
        """审查完成通知 — 蓝色卡片，含分析摘要和跳转按钮。"""
        card = self._build_review_card(
            analysis_id=analysis_id,
            score=score,
            fr_count=fr_count,
            nfr_count=nfr_count,
            tp_count=tp_count,
            issues=issues or [],
            filename=filename,
            template=CARD_TEMPLATE_BLUE,
        )
        return await self._send_card(card, notify_type="review_complete")

    async def notify_failed(
        self,
        analysis_id: str,
        stage_name: str,
        error_summary: str,
    ) -> NotifyResult:
        """任务失败通知 — 红色卡片，含重试/补充意见重试/结束三个按钮。"""
        card = self._build_failure_card(
            analysis_id=analysis_id,
            stage_name=stage_name,
            error_summary=error_summary,
        )
        return await self._send_card(card, notify_type="task_failed")

    async def notify_review_result(
        self,
        analysis_id: str,
        decision: str,
        comment: str = "",
    ) -> NotifyResult:
        """人工审核结果通知 — 绿色（通过）或蓝色（驳回）卡片。"""
        if decision == "approved":
            template = CARD_TEMPLATE_GREEN
            title = "✅ 需求分析已通过"
        else:
            template = CARD_TEMPLATE_BLUE
            title = "🔄 需求分析已驳回"

        card = self._build_result_card(
            analysis_id=analysis_id,
            title=title,
            decision=decision,
            comment=comment,
            template=template,
        )
        return await self._send_card(card, notify_type=f"review_{decision}")

    async def notify_text(self, text: str) -> NotifyResult:
        """发送简单文本消息（用于快速测试）。"""
        payload = {
            "msg_type": "text",
            "content": {"text": text},
        }
        return await self._send_raw(payload, notify_type="text")

    # ============================================================
    # 卡片构建
    # ============================================================

    def _build_review_card(
        self,
        analysis_id: str,
        score: int,
        fr_count: int,
        nfr_count: int,
        tp_count: int,
        issues: list[str],
        filename: str,
        template: str,
    ) -> dict:
        """构建审查完成消息卡片。"""
        # 评分等级
        if score >= 90:
            level = "优秀"
        elif score >= 80:
            level = "良好"
        elif score >= 70:
            level = "一般"
        else:
            level = "需改进"

        # 核心指标行
        metrics_text = (
            f"**评分**：{score}/100（{level}）\\n"
            f"**FR** {fr_count} 条  |  **NFR** {nfr_count} 条  |  **测试点** {tp_count} 个"
        )

        # 问题摘要（最多展示 3 条）
        issues_text = ""
        if issues:
            top_issues = issues[:3]
            issues_text = "\\n".join(f"• {issue}" for issue in top_issues)
            if len(issues) > 3:
                issues_text += f"\\n• ...共 {len(issues)} 条建议"

        url = f"{self.platform_base_url}/requirement-analysis?id={analysis_id}"

        elements: list[dict] = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**文档**：{filename or analysis_id}\\n{metrics_text}",
                },
            },
        ]

        if issues_text:
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**⚠️ 需关注**：\\n{issues_text}",
                },
            })

        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "请前往测试平台查看详情并完成人工审核",
            },
        })
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "去平台审核"},
                    "type": "primary",
                    "url": url,
                }
            ],
        })

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"📋 需求分析审查完成",
                    },
                    "template": template,
                },
                "elements": elements,
            },
        }

    def _build_failure_card(
        self,
        analysis_id: str,
        stage_name: str,
        error_summary: str,
    ) -> dict:
        """构建任务失败消息卡片。"""
        # 按钮的 value 编码 JSON payload，由平台回调处理
        retry_value = json.dumps({"action": "retry", "id": analysis_id})
        feedback_retry_value = json.dumps(
            {"action": "retry_with_feedback", "id": analysis_id}
        )
        abort_value = json.dumps({"action": "abort", "id": analysis_id})

        url = f"{self.platform_base_url}/requirement-analysis?id={analysis_id}"

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": "❌ 需求分析失败",
                    },
                    "template": CARD_TEMPLATE_RED,
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": (
                                f"**失败阶段**：{stage_name}\\n"
                                f"**分析 ID**：{analysis_id}\\n"
                                f"**失败原因**：{error_summary}"
                            ),
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": "请选择下一步操作：",
                        },
                    },
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "🔄 重试"},
                                "type": "primary",
                                "value": retry_value,
                            },
                            {
                                "tag": "button",
                                "text": {
                                    "tag": "plain_text",
                                    "content": "📝 补充意见后重试",
                                },
                                "type": "default",
                                "value": feedback_retry_value,
                            },
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "⏹ 结束任务"},
                                "type": "danger",
                                "value": abort_value,
                            },
                        ],
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"[在测试平台中查看]({url})",
                        },
                    },
                ],
            },
        }

    def _build_result_card(
        self,
        analysis_id: str,
        title: str,
        decision: str,
        comment: str,
        template: str,
    ) -> dict:
        """构建人工审核结果通知卡片。"""
        decision_text = "审核通过 ✅" if decision == "approved" else "已驳回，待重新分析 🔄"
        comment_text = f"\\n**审核意见**：{comment}" if comment else ""
        url = f"{self.platform_base_url}/requirement-analysis?id={analysis_id}"

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": template,
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": (
                                f"**分析 ID**：{analysis_id}\\n"
                                f"**状态**：{decision_text}"
                                f"{comment_text}"
                            ),
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "在平台中查看"},
                                "type": "primary",
                                "url": url,
                            }
                        ],
                    },
                ],
            },
        }

    # ============================================================
    # 底层发送
    # ============================================================

    async def _send_card(
        self,
        card: dict,
        notify_type: str = "",
    ) -> NotifyResult:
        """发送消息卡片，含错误处理和日志。"""
        logger.info("feishu_send_start", notify_type=notify_type)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    self.webhook_url,
                    json=card,
                    headers={"Content-Type": "application/json"},
                )
                data = resp.json()

                code = data.get("code", -1)
                msg = data.get("msg", "")

                if code == 0:
                    logger.info("feishu_send_success", notify_type=notify_type)
                    return NotifyResult(success=True, message=msg)

                logger.error(
                    "feishu_send_failed",
                    notify_type=notify_type,
                    code=code,
                    msg=msg,
                )
                return NotifyResult(
                    success=False,
                    message=f"飞书返回错误 (code={code}): {msg}",
                )

        except httpx.TimeoutException:
            logger.error("feishu_send_timeout", notify_type=notify_type)
            return NotifyResult(success=False, message="飞书请求超时")
        except Exception as exc:
            logger.error("feishu_send_error", notify_type=notify_type, error=str(exc))
            return NotifyResult(success=False, message=str(exc))

    async def _send_raw(
        self,
        payload: dict,
        notify_type: str = "",
    ) -> NotifyResult:
        """直接发送原始 JSON payload（用于简单文本消息）。"""
        logger.info("feishu_send_raw_start", notify_type=notify_type)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                data = resp.json()
                code = data.get("code", -1)

                if code == 0:
                    logger.info("feishu_send_raw_success", notify_type=notify_type)
                    return NotifyResult(success=True)
                return NotifyResult(success=False, message=str(data))

        except Exception as exc:
            logger.error("feishu_send_raw_error", notify_type=notify_type, error=str(exc))
            return NotifyResult(success=False, message=str(exc))
