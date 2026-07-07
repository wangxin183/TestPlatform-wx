# LangGraph 迁移方案

> 基于 2026-05-19 项目状态，当前架构：transitions FSM + AbstractStage + PipelineContext + orchestrator。

## 概念映射

| 当前实现 | LangGraph 对应 |
|----------|---------------|
| `PipelineStateMachine`（transitions 库，14 状态） | `StateGraph` + `compile()` |
| `PipelineContext`（dataclass） | `TypedDict` State + Reducer |
| `AbstractStage.execute()` → `StageOutput` | node 函数 `(State) → Partial[State]` |
| `STAGE_MAP` 路由 | `add_node()` + edges |
| `fsm.advance()` / `reject()` | `add_edge()` / `add_conditional_edges()` |
| `_check_interrupted()` 轮询 | `interrupt_before` / `interrupt()` |
| review 人工 pause/continue | `interrupt()` + `Command(resume=value)` |
| `PipelineStageLog` DB 记录 | `SqliteSaver` checkpointer 或自定义 callback |
| `run_stage()` 独立执行 | subgraph 调用，传入部分 State |
| WebSocket 广播 | `graph.stream()` 事件 + 现有 WS |

---

## 方案一：薄包装模式（2-3 天）

**核心思路**：8 个 Stage 类一个不动，在它们外面包一层 LangGraph node。StateGraph 只做编排，不替换业务逻辑。

**代码量**：新增 ~150 行，现有代码零改动。

```python
# src/pipeline/orchestrator_langgraph.py

from typing import TypedDict, Annotated, Optional
from operator import add
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt

from src.pipeline.stages.base import StageInput
from src.pipeline.context import PipelineContext
from src.pipeline.orchestrator import STAGE_MAP

class PipelineState(TypedDict):
    pipeline_id: str
    project_id: str
    status: str
    current_stage: str
    context_json: dict          # PipelineContext.to_json()
    stage_results: Annotated[list, add]
    failures: Annotated[list, add]

def make_node(stage_name: str):
    """工厂函数：把现有 Stage 包装成 LangGraph node"""
    stage_cls = STAGE_MAP[stage_name]

    async def node(state: PipelineState, config) -> PipelineState:
        from src.core.database import async_session_factory
        context = PipelineContext.from_json(state["context_json"])

        async with async_session_factory() as session:
            stage = stage_cls(session)
            stage_input = StageInput(
                pipeline_id=state["pipeline_id"],
                project_id=state["project_id"],
                context=context,
            )
            output = await stage.run(stage_input)

        return {
            "context_json": context.to_json(),
            "stage_results": [{stage_name: output.status}],
            "current_stage": stage_name,
            "status": "completed" if output.is_success else "failed",
            "failures": [stage_name] if not output.is_success else [],
        }
    return node

def build_graph():
    builder = StateGraph(PipelineState)

    builder.add_node("ingestion", make_node("ingestion"))
    builder.add_node("parsing", make_node("parsing"))
    builder.add_node("analysis", make_node("analysis"))
    builder.add_node("generation", make_node("generation"))
    builder.add_node("review", make_node("review"))
    builder.add_node("execution", make_node("execution"))
    builder.add_node("reporting", make_node("reporting"))
    builder.add_node("regression", make_node("regression"))

    builder.add_edge(START, "ingestion")
    builder.add_edge("ingestion", "parsing")
    builder.add_edge("parsing", "analysis")
    builder.add_edge("analysis", "generation")
    builder.add_edge("generation", "review")
    builder.add_edge("execution", "reporting")
    builder.add_edge("reporting", "regression")
    builder.add_edge("regression", END)

    def route_review(state: PipelineState) -> str:
        ctx = state["context_json"]
        if ctx.get("review_approved"):
            return "execution"
        return "generation"

    builder.add_conditional_edges("review", route_review, {
        "execution": "execution",
        "generation": "generation",
    })

    return builder.compile(
        checkpointer=SqliteSaver.from_conn_string("storage/langgraph_checkpoints.db"),
        interrupt_before=["review"],
    )
```

