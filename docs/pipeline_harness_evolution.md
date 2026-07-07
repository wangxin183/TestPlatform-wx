# 流水线架构演进方案：引入 Harness 设计模式

## 一、当前架构

### 1.1 当前使用的设计模式

| 模式 | 位置 | 说明 |
|------|------|------|
| Pipeline Orchestrator | `src/pipeline/orchestrator.py` | 中央调度器，while 循环串行推进阶段 |
| Stage-Context | `src/pipeline/context.py` | 共享数据总线，阶段间通过 context 传递数据 |
| FSM 状态机 | `src/pipeline/state_machine.py` | `transitions` 库实现 13 个状态 + 暂停/取消/回环 |
| Template Method | `src/pipeline/stages/base.py` | `AbstractStage.run()` 包装 execute()，统一日志和异常 |
| Agent + Skill | `src/llm/agents/base.py` | Agent 封装编排 + 重试，Skill 定义 LLM 行为 |

### 1.2 当前存在的问题

1. **阶段内部步骤无抽象**：解析阶段的 Map/Collect/Reduce 都是私有方法调用，没有统一的 Step 概念
2. **不能并行执行**：多文档 chunk LLM 调用是串行 for 循环，天然适合并行的 Map 阶段被浪费
3. **失败策略单一**：任何一个 chunk LLM 调用失败 → 整个 pipeline 失败，缺少 skip/retry 配置
4. **阶段配置硬编码**：`STAGE_MAP` 和阶段顺序在代码中写死，调整需改 Python 源码
5. **可观测性粗**：日志在阶段级别，无法看每个 Step 的独立耗时和状态

---

## 二、目标架构：Harness 三层抽象

```
Pipeline                流程门面
├─ Stage                阶段（对应当前概念）
│  ├─ StepGroup         步骤组（并行/串行执行策略）
│  │  ├─ Step           原子步骤（可重试、可跳过）
│  │  └─ Step
│  └─ StepGroup
└─ Stage
```

### 2.1 Harness 抽象对照

```
Harness              →  当前项目                  →  演进后
───────────────────────────────────────────────────────────
Pipeline             →  PipelineContext + FSM     →  不变
├─ Stage             →  AbstractStage             →  Stage（保留，加失败策略）
│  ├─ StepGroup      →  （无，阶段内串行）          →  StepGroup（并行执行）
│  │  ├─ Step        →  （无，LLM 调用裸写在阶段里） →  Step（原子操作）
│  │  └─ Step
│  └─ StepGroup
└─ Stage
```

### 2.2 核心抽象定义

```python
class Step:
    """原子步骤 — 最小的执行单元"""
    name: str
    execute: Callable[[Context], Awaitable[StepResult]]
    on_failure: "abort" | "skip" | "retry" = "abort"
    max_retries: int = 0          # 步骤级别重试
    timeout_seconds: int = 300    # 单个步骤超时

class StepGroup:
    """步骤组 — 控制执行策略"""
    strategy: "serial" | "parallel" = "serial"
    steps: list[Step | StepGroup]  # 支持嵌套
    on_failure: "abort_all" | "continue_others" = "abort_all"

class StepResult:
    """步骤执行结果"""
    status: "success" | "failed" | "skipped" | "timeout"
    data: dict
    error: str | None
    duration_ms: int
    retry_count: int
```

---

## 三、三步演进路径

### 第 1 步：拆出 Step 抽象（不改执行方式）

**目标**：把阶段内部的私有方法调用显式化为 Step 序列。

**以 ParsingStage 为例**：

改造前（当前代码）：
```python
class ParsingStage(AbstractStage):
    async def execute(self, stage_input):
        all_chunk_results, chunk_files = await self._map_phase(context)
        all_reqs, all_non_func = self._collect_requirements(all_chunk_results)
        if total_chunks > 1:
            merged = await self._deduplicate_requirements(all_reqs)
        # ... 每步都是私有方法调用，失败直接抛异常
```

改造后：
```python
class ParsingStage(AbstractStage):
    async def execute(self, stage_input):
        steps = [
            Step(name="map_chunks", execute=self._map_phase,
                 on_failure="abort"),
            Step(name="collect_requirements", execute=self._collect)
                 on_failure="skip",  # 收集失败可降级
            Step(name="deduplicate", execute=self._deduplicate,
                 on_failure="skip",  # 去重失败用未去重数据
                 max_retries=1),
        ]
        return await self._run_steps(steps, stage_input)
```

