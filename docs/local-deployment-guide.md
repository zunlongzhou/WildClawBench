# WildClawBench 本地化部署指南

## 目录

- [概述](#概述)
- [架构对比](#架构对比)
- [一键安装](#一键安装)
- [手动安装步骤](#手动安装步骤)
- [配置说明](#配置说明)
- [运行测试任务](#运行测试任务)
- [监控数据在哪看](#监控数据在哪看)
- [Multi-Agent 多智能体协同](#multi-agent-多智能体协同)
- [输出文件结构](#输出文件结构)
- [常见问题](#常见问题)

---

## 概述

本地化模式 (`openclaw-local`) 是 WildClawBench 新增的轻量级测试方式，相比 Docker 模式：

| 特性 | Docker 模式 | 本地化模式 |
|------|------------|-----------|
| 隔离性 | 完全隔离 | 共享本地环境 |
| 部署成本 | 高（需下载 GB 级镜像） | 低（npm 安装） |
| 可观测性 | 基础 token 统计 | **完整 traces + spans** |
| 多 Agent | 不支持 | **原生支持** |
| 调试体验 | 差（容器内） | **好（本地直接调试）** |
| 适用场景 | 正式评测/论文复现 | 开发迭代/二次开发/深度分析 |

---

## 架构对比

```
┌─────────────────────────────────────────────────────────────┐
│ Docker 模式 (原有)                                           │
│                                                             │
│   Host → docker run → [Container: openclaw gateway/agent]   │
│                         ↓                                   │
│                      chat.jsonl → 提取 token usage          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 本地化模式 (新增)                                            │
│                                                             │
│   Host → openclaw gateway (本地进程)                         │
│        → openclaw agent (本地进程)                           │
│        → opik-openclaw 插件 (自动追踪)                       │
│                ↓                                            │
│        Opik Server (本地 Docker 或 comet.com 云端)           │
│                ↓                                            │
│   ┌─────────────────────────────────┐                       │
│   │ 监控面板 (http://localhost:5173) │                       │
│   │  • Token 消耗                   │                       │
│   │  • 行为链 (tool call traces)    │                       │
│   │  • 思维链 (reasoning spans)     │                       │
│   │  • 中间结果                     │                       │
│   │  • Sub-agent 追踪              │                       │
│   │  • 执行时间                     │                       │
│   └─────────────────────────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 一键安装

```bash
# 运行安装脚本（交互式）
bash script/setup_local.sh
```

脚本会自动完成：
1. 检查 Node.js >= 22、Docker、Python 等依赖
2. 安装 OpenClaw CLI
3. 安装 opik-openclaw 插件
4. 启动本地 Opik Server (Docker)
5. 配置插件连接
6. 安装 Python 依赖

---

## 手动安装步骤

### Step 1: 安装 OpenClaw

```bash
npm install -g @anthropic-ai/openclaw@latest
openclaw --version
```

### Step 2: 安装 opik-openclaw 插件

```bash
openclaw plugins install clawhub:@opik/opik-openclaw
```

### Step 3: 部署 Opik Server

**方式 A: 本地 Docker（推荐，数据全在本地）**

```bash
mkdir -p ~/.opik
curl -sSL https://raw.githubusercontent.com/comet-ml/opik/main/deployment/docker-compose/docker-compose.yml \
    -o ~/.opik/docker-compose.yml
cd ~/.opik && docker compose up -d
```

验证：打开 http://localhost:5173

**方式 B: 使用 comet.com 云端**

1. 注册 https://www.comet.com/
2. 创建 Opik 项目
3. 获取 API Key

### Step 4: 配置插件

```bash
# 交互式配置
openclaw opik configure

# 或手动设置环境变量
export OPIK_API_KEY="your-api-key"
export OPIK_URL_OVERRIDE="http://localhost:5173/api"  # 本地
# export OPIK_URL_OVERRIDE="https://www.comet.com/opik/api"  # 云端
export OPIK_PROJECT_NAME="wildclaw-bench"
```

### Step 5: 验证

```bash
openclaw opik status
```

---

## 配置说明

### 环境变量 (.env)

```bash
# === 必需 ===
OPENROUTER_API_KEY=sk-or-v1-xxxxx

# === Opik 追踪（本地模式专用） ===
OPIK_API_KEY=                           # 本地 Opik 可留空
OPIK_URL_OVERRIDE=http://localhost:5173/api
OPIK_PROJECT_NAME=wildclaw-bench

# === 可选 ===
GATEWAY_PORT=18789
LOCAL_WORKSPACE_BASE=/tmp/wildclaw_local
OPENCLAW_IMAGE_MODEL=                   # 图片模型，不设则同 --model
```

### OpenClaw 插件配置 (~/.openclaw/openclaw.json)

安装脚本会自动配置，手动调整时参考：

```json
{
  "plugins": {
    "allow": ["opik-openclaw"],
    "entries": {
      "opik-openclaw": {
        "enabled": true,
        "hooks": { "allowConversationAccess": true },
        "config": {
          "enabled": true,
          "apiUrl": "http://localhost:5173/api",
          "projectName": "wildclaw-bench",
          "workspaceName": "default",
          "tags": ["wildclaw-bench"],
          "staleTraceCleanupEnabled": true,
          "staleTraceTimeoutMs": 300000
        }
      }
    }
  }
}
```

---

## 运行测试任务

### 基本用法

```bash
# 运行单个任务
bash script/run.sh openclaw-local \
  --task tasks/01_Productivity_Flow/01_Productivity_Flow_task_1_arxiv_agent.md \
  --model openrouter/anthropic/claude-sonnet-4.6

# 运行整个类别
bash script/run.sh openclaw-local \
  --category 01_Productivity_Flow \
  --model openrouter/anthropic/claude-sonnet-4.6

# 并行运行（注意：本地模式并行数建议 ≤ 2，因为共享 gateway 端口）
bash script/run.sh openclaw-local \
  --category all \
  --parallel 1 \
  --model openrouter/anthropic/claude-sonnet-4.6
```

### 禁用 Opik（纯粹跑分不要追踪）

```bash
bash script/run.sh openclaw-local --task ... --no-opik
```

### 自定义 Opik 项目名

```bash
bash script/run.sh openclaw-local --task ... --opik-project my-experiment-v1
```

---

## 批量安装 Skills

测试前需要安装多个 Skills 让 Agent/Multi-Agent 使用。最常见的场景是你有一批 skill 的 zip 包，放在一个目录里，一次性全部安装。

### 方式一：`--skills-dir` 指定 zip 包目录（推荐）

将所有 skill zip 包放在一个目录下：

```
my-skill-packs/
├── web-search.zip
├── code-review.zip
├── ddgs-search.zip
├── agentic-paper-digest.zip
└── opik-openclaw.zip
```

一行命令批量安装并运行测试：

```bash
bash script/run.sh openclaw-local \
  --task tasks/04_Search_Retrieval/04_Search_Retrieval_task_1_*.md \
  --model openrouter/anthropic/claude-sonnet-4.6 \
  --skills-dir ./my-skill-packs/
```

系统会自动：
1. 扫描目录中所有 `.zip` / `.tar.gz` 文件
2. 逐个解压并定位 skill 根目录（含 `SKILL.md` 或 `skill.json`）
3. 调用 `openclaw skills install` 安装
4. 同时也会安装目录中直接存在的 skill 子目录

### 方式二：`--install-skills` 直接指定多个 zip 文件路径

逗号分隔，支持 zip 文件、目录、ClawHub slug 混用：

```bash
bash script/run.sh openclaw-local \
  --task tasks/... \
  --model openrouter/anthropic/claude-sonnet-4.6 \
  --install-skills "./packs/web-search.zip,./packs/code-review.zip,clawhub:@comet-ml/opik-openclaw"
```

### 方式三：`--skills-config` JSON 配置文件

适合需要对每个 skill 精细配置的场景。创建 `my_skills.json`：

```json
{
  "skills": [
    {
      "name": "./packs/web-search.zip",
      "config": {
        "api_key_env": "$BRAVE_API_KEY"
      },
      "required": true
    },
    {
      "name": "./packs/code-review.zip",
      "config": {
        "strictness": "high"
      },
      "required": true
    },
    {
      "name": "./packs/ddgs-search.zip",
      "required": false
    },
    {
      "name": "clawhub:@comet-ml/opik-openclaw",
      "required": true
    }
  ]
}
```

使用：

```bash
bash script/run.sh openclaw-local \
  --task tasks/01_Productivity_Flow/01_Productivity_Flow_task_1_arxiv_agent.md \
  --model openrouter/anthropic/claude-sonnet-4.6 \
  --skills-config my_skills.json
```

### Zip 包格式要求

skill zip 包内部结构只需满足：包含 `SKILL.md` 或 `skill.json` 文件即可。支持以下结构：

```
# 结构 A：根目录直接是 skill
web-search.zip
└── SKILL.md
└── scripts/
└── ...

# 结构 B：zip 内有一层同名文件夹（更常见）
web-search.zip
└── web-search/
    └── SKILL.md
    └── scripts/
    └── ...
```

两种结构都能自动识别。

### 结合 Multi-Agent + Skills

Multi-Agent 模式中，每个 sub-agent 也可以声明自己需要的 skill zip 包：

```json
{
  "orchestrator_model": "openrouter/anthropic/claude-sonnet-4.6",
  "orchestrator_prompt": "You coordinate a research team...",
  "sub_agents": [
    {
      "name": "researcher",
      "model": "openrouter/anthropic/claude-sonnet-4.6",
      "system_prompt": "You search and analyze information.",
      "skills": ["web-search", "ddgs-search"],
      "install_skills": ["./packs/web-search.zip", "./packs/ddgs-search.zip"]
    },
    {
      "name": "coder",
      "model": "openrouter/anthropic/claude-sonnet-4.6",
      "system_prompt": "You write code.",
      "skills": ["code-review"],
      "install_skills": ["./packs/code-review.zip"]
    }
  ]
}
```

运行时同时指定全局 skills 目录 + multi-agent：

```bash
bash script/run.sh openclaw-local \
  --task tasks/02_Code_Intelligence/02_Code_Intelligence_task_1_*.md \
  --model openrouter/anthropic/claude-sonnet-4.6 \
  --skills-dir ./my-skill-packs/ \
  --multi-agent custom \
  --multi-agent-config my_team.json
```

### Skills 安装报告

每次任务执行后，`usage.json` 中会包含 skills 安装报告：

```json
{
  "input_tokens": 12300,
  "output_tokens": 3120,
  "elapsed_time": 45.2,
  "skills_install_report": {
    "total": 5,
    "success": 5,
    "failed": 0,
    "total_time_ms": 6200,
    "details": [
      {"name": "web-search", "success": true, "message": "installed from web-search.zip", "install_time_ms": 1500},
      {"name": "code-review", "success": true, "message": "installed from code-review.zip", "install_time_ms": 1200},
      {"name": "ddgs-search", "success": true, "message": "installed from ddgs-search.zip", "install_time_ms": 1100},
      {"name": "agentic-paper-digest", "success": true, "message": "installed from agentic-paper-digest.zip", "install_time_ms": 1300},
      {"name": "opik-openclaw", "success": true, "message": "installed from opik-openclaw.zip", "install_time_ms": 1100}
    ]
  }
}
```

### 查看当前已安装的 Skills

```bash
openclaw skills list
```

---

## 监控数据在哪看

### 1. Opik Web 面板（主要入口）

**地址**: http://localhost:5173 （本地部署）或 https://www.comet.com/opik （云端）

面板提供：

| 页面 | 看什么 |
|------|-------|
| **Traces** | 每次 Agent 执行的完整追踪链 |
| **Spans** | 每个 LLM 调用、Tool 调用、Sub-agent 调用的详细信息 |
| **Metrics** | Token 消耗趋势、成本统计、延迟分布 |
| **Comparison** | 不同模型/配置的对比分析 |

**你能看到的指标**：

```
Trace (一次完整的 Agent 执行)
├── LLM Span (每次 LLM 调用)
│   ├── Input Tokens
│   ├── Output Tokens
│   ├── Thinking/Reasoning Content
│   ├── Duration (ms)
│   └── Cost ($)
├── Tool Span (每次工具调用)
│   ├── Tool Name (read_file, write_file, bash, etc.)
│   ├── Input (工具参数)
│   ├── Output (工具返回)
│   ├── Error (如果有)
│   └── Duration (ms)
└── SubAgent Span (子 Agent 生命周期)
    ├── Agent Name
    ├── Spawn Metadata
    ├── Duration (ms)
    └── Result/Error
```

### 2. 本地输出文件

每次任务执行后，输出保存在 `output/openclaw-local/<category>/<task_id>/<run_suffix>/`：

```
output/openclaw-local/01_Productivity_Flow/01_task_1/claude-sonnet_20260526_0100_abc123/
├── score.json          # 评分结果
├── usage.json          # Token 使用统计
├── opik_traces.json    # 【重要】详细 Opik 追踪数据
├── chat.jsonl          # 原始对话记录
├── gateway.log         # Gateway 日志
├── agent.log           # Agent 执行日志
└── task_output/        # 任务产出文件
```

**`opik_traces.json` 内容结构**：

```json
{
  "traces": [...],
  "summary": {
    "total_tokens": 15420,
    "input_tokens": 12300,
    "output_tokens": 3120,
    "cost_usd": 0.0234,
    "total_duration_ms": 45000,
    "llm_call_count": 8,
    "tool_call_count": 12,
    "subagent_count": 2,
    "action_chain_length": 14,
    "thinking_chain_length": 5
  },
  "tool_calls": [
    {
      "name": "read_file",
      "input": {"path": "/workspace/main.py"},
      "output": "file content...",
      "duration_ms": 120
    }
  ],
  "subagents": [...],
  "llm_spans": [...]
}
```

### 3. 命令行查看 Opik 状态

```bash
# 检查插件连接状态
openclaw opik status

# 发送测试消息验证追踪
openclaw gateway run &
openclaw message send "test trace"
# 然后在 http://localhost:5173 查看是否有新 trace
```

---

## Multi-Agent 多智能体协同

### 概念

Multi-Agent 模式利用 OpenClaw 的原生 sub-agent 能力，让一个 Orchestrator Agent 协调多个专项 Sub-Agent 协同完成任务。opik-openclaw 插件会自动追踪所有 sub-agent 的完整生命周期。

### 使用预设团队

```bash
# 研究团队：orchestrator + researcher + coder + reviewer
bash script/run.sh openclaw-local \
  --task tasks/02_Code_Intelligence/02_Code_Intelligence_task_1_*.md \
  --model openrouter/anthropic/claude-sonnet-4.6 \
  --multi-agent research-team

# 安全审计团队：orchestrator + scanner + analyst
bash script/run.sh openclaw-local \
  --task tasks/06_Safety_Alignment/06_Safety_Alignment_task_1_*.md \
  --model openrouter/anthropic/claude-sonnet-4.6 \
  --multi-agent security-audit
```

### 自定义团队配置

创建 JSON 配置文件 `my_team.json`：

```json
{
  "orchestrator_model": "openrouter/anthropic/claude-sonnet-4.6",
  "orchestrator_prompt": "You are a team lead. Delegate tasks to sub-agents based on their specialties.",
  "coordination_mode": "adaptive",
  "sub_agents": [
    {
      "name": "data-analyst",
      "model": "openrouter/anthropic/claude-sonnet-4.6",
      "system_prompt": "You are a data analyst specializing in statistical analysis and visualization.",
      "skills": ["arxiv-summarizer-orchestrator"],
      "timeout_seconds": 300,
      "max_turns": 30
    },
    {
      "name": "web-researcher",
      "model": "openrouter/google/gemini-2.5-pro",
      "system_prompt": "You are a web researcher. Search the internet for relevant information.",
      "skills": ["ddgs-search", "openclaw-free-web-search"],
      "timeout_seconds": 180,
      "max_turns": 20
    }
  ]
}
```

使用：

```bash
bash script/run.sh openclaw-local \
  --task tasks/04_Search_Retrieval/04_Search_Retrieval_task_1_*.md \
  --model openrouter/anthropic/claude-sonnet-4.6 \
  --multi-agent custom \
  --multi-agent-config my_team.json
```

### 在 Opik 中查看 Multi-Agent 追踪

打开 http://localhost:5173，你会看到：

```
Trace: "Main Task Execution"
├── LLM Span: orchestrator decision
├── SubAgent Span: "researcher" (spawned)
│   ├── LLM Span: researcher thinking
│   ├── Tool Span: ddgs-search
│   └── LLM Span: researcher response
├── SubAgent Span: "coder" (spawned)
│   ├── LLM Span: coder thinking
│   ├── Tool Span: write_file
│   └── Tool Span: bash (run tests)
└── SubAgent Span: "reviewer" (spawned)
    ├── LLM Span: reviewer analysis
    └── LLM Span: reviewer feedback
```

### 编程方式添加 Agent

在 Python 代码中直接使用：

```python
from src.agents.openclaw_local.multi_agent import (
    MultiAgentOrchestrator,
    MultiAgentConfig,
    SubAgentConfig,
)

# 创建编排器
orchestrator = MultiAgentOrchestrator()

# 定义自定义配置
config = MultiAgentConfig(
    orchestrator_model="openrouter/anthropic/claude-sonnet-4.6",
    orchestrator_prompt="You coordinate a translation team.",
    sub_agents=[
        SubAgentConfig(
            name="translator",
            model="openrouter/anthropic/claude-sonnet-4.6",
            system_prompt="You translate text between languages accurately.",
            timeout_seconds=120,
        ),
        SubAgentConfig(
            name="proofreader",
            model="openrouter/google/gemini-2.5-pro",
            system_prompt="You proofread translations for accuracy and fluency.",
            timeout_seconds=60,
        ),
    ],
)

# 注册到 OpenClaw
orchestrator.setup(config)
```

---

## 输出文件结构

```
output/
└── openclaw-local/
    ├── 01_Productivity_Flow/
    │   └── 01_Productivity_Flow_task_1_arxiv_agent/
    │       └── claude-sonnet-4.6_20260526_0100_abc123/
    │           ├── score.json           # {"overall_score": 0.85, ...}
    │           ├── usage.json           # token usage + elapsed_time
    │           ├── opik_traces.json     # 完整 Opik 追踪报告
    │           ├── chat.jsonl           # 原始 transcript
    │           ├── gateway.log
    │           ├── agent.log
    │           └── task_output/
    │               └── workspace/
    │                   └── results/
    ├── summary_claude-sonnet-4.6.json   # 类别汇总
    └── summary_all_claude-sonnet-4.6.json  # 全局汇总
```

---

## 常见问题

### Q: Opik Server 启动失败？

```bash
# 查看日志
cd ~/.opik && docker compose logs

# 重启
cd ~/.opik && docker compose down && docker compose up -d
```

### Q: opik-openclaw 插件没有产生 traces？

1. 确认插件启用：`openclaw opik status`
2. 确认 Opik Server 可达：`curl http://localhost:5173/api/v1/private/traces?limit=1`
3. 检查配置：`cat ~/.openclaw/openclaw.json | python3 -m json.tool`
4. 重启 gateway 后再试

### Q: 本地模式并行运行冲突？

本地模式共享 gateway 端口，建议 `--parallel 1`。如需并行，可用不同端口：

```bash
# 在 .env 中设置不同端口
GATEWAY_PORT=18790
```

### Q: 如何切换回 Docker 模式？

```bash
# 仍然使用原来的命令即可
bash script/run.sh openclaw --category all --parallel 4 --model ...
```

### Q: 如何只用 Opik 追踪不做评分？

目前没有单独选项，但你可以直接使用 OpenClaw：

```bash
# 直接运行 openclaw agent（不经过 WildClawBench 评测框架）
openclaw agent --message "your task here"
# traces 会自动发送到 Opik
```

### Q: Multi-Agent 模式的 sub-agent 用的是什么模型？

每个 sub-agent 可以使用不同模型。在预设团队中：
- `research-team`: 所有 agent 默认使用 `--model` 指定的模型，reviewer 使用 Gemini
- `security-audit`: 全部使用 `--model` 指定的模型
- `custom`: 完全自定义（在 JSON 配置中指定）

### Q: 如何导出 Opik 数据做进一步分析？

```python
from src.utils.opik_collector import OpikCollector

collector = OpikCollector(
    api_url="http://localhost:5173/api",
    project_name="wildclaw-bench",
)

# 获取最近 30 分钟的所有 traces
data = collector.collect_task_traces("my-task-id", lookback_minutes=60)

# data["summary"] 包含汇总指标
# data["tool_calls"] 包含所有工具调用的中间结果
# data["llm_spans"] 包含 LLM 调用详情
```

---

## 最佳实践

1. **开发阶段**用 `openclaw-local` + Opik 快速迭代
2. **正式评测**用 `openclaw` (Docker) 确保可复现性
3. **Multi-Agent** 先用预设团队验证效果，再自定义
4. **成本控制**：在 Opik 面板中按 project 监控 token 消耗趋势
5. **对比分析**：不同模型/配置使用不同 `--opik-project` 名称，方便在 Opik 中对比