### 评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 改动量 | ★★★★★ | 1 个新文件 ~150 行 |
| 可靠性 | ★★★☆☆ | 双层抽象可能产生竞态 |
| 可维护性 | ★★★☆☆ | 三层排查路径 |
| 回滚风险 | ★★★★★ | 删文件即刻恢复 |
| LangGraph 利用率 | 30% | |

---

## 方案二：完全迁移模式（2-3 周）

**核心思路**：去掉 FSM、AbstractStage、PipelineContext、orchestrator。8 个 Stage 从 `class` 改为 `async function`。全部 LangGraph 原生。

**代码量**：~2000 行重写 + 删除 FSM/orchestrator/context。

```python
# src/pipeline/graph_state.py

from typing import TypedDict, Annotated, Optional
from operator import add

class PipelineState(TypedDict):
    pipeline_id: str
    project_id: str
    status: str
    current_stage: str

    raw_texts: Optional[list[str]]
    parsed_requirements: Optional[list]
    test_plan: Optional[dict]
    test_cases: Optional[list]
    execution_results: Optional[dict]

    review_decision: Optional[str]
    review_feedback: Optional[str]
    stage_logs: Annotated[list, add]
    errors: Annotated[list, add]


# src/pipeline/graph_nodes.py

async def ingestion_node(state: PipelineState, config) -> PipelineState:
    """替代 IngestionStage.execute()"""
    from src.core.database import async_session_factory
    from sqlalchemy import select
    from src.core.models.models import Document

    async with async_session_factory() as session:
        result = await session.execute(
            select(Document).where(Document.project_id == state["project_id"])
        )
        docs = result.scalars().all()
        raw_texts = [d.raw_text for d in docs if d.raw_text]

    return {
        "raw_texts": raw_texts,
        "stage_logs": [{"stage": "ingestion", "status": "completed"}],
    }

async def review_node(state: PipelineState, config) -> PipelineState:
    """替代 ReviewStage + 人工卡点"""
    from langgraph.types import interrupt

    decision = interrupt({
        "question": "请评审生成的用例",
        "test_cases": state["test_cases"],
    })

    return {
        "review_decision": decision["action"],
        "review_feedback": decision.get("feedback"),
    }


# src/pipeline/graph.py

def build_graph():
    builder = StateGraph(PipelineState)

    builder.add_node("ingestion", ingestion_node)
    builder.add_node("parsing", parsing_node)
    builder.add_node("analysis", analysis_node)
    builder.add_node("generation", generation_node)
    builder.add_node("review", review_node)
    builder.add_node("execution", execution_node)
    builder.add_node("reporting", reporting_node)
    builder.add_node("regression", regression_node)

    builder.add_edge(START, "ingestion")
    builder.add_edge("ingestion", "parsing")
    builder.add_edge("parsing", "analysis")
    builder.add_edge("analysis", "generation")
    builder.add_edge("generation", "review")
    builder.add_conditional_edges("review", route_after_review, {
        "execution": "execution",
        "generation": "generation",
    })
    builder.add_edge("execution", "reporting")
    builder.add_edge("reporting", "regression")
    builder.add_edge("regression", END)

    return builder.compile(
        checkpointer=SqliteSaver.from_conn_string("storage/langgraph.db"),
        interrupt_before=["review"],
    )

graph = build_graph()

# API 调用
config = {"configurable": {"thread_id": pipeline_id}}
graph.invoke(initial_state, config)

# Review 决定
from langgraph.types import Command
graph.invoke(Command(resume={"action": "approved"}), config)
```

### 评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 改动量 | ★★☆☆☆ | ~2000 行重写 |
| 可靠性 | ★★★★☆ | 单一抽象层 |
| 可维护性 | ★★★★★ | 纯函数，结构化 |
| 回滚风险 | ★☆☆☆☆ | 不可逆 |
| LangGraph 利用率 | 90% | |

---

## 方案三：Graph 编排 + 保留 Stage 逻辑（1-1.5 周，推荐）

**核心思路**：LangGraph 只做编排（状态流转/暂停/恢复/分支），业务逻辑留在现有 Stage 类中。删掉 transitions FSM。