**收益**：
- 每个 Step 独立日志/耗时/状态
- 失败策略可配置（当前 `_deduplicate` 失败已降级，但逻辑在 except 里——Step 的 `on_failure="skip"` 更显式）
- 前端可展示更细粒度的进度

### 第 2 步：引入 StepGroup 并行

**目标**：多文档/多 chunk 的 Map 操作并行执行。

**以 Map Phase 为例**：
```python
# 生成并行 StepGroup
chunk_steps = []
for doc_id, md_text in context.raw_texts.items():
    chunks = self._split_markdown_with_trace(md_text)
    for ci, chunk in enumerate(chunks):
        chunk_steps.append(Step(
            name=f"parse_{doc_id[:8]}_chunk{ci}",
            execute=lambda: self._llm_extract_chunk(chunk, ci),
            on_failure="skip",       # 单个 chunk 失败不阻塞
            max_retries=2,
            timeout_seconds=120,
        ))

map_group = StepGroup(
    strategy="parallel",
    steps=chunk_steps,
    on_failure="continue_others",
    max_concurrency=5,              # 控制并发数
)

reduce_steps = [
    Step(name="collect", ...),
    Step(name="deduplicate", ...),
]
```

**收益**：
- 多文档解析从串行变并行，耗时降为 `max(单文档耗时)` 而非 `sum(所有文档耗时)`
- 并发数可控，不压垮 LLM API

### 第 3 步：配置驱动

**目标**：阶段定义从硬编码 Python 改为 YAML 配置。

```yaml
# pipeline_config.yaml
stages:
  - name: ingestion
    class: IngestionStage
    steps:
      - name: load_documents
        on_failure: abort

  - name: parsing
    class: ParsingStage
    groups:
      - name: map_phase
        strategy: parallel
        max_concurrency: 5
        step_template:
          on_failure: skip
          max_retries: 2
          timeout_seconds: 120
      - name: reduce_phase
        strategy: serial
        steps:
          - name: collect
          - name: deduplicate
            on_failure: skip              # 降级使用未去重数据
            max_retries: 1

  - name: analysis
    class: AnalysisStage
    agent: RequirementAgent               # 引用 Agent

  - name: generation
    class: GenerationStage

  # ... review, execution, reporting, regression
```

**收益**：
- 非开发人员可调整阶段顺序、失败策略
- 新增阶段只需配置 + 实现 Agent/Stage
- 不同项目可用不同的 pipeline 配置

---

## 四、不引入的部分

| Harness 特性 | 引入？ | 原因 |
|-------------|--------|------|
| 分布式 Delegate | 否 | 单进程 async 足够，不引入 Redis/消息队列 |
| 模板市场/Step Registry | 否 | 项目规模不需要 |
| 审批流/Gate | 否 | 已有 review 阶段实现（暂停等待人工确认） |
| 图形化 Builder | 否 | YAML 配置即可 |
| 定时触发 | 否 | 按需用 CronCreate 即可 |

---

## 五、实施建议

1. **每次只做一步**，不改动当前可工作的流程
2. **Step 层先做日志可观测**，不改变执行行为，确认稳定后再改失败策略
3. **并行化选 Map Phase 试点**，成功后推广到其他适合并行的 StepGroup
4. **配置化放在最后**，等 Step/StepGroup 模式稳定后再做

### 预估工作量

| 步骤 | 改动量 | 风险 |
|------|--------|------|
| 第 1 步：Step 抽象 | ~200 行（基类 + 集成） | 低 — 不改执行行为 |
| 第 2 步：StepGroup 并行 | ~150 行（asyncio.gather + 并发控制） | 中 — LLM API 限流需关注 |
| 第 3 步：配置驱动 | ~300 行（YAML 解析 + 动态加载） | 低 — Python 加载 YAML 成熟 |

---

## 六、相关文件

| 文件 | 角色 |
|------|------|
| `src/pipeline/stages/base.py` | Stage/Step/StepGroup 基类 |
| `src/pipeline/orchestrator.py` | 流水线调度器 |
| `src/pipeline/state_machine.py` | FSM 状态机 |
| `src/pipeline/context.py` | 共享数据总线 |
| `src/llm/agents/base.py` | Agent + Skill 基类 |
| `pipeline_config.yaml` | （新增）流水线配置文件 |
