# Agent Harness 工程模式：跨阶段自主决策与自愈

> 日期：2026-07-22  
> 状态：**M1+M2 已落地**（RA 证据策略与平台展示增强见 M3）  
> 目标：RA / TCG / EXE 共用 **StageAgentHarness**，在确定性与 Agent 自愈间平衡，向无人值守演进。

**北极星（勿忘）：** 本项目要用 Agent 能力平衡确定性与自主决策；人不当日常 diagnoser。  
后续设计禁止短视硬编码（不为当前一个失败 case 堆特例/文案白名单/错误码死表）；从大局观维护。  
展开：`memory-feedback-project-north-star.md`（仓库根目录）。

---

## 0. 已确认决策

| # | 决策 | 结论 |
|---|------|------|
| 1 | HealLoop 形态 | **新建** `src/services/heal_loop.py`；`self_healing.py` 变薄包装（RA 行为兼容） |
| 2 | TCG 可执行性自愈 | **定点修** expected/合同为主，整案 regen 兜底（M2） |
| 3 | EXE 预算 | **三档**：setup / step / case（见配置） |
| 4 | 落地顺序 | **先 M1**：Harness 内核 + EXE；RA/TCG 先接 API，策略暂保持 |

**显式采用 Harness 设计模式**：模型只决策；围栏、循环、预算、验收、账本由工程层提供。

---

## 1. 问题与目标

### 1.1 痛点

Agent 已接入，但仍常变成「人调试 → 改代码 → 再跑」。人仍是 diagnoser，与无人值守目标不符。

### 1.2 产品目标

1. **确定性高速路**：DSL、Setup、锚点、缓存、组批、规则编译 —— 便宜可回放。  
2. **Harness 内 Agent 自愈**：阻塞时在围栏内诊断换招，直到**阶段目标**达成或记缺陷。  
3. **经验沉淀**：成功路径/策略可回写，降低重复决策。  
4. **人不修抖动**：人只处理真缺陷与耗尽后的升级。

---

## 2. StageAgentHarness（统一工程模式）

### 2.1 概念

```
Brain (LLM role)     ← 只输出决策 / 产物
        ↑
Harness              ← 本方案一等公民
  · goal + budget
  · attempt 循环
  · tools / validators 围栏
  · diagnoser → HealPlan
  · ledger
        ↑
Deterministic core   ← Setup / DSL / 解析 / 组批
```

| Harness 组件 | 职责 | 本仓库落点 |
|--------------|------|------------|
| Runtime | 按 role 调脑 | `src/agent_runtime/` |
| HealLoop | 失败分类 → plan → 限次重试 | `src/services/heal_loop.py` |
| Tools | 可执行动作围栏 | EXE: `ToolGateway`；RA/TCG: 重跑 role / 修输出 |
| Validator | 目标是否达成 | 证据/覆盖/lint/compile/postconditions |
| Ledger | 可观测自愈 | `heal_ledger.jsonl` |
| Skill | 决策说明书（非状态机） | `.agents/skills/execution-*` 等 |

**硬规则：**

1. 业务只认 `role`，禁止散落 CLI 品牌调用。  
2. Agent 不得绕过围栏（EXE 必须 `ToolGateway`）。  
3. 验收权在 Validator/合同，不在口头声明。  
4. Skill 管「怎么想」，Harness 管「能不能做 / 几次 / 算不算过」。  
5. 执行热路径不挂 Cursor MCP。

### 2.2 五层映射

```
L5 编排 (RA/TCG/EXE)     = Harness 调用方
L4 HealLoop              = Harness 控制环
L3 agent_runtime roles   = Brain
L2 Tools/Validators      = 围栏
L1 确定性内核            = 快速路径
```

### 2.3 HealLoop 状态机

```
attempt(goal) → pass → DONE
             → fail → classify
                  → INFRA → backoff/fallback → retry
                  → BLOCKED/QUALITY → diagnoser → plan
                       → give_up → DONE(defect|needs_human)
                       → else apply(plan) → retry
                  → PRODUCT_BUG → DONE(defect)
             → budget exhausted → DONE(exhausted)
```