**代码量**：~500 行（删 state_machine.py，改 orchestrator.py，Stage 接口微调）。

```python
# src/pipeline/state.py —— 新文件

from typing import TypedDict, Annotated, Optional
from operator import add

class PipelineState(TypedDict):
    pipeline_id: str
    project_id: str
    status: str
    current_stage: str

    document_ids: list[str]
    raw_texts: Optional[list[str]]
    parsed_requirements: Optional[list]
    test_plan: Optional[dict]
    test_cases: Optional[list]
    review_feedback: Optional[str]
    project_config: dict

    stage_logs: Annotated[list, add]
    stage_timings: Annotated[list, add]


# src/pipeline/graph.py —— 新文件

def make_stage_node(stage_name: str, stage_cls):
    """每个 Stage 只改返回值：从 StageOutput 改为 State 更新 dict"""

    async def node(state: PipelineState, config) -> PipelineState:
        from src.core.database import async_session_factory

        async with async_session_factory() as session:
            stage = stage_cls(session)
            output = await stage.execute_for_graph(state)

        output["current_stage"] = stage_name
        return output

    return node

def build_graph():
    from src.pipeline.stages.ingestion import IngestionStage
    from src.pipeline.stages.parsing import ParsingStage
    # ... 其余 import

    builder = StateGraph(PipelineState)

    builder.add_node("ingestion", make_stage_node("ingestion", IngestionStage))
    builder.add_node("parsing", make_stage_node("parsing", ParsingStage))
    # ... 其余 add_node

    builder.add_edge(START, "ingestion")
    builder.add_edge("ingestion", "parsing")
    # ... 其余边

    builder.add_conditional_edges("review", route_review, {
        "execution": "execution",
        "generation": "generation",
    })
    builder.add_edge("regression", END)

    return builder.compile(
        checkpointer=SqliteSaver.from_conn_string("storage/langgraph.db"),
        interrupt_before=["review"],
    )
```

### 评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 改动量 | ★★★★☆ | ~500 行 |
| 可靠性 | ★★★★☆ | Stage 逻辑不动，编排层独立 |
| 可维护性 | ★★★★☆ | 编排/业务分离 |
| 回滚风险 | ★★★★☆ | 1 天可退 |
| LangGraph 利用率 | 60% | |

---

## 量化对比

| | 方案一「薄包装」 | 方案二「全量迁移」 | 方案三「混合」 |
|---|---|---|---|
| 新增/修改 | ~150 行 | ~2000 行 | ~500 行 |
| Stage 改动 | 零 | 8 个 class → function | 只改返回值接口 |
| 删除代码 | 无 | FSM+orchestrator+context | FSM 文件 |
| 抽象层数 | 3 | 1 | 2 |
| 人工卡点 | `interrupt_before` | `interrupt()` 动态 | `interrupt_before` |
| 并行解析 | 需额外实现 | `Send()` 原生 | `Send()` 可用 |
| 可回滚 | 即删即退 | 不可逆 | 1 天可退 |
| 调试难度 | 高 | 低 | 中 |
| 推荐场景 | 快速验证 LangGraph | 长期以 LangGraph 为核心 | **生产迁移首推** |

## 涉及文件清单

| 文件 | 方案一 | 方案二 | 方案三 |
|------|--------|--------|--------|
| `src/pipeline/orchestrator.py` | 不动 | 删除 | 重写 |
| `src/pipeline/state_machine.py` | 闲置 | 删除 | 删除 |
| `src/pipeline/context.py` | 不动 | 删除 | 保留 |
| `src/pipeline/stages/base.py` | 不动 | 删除 | 微调 |
| `src/pipeline/stages/*.py` (8 files) | 不动 | 重写为 function | 改返回值 |
| `src/api/v1/pipelines.py` | 微调 | 重写 | 微调 |
| `src/pipeline/graph_state.py` | **新增** | **新增** | **新增** |
| `src/pipeline/graph.py` | **新增** | **新增** | **新增** |
| `src/pipeline/graph_nodes.py` | — | **新增** | — |
