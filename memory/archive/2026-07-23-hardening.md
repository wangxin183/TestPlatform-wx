---
name: 2026-07-23-hardening-retro
description: 2026-07-22~23 TCG/编译/硬编码治理踩坑与经验
metadata:
  node_type: memory
  type: feedback
  date: 2026-07-23
---

# 回顾：TCG 可执行性 + 硬编码治理（2026-07-22 ~ 07-23）

> 与 [项目北极星](../../memory-feedback-project-north-star.md) 对齐：本轮删除 UI 文案白名单、诊断改 Agent、词表进 yaml，是戒律落地，不是又一次「为当前 case 打补丁」。

> **归档说明**：永久约定已吸收进 `memory-feedback-testcase-generation.md` / `memory-feedback-execution-runtime.md` / 北极星；本文仅保留日记与踩坑表，非常读。

## 今天做了什么（成果）

1. **TCG 入口前缀误剥**（`_strip_module_navigation_prefix`）  
   - 中途 `member_page → reader_main` 被当成「进模块」前缀整段删掉 → 空步骤 → 覆盖失败（TCG-0013 / TP-010）。  
   - 修复：步 1 已在模块主状态则不剥；只剥到第一次「外→主」落地；剥光则保留原文。复跑 TCG-0015 通过。

2. **保存/重编译「假死」**  
   - 点击有效，状态仍「不可执行」无反馈。  
   - 根因：`score_assertion_quality` 对空 post 的 tap/wait 取 min → 整案被 `ASSERTION_QUALITY_LOW` 拖死。  
   - 修复：动作步空 post 跳过评分；前端 toast + 重开弹窗。

3. **编译诊断硬编码 → Agent**  
   - 去掉错误码→suggestion 死表；新增 `testcase.compile_advisor` + Skill + `testcase_compile_advisor.py`。  
   - Agent 失败才用通用两句 fallback，禁止再长成错误码表。

4. **硬编码审计与收敛（产品拍板）**  
   - **不做** `known_ui_texts.yaml`；编译/自愈**不再**靠 UI 文案白名单猜无引号 expected。  
   - 删除 `_KNOWN_UI_TEXTS` / `known_ui_text_signals` / heal 确定性补「」。  
   - 策略词表合并进 `config/automation_lexicon.yaml`（compiler + lint 共用）。  
   - Slim 去掉产品例句；示例放 `.agents/skills/ui-testcase-from-testpoint/examples.md`。  
   - RA 批大小/token → `settings.yaml` → `requirement_analysis`。

5. **顺带修** `narrate(event, **payload)` 与 payload 含 `event` 时 TypeError（module_session / execution_runtime_service）。

---

## 踩过的坑

| 坑 | 表现 | 根因 |
|----|------|------|
| 前缀剥离过宽 | 覆盖缺 TP、用例被丢 | 把「回模块主状态」当成「入口前缀」 |
| 评分取全局 min | 保存后仍不可执行、像按钮坏了 | 空 post 的动作步质量=none |
| 诊断写死在代码 | 文案僵、难维护 | 把 Agent 该说的话做成错误码 map |
| UI 文案白名单 | 短期方便、长期债 | 产品事实写进规则引擎；动态文案必然失效 |
| 同名 kwargs | 单测偶发 TypeError | `narrate(name, **ev)` 且 `ev` 含 `event` |
| 无同 ID 重跑 | TCG-0013 只能新建任务 | 生成任务 API 无 retry-same-id |
| zsh 轮询 | 脚本怪错 | 环境变量名 `GID` 与 zsh 保留/特殊冲突 |
| 成功态误判 | 以为没跑完 | TCG 成功常是 `pending_review`，不是 `completed` |

---

## 经验（可复用）

1. **先分清「产品事实 / 工程策略 / Agent 话术」**  
   - 产品文案 → 用例「」+ Skill 示例，不进编译白名单。  
   - 主观/模糊词 → 一份 yaml 词表。  
   - 诊断建议 → Agent；fallback 只允许通用句。

2. **兜底规则要可证明边界**  
   - 前缀剥离、质量评分、无引号推断：每加一条规则先写「什么情况不该触发」。

3. **UI「没反应」先查状态机与提示，再查点击**  
   - 请求成功但展示不变 = 反馈缺失或规则把结果打回失败。

4. **调试期捷径不要配置化「升级」**  
   - 白名单该删就删，不要先抽 yaml 再养大；用户已确认 B 方案。

5. **验收对齐真实状态枚举**  
   - 轮询/脚本用 `pending_review` 等产品态，勿想当然 `completed`。

---

## 好的地方

- 硬编码治理先方案后确认（yaml vs 删除），避免无效抽取。  
- 编译诊断主路径改 Agent，符合「模型决策、工程围栏」。  
- lexicon / RA 配置与 TCG `testcase_generation` 配置风格对齐。  
- 单测跟着语义改（无引号不再期望 `text_visible:追更`），避免假绿。

---

## 需要改进

| 项 | 说明 |
|----|------|
| TCG 同任务重跑 | 缺 `retry` / 复用同一 generation_id 的 API |
| 旧库用例 | 无「」的 expected 会变弱/失败，需评审补引号或 Agent heal |
| lexicon 热更新 | 当前 `lru_cache`，改 yaml 需重启进程 |
| narrate 调用约定 | 统一禁止 `**dict` 原样传入含 `event` 的对象；可再收紧 API |
| RA 固定批大小 | 已进配置；中长期可学 TCG 做 token 组批 |
| 前端反馈 | 编译失败原因卡片要持续可见；避免「静默不可执行」 |
| 文档同步 | `docs/requirement_analysis_module.md` 仍可能写死 FR_TP=4 常量名，宜改指 settings |

---

## 永久约定（摘要）

- expected **必须**「」包裹关键文案；编译**只认引号**，不维护产品 UI 白名单。  
- 策略词表：`config/automation_lexicon.yaml`。  
- 例句：Skill 目录 `examples.md`，不塞进 `testcase_coverage` 产品文案。  
- 编译诊断：`testcase.compile_advisor`；禁止错误码硬编码 suggestion 表回归。
