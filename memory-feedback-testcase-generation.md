---
name: testcase-generation-module
description: 独立用例生成 TCG 模块产品决策与降本提速约定
metadata:
  node_type: memory
  type: feedback
---

## 独立用例生成（TCG）模块边界

与 Project / Pipeline 解耦。输入仅从需求分析 `RA-xxxx` 选中 `test_type=ui` 的测试点；结果写入 `TestCase`（`project_id=NULL`），详情页 + 用例库可见；逐条评审，无待审则任务 `completed`。

Why: 产品确认的独立工作流，避免绑死流水线 FSM。

How to apply:
- API：`/api/v1/testcase-generations*`
- Service：`src/services/testcase_generation_service.py`
- 覆盖：`src/services/testcase_coverage.py`
- Agent：必须 `agent_runtime.run(AgentTask(role="testcase.generator"))`，禁止自建 Agent
- Skill：`.agents/skills/ui-testcase-from-testpoint/SKILL.md`
- 落盘：`storage/testcase_generations/{TCG-id}/`

## 降本提速：token 组批 + 瘦 prompt + 有限并发

旧逻辑 `TP_BATCH_SIZE=4` 串行：193 TP → 49 次调用，每批重复整篇 Skill，成本过高。

已落地：
1. `pack_tp_batches_by_tokens`（上限 12 TP / 目标约 7k input tokens）
2. Prompt 只注入精简硬规则；完整 SKILL 仅 `SKILL_used.md` 审计
3. TP/FR JSON 压缩字段 + 本批 FR 去重
4. `asyncio.Semaphore(max_concurrency=3)`，日志加 `log_lock`
5. 覆盖自愈：缺失 TP 再组批补齐，禁止逐条重调

配置：`config/settings.yaml` → `testcase_generation`

验收：`batch_count` ≤ 20（相对 49）；`agent_start` 次数对齐；时间戳可交错；覆盖校验仍过。

明确不做：193 一把梭；绕过 agent_runtime；跨任务缓存。

## 进行中任务不热切换

已跑中的旧 TCG（如 TCG-0002 串行）不会自动用新组批；新创建任务才走新逻辑。

## 可执行性与硬编码收敛（永久约定）

- **入口前缀剥离**：不得把中途「外页→模块主状态」当成入口前缀删掉；剥光则保留原文（TCG-0013 / TP-010）。
- **断言质量评分**：空 post 的 tap/wait/swipe/input/scroll **不参与** min 质量，避免误杀整案。
- **编译诊断**：走 `testcase.compile_advisor` Agent，禁止错误码→suggestion 死表。
- **无 UI 文案白名单**：expected 必须「」；例句在 Skill `examples.md`；Slim 不写死产品文案。
- **策略词表**：`config/automation_lexicon.yaml`（与 lint/compiler 共用）。
- **成功态**：任务生成成功多为 `pending_review`，不是 `completed`；无同 ID 重跑 API，失败需新建 TCG。

日记式踩坑全文见归档：[memory/archive/2026-07-23-hardening.md](memory/archive/2026-07-23-hardening.md)。
