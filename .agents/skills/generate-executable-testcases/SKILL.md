---
name: generate-executable-testcases
description: 根据结构化需求文件内容生成可直接AI执行的测试用例
---

## 核心原则

1. **可执行性**：每个测试用例必须包含原子操作步骤（如API调用、UI操作、脚本执行）和可验证的断言，确保能被自动化执行引擎（如AI测试工具）直接解析运行。
2. **需求追溯**：每个测试用例需明确关联对应的需求点标识，确保覆盖关系可审计。
3. **明确性与确定性**：前置条件、输入数据、预期结果均需具体化，避免歧义表述（如“系统正常工作”应转换为具体状态码、响应体字段值、界面元素状态等）。
4. **异常路径覆盖**：除正常流程外，需根据需求中的异常描述生成边界值、错误输入、权限缺失等负面测试用例。
5. **数据隔离与可重复性**：测试用例应使用可重置的测试数据（如沙箱用户、临时订单），或明确数据准备与清理步骤。

## 执行流程

1. **解析需求文件**  
   - 读取结构化输入（YAML/JSON），提取需求点列表，每个需求点包含：`id`、`描述`、`前置条件`、`操作步骤`、`预期结果`、`异常场景`（可选）。
   - 校验字段完整性，若缺失关键字段则提示补全。

2. **生成测试用例骨架**  
   - 为每个需求点分配唯一测试用例ID（如`TC-<需求ID>-<序号>`）。
   - 根据操作步骤类型识别执行动作：  
     - 若为API操作：转换为HTTP方法、URL、请求体、 headers。  
     - 若为UI操作：转换为页面元素定位与交互（点击、输入等），需依赖UI自动化框架（如Selenium）。  
     - 若为数据处理：转换为脚本调用（如Python函数、数据库查询）。
   - 将预期结果转化为精确断言（状态码、字段值、元素可见性等）。

3. **补充可执行细节**  
   - 自动补全缺失信息：如默认测试环境URL、认证Token占位符（标记为需配置变量）。
   - 根据`异常场景`生成异常用例，包括异常步骤、预期错误码或提示信息。
   - 添加数据准备与清理步骤（如通过前置脚本创建测试订单，后置脚本删除）。

4. **优化与验证**  
   - 检查用例间的依赖与冲突，确保可并行执行时数据隔离。
   - 若检测到模糊断言，尝试细化（如“响应成功” → “status_code=200 and response.body.code=0”）。
   - 输出生成报告，包含用例总数、覆盖情况统计。

## 输入格式

结构化需求文件应为YAML格式，文件结构如下：

```yaml
requirements:
  - id: "REQ-001"
    title: "用户登录"
    description: "验证合法用户可通过正确的用户名和密码登录系统"
    preconditions:
      - "用户已注册且状态正常"
      - "系统处于可访问状态"
    steps:
      - "发送POST请求到/auth/login，携带JSON体{“username”:“testuser”,“password”:“Pass1234”}"
      - "检查响应状态码"
      - "检查响应体包含token字段且非空"
    expected:
      - "状态码200"
      - "响应体格式为{“token”:“<jwt>”,“expires_in”:3600}"
    exceptions:
      - scenario: "密码错误"
        steps:
          - "发送POST请求到/auth/login，携带JSON体{“username”:“testuser”,“password”:“wrong”}"
        expected:
          - "状态码401"
          - "响应体包含错误码AUTH_FAILED"
  - id: "REQ-002"
    title: "获取订单列表"
    ...
```

## 输出格式

输出为可直接交由AI执行引擎（如基于Playwright、pytest的自定义Runner）处理的测试用例文件，推荐YAML格式。每个测试用例包含顶级字段`test_cases`，元素结构如下：

```yaml
test_cases:
  - id: "TC-REQ-001-1"
    requirement_id: "REQ-001"
    description: "正常用户登录成功"
    preconditions:
      - action: "sql"
        query: "INSERT INTO user ..."  # 可选前置数据准备
    steps:
      - action: "http_request"
        method: "POST"
        url: "{{base_url}}/auth/login"
        headers:
          Content-Type: "application/json"
        body:
          username: "testuser"
          password: "Pass1234"
        extract:  # 提取变量供后续步骤使用
          token: "response.body.token"
      - action: "assert"
        type: "status_code"
        expected: 200
      - action: "assert"
        type: "json_path"
        path: "$.token"
        exists: true
        not_empty: true
      - action: "assert"
        type: "json_path"
        path: "$.expires_in"
        value: 3600
    cleanup:
      - action: "sql"
        query: "DELETE FROM session WHERE user_id=..."
  - id: "TC-REQ-001-2"
    requirement_id: "REQ-001"
    description: "错误密码登录返回401"
    steps:
      - action: "http_request"
        method: "POST"
        url: "{{base_url}}/auth/login"
        headers:
          Content-Type: "application/json"
        body:
          username: "testuser"
          password: "wrong"
      - action: "assert"
        type: "status_code"
        expected: 401
      - action: "assert"
        type: "json_path"
        path: "$.error_code"
        value: "AUTH_FAILED"
    cleanup: []
```

**输出说明**：
- `steps`数组中的每个`action`代表一个原子操作，执行引擎按顺序执行。
- `http_request`动作可包含`extract`提取变量，`assert`动作用于验证。
- 支持`sql`、`script`（执行Python/Shell）、`ui_action`等动作类型，根据需求中的步骤类型动态生成。
- 所有动态环境参数（如`base_url`）使用`{{var}}`占位，由执行时环境变量注入。
- 每个用例的`cleanup`步骤保证数据可恢复，即使测试失败也应尽可能执行。