---
name: 500-errors-read-stacktrace
description: 500 错误处理准则 — 读堆栈不猜测 + 模型与 DB 同步 + 改动全局检查
metadata: 
  node_type: memory
  type: feedback
---

## 500 错误：第一动作是读完整堆栈

面对任何 500 Internal Server Error，第一个动作必须是读完整异常堆栈，禁止猜测原因。堆栈里的异常类型和 SQL 语句会精确指向根因。

Why: 目录创建连续 500，反复猜了"服务没重启""参数为空"，实际根因是 MissingGreenlet 和 no such column: directory_id。读堆栈一轮就修好。

## 模型变更必须同步 DB schema

SQLAlchemy 模型定义 ≠ SQLite 表结构。改 Column 或 nullable 后必须对比并迁移 DB。SQLite 不支持 ALTER COLUMN，需重建表。

How to apply:
- 改模型后执行 PRAGMA table_info 对比
- 缺列用 ALTER TABLE ADD COLUMN
- 改约束需 CREATE TABLE new → INSERT SELECT → DROP → RENAME

## 改一处代码，全局 grep 检查连锁影响

修改 API 参数或数据结构时必须 grep 所有引用，一次性修完。

Why: 移除 create_directory 的 project_id 后，loadCLDirectories、loadCLCases 仍依赖 project_id，导致"目录不加载""点击无反应"两个连锁 bug。

How to apply:
- 改 API 参数 → grep 前端所有 fetch 调用点
- 改字段名 → grep 所有 .字段名 引用
- 改 DB 约束 → grep 所有 INSERT/UPDATE 路径