### 2.4 Role / Skill

| Role | Skill | 阶段 |
|------|-------|------|
| requirement.* / testcase.generator | 现有 | RA/TCG |
| execution.compiler | execution-compiler | EXE |
| execution.navigator | **execution-navigator** | EXE 战术选 tool |
| execution.diagnoser | **execution-healer** | EXE 战略换招 |
| utility.diagnoser | （RA 沿用） | 输出/质量修复 |

---

## 3. 分阶段 Goal Contract

| 阶段 | 目标（机器可判定） | Harness 自愈 | 禁止假绿 |
|------|-------------------|--------------|----------|
| RA | schema + 原文证据 + 不编造 | JSON/infra/质量（现有逻辑经包装） | 造 FR |
| TCG | 覆盖 + 可编译阈值 | 补批；M2 定点修 expected | 主观点强行 ready |
| EXE | postconditions / 业务 expected | Setup/导航/步骤 HealLoop | 缺陷涂绿；跨页误 back |

---

## 4. EXE 预算（三档）

```yaml
run:
  self_heal_enabled: true
  heal_budget:
    setup: 2
    step: 2    # navigator max_calls 之外的战役级次数
    case: 2
  max_heal_attempts: 2  # 兼容旧字段，等价于 case 档默认
```

---

## 5. 实施节奏

| 迭代 | 内容 | 状态 |
|------|------|------|
| **M1** | `heal_loop.py` + EXE 接入 diagnoser + navigator/healer Skill + ledger + 预算分档 + 单测；RA `self_healing` 薄说明/委托入口 | **已完成（内核+EXE）** |
| **M2** | TCG 定点可执行性自愈 + 自然语言日志/前端展示 | **已完成** |
| **M3** | RA 证据/范围策略 Skill 化 + 平台展示自愈 | 待定 |
| **M4** | 经验沉淀升格 | 待定 |

### M1 文件清单

- `src/services/heal_loop.py` — 内核  
- `execution_runtime/heal/` — EXE adapter（diagnoser + apply plan）  
- `.agents/skills/execution-navigator/SKILL.md`  
- `.agents/skills/execution-healer/SKILL.md`  
- `execution_runtime/config.py` + `settings.yaml` — heal_budget  
- `execution_runtime/pytest_exec/test_execution.py` / `case_runner.py` — 接入  
- `agent_tool_runner.py` — 加载 navigator Skill  
- `tests/unit/test_heal_loop.py`  
- 文档：本文件；`docs/execution_runtime_design.md` 指向本模式  

### 已删除的过时方案（不影响运行时）

- `docs/superpowers/plans/2026-07-17|18|21-*.md` 及对应 specs（已落地完成）  
- `docs/plans/auto-execution-module.md`、`docs/execution_plan.md`  
- `docs/langgraph_migration_plans.md`、`docs/pipeline_harness_evolution.md`（旧流水线/迁移草案）  

保留：`docs/testexe.md`、`docs/agent_runtime.md`、`docs/requirement_analysis_module.md`、`docs/execution_runtime_design.md`（改为指向 Harness）。

---

## 6. 成功标准（摘要）

- EXE 弹层/卡页/定位漂移多数由 Harness 消化，而非改代码  
- 不回归跨页误愈；禁止自愈涂绿产品缺陷  
- ledger 可审计每次 plan  

---

## 7. 一句话

> **StageAgentHarness**：确定性跑已知路径；HealLoop + 角色 Agent 在 Tool/Validator 围栏内自主换招直到阶段目标达成；RA/TCG/EXE 共用 harness，差异只在 adapter。

---

## 8. 后续硬化笔记（2026-07-23）

与本计划衔接的落地见仓库根目录：

- [memory/archive/2026-07-23-hardening.md](../../../memory/archive/2026-07-23-hardening.md)

要点：TCG 可执行性自愈去掉 UI 文案白名单；编译诊断改 `testcase.compile_advisor`；策略词表进 `automation_lexicon.yaml`。
