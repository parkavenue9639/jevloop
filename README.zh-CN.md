<div align="center">

<!-- HERO_IMAGE_START -->
<img src="docs/assets/jevloop-cover.svg" alt="JevLoop——更快的 Agent，更少的 LLM 调用。" width="100%" />
<!-- HERO_IMAGE_END -->

<p>
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a>
</p>

<p>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.12%2B-3776AB.svg" alt="Python 3.12+" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="Apache-2.0" /></a>
  <a href="#项目状态"><img src="https://img.shields.io/badge/status-pre--alpha-orange.svg" alt="Pre-alpha" /></a>
</p>

</div>

JevLoop 是一个由 [TypeSafe Jev](https://docs.typesafe.ai/introduction)
驱动的开源 Agent Runtime。Jev 负责快速、可校准的工具决策；LLM 仅在需要
自然语言生成或复核低置信决策时介入；确定性代码负责执行、安全、持久化与状态。

```text
观察 → Jev 决策 ──────────────→ 安全审查 → 执行 → 账本 → 循环
              ├─ 需要语言生成 → LLM 撰写 ───────┘
              └─ 低置信度     → LLM 复核 ───────┘
```

最终得到的是一个带有快速路径的传统工具调用 Agent：有限选择无需经过完整 LLM
调用；需要撰写的内容仍由 LLM 基于完整对话 Transcript 生成，并延续供应商的
Prompt Cache。

JevLoop 是独立的开源项目，由 Jev 提供能力，但不是 TypeSafe 官方产品。

## 同一个问题，两条 Agent Loop

“帮我新增一个改数据的接口。”这次历史双线运行中，JevLoop 用 **26.4 秒、5 步**
完成回答，纯 LLM Agent 用 **64.1 秒、14 步**。两条路径接收相同请求与执行配置，
工作区和对话账本相互隔离。

<img src="docs/assets/agent-loop-paired-zh-CN.svg" alt="同一问题的双线执行：左侧 JevLoop 26.4 秒、5 步；右侧纯 LLM Agent 64.1 秒、14 步。展示全部步骤，包括 JevLoop 一次被拦截的尝试。" width="100%" />

原生矢量图由已公开的历史执行记录生成，复用前端配色，没有省略步骤，并非原始截图。
本轮 JevLoop 的 5 步均使用了 LLM 辅助，步骤更少不代表完全不调用 LLM。
这是选取的一轮观测，不代表普遍性能或正确性结论。
[运行证据与渲染说明 →](docs/assets/README.md)

## 一条经过实测的快速路径

> 一次手工驱动的六轮 FastAPI Case。两条 lane 均完成并回答全部 turn；
> 这是一个范围明确的观测结果，不是普遍性能结论。

<img src="docs/assets/fastapi-session-zh-CN.svg" alt="六轮 FastAPI 观测结果：JevLoop 耗时 103.5s、预估成本 $0.040872；纯 LLM 耗时 206.7s、预估成本 $0.132537。" width="100%" />

基于[公开运行数据](docs/evidence/fastapi-6turn-20260922/summary.json)生成，
复用前端面板配色。这是静态数据可视化，不是实时仪表盘。

[完整指标、实验方法、限制、计价与 Run 证据 →](docs/fastapi-case-study.md#中文)
· [重新生成面板](docs/assets/README.md#summary-panels)

## 为什么是 JevLoop

传统 Agent Loop 会让通用 LLM 决定每一个步骤，包括“调用哪个工具”“打开哪个
已知资源”这类高频、重复、候选有限的选择。JevLoop 按任务类型拆分职责：

- **Jev 决策**：通过带完整概率分布的类型化问题选择操作和兼容目标；
- **LLM 撰写**：只为命令、查询、消息、文档、文件和最终答案生成文本；
- **LLM 复核**：在同一条追加式 Transcript 上处理低置信度 Jev 决策；
- **Runtime 执行**：统一执行预算、策略、幂等、重复保护和沙箱边界；
- **Ledger 记录**：保存 LLM 可见的原始对话，Jev 的紧凑状态由账本恢复。

当 Jev 能直接完成足够多的步骤时，这套架构旨在降低模型延迟与成本。JevLoop
不会宣称所有任务都更便宜：内置的成对实验会明确展示写作密集型与导航密集型
任务的差异，而不是把所有成本隐藏在一个总数里。

## 核心设计

### 一次请求，并行类型化决策

挂载的 `ToolSpec` 会被编译成一次 Jev 请求，其中包含操作问题和推测性目标问题。
工程代码只消费与最终操作兼容的目标 head。概率分布不完整、候选非法或结果与
argmax 不一致时，执行前直接拒绝。

### 一个账本，两种视图

LLM 读取完整 `Transcript`；Jev 读取有界的 `Workspace` 投影，其中包含已知资源、
近期观察和近期动作。持久化只保存 Transcript，Workspace 可以从账本恢复。

### 观察结果成为可选的参数绑定

按目录分页列举、按行读取和有界搜索产生带真实引用的历史观察视图。Jev 可以直接
选择兼容引用及工具声明的默认参数，也始终可以为同一操作选择 `LLM_PARAMETERS`。
部分绑定保持锁定，由 LLM 补齐其余参数；候选构造不机械匹配用户输入。
所有路径共用参数校验和执行策略。详见[实现契约](docs/observation-view-contract.md)。

### 所有 Driver 共用一个 Runtime

`JevDriver` 与 `PlainLlmDriver` 都只向同一个 `RuntimeKernel` 返回 proposal。Driver
不能执行工具、消耗预算或修改状态。Kernel 独占参数物化、安全审查、持久化
intent/effect、工具分发和终止状态。

### 可观测的公平对比

`paired_shadow` 使用相同目标、工具目录、策略、预算和不可变沙箱镜像，同时运行
JevLoop 与纯 LLM Loop。每条 lane 拥有独立的 Transcript、Docker Workspace 和
Prompt Cache 命名空间。指标会分别统计：

- Jev 决策次数、输入 Token 与成本；
- 直接绕过 LLM 的 Jev 步骤；
- LLM 撰写、仲裁和纯 LLM 决策调用；
- 供应商返回的 Cache Hit、Cache Miss 与未知输入；
- 延迟、工具活动、最终结果与总预估成本。

系统不会根据单条 lane 虚构“节省了多少 LLM 成本”；Dashboard 只比较真实执行的
成对结果及其最终状态。



## 成对基准实验

运行固定的多轮场景集：

```bash
make bench
# 或：cd backend && uv run jevloop bench
```

报告写入 `backend/artifacts/bench/<stamp>/report.{json,md}`。Suite 会记录自身摘要、
模型标识、机器校验条件、路由拆分、缓存感知成本、Wall-clock 时间和实际优势。
除非使用 `--no-journal`，运行也会出现在 Dashboard 历史中。

常用选项：

```bash
uv run jevloop bench --list
uv run jevloop bench --only <scenario-id>
uv run jevloop bench --keep-volumes
uv run jevloop bench --no-journal
```

## 执行 Profile

| Profile | Lane | 沙箱副作用 | Lark 写操作 |
| --- | --- | --- | --- |
| `single_shadow` | JevLoop | 执行 | Dry-run |
| `single_live` | JevLoop | 执行 | 通过安全审查后执行 |
| `paired_shadow` | JevLoop + 纯 LLM | 在隔离 Workspace 中执行 | 两条 lane 均为 Dry-run |

系统不提供 paired live write。`max_writes` 是外部副作用的可选紧急上限；沙箱内的
`WRITE_FILE` 与 `BASH` 由 `max_steps` 约束，不计入外部写预算。

## 安全模型

- Docker 容器以非 root 用户运行，移除 capabilities，启用
  `no-new-privileges`，RootFS 只读，并设置资源上限；
- 外部写操作默认拒绝，执行前必须通过置信策略、收件人 Allowlist 与预算；
- 物化后的 intent 在副作用前持久化，执行后记录带明确 disposition 的 effect；
- Bash 源码通过 stdin 进入容器，并在当前命令专属的 Process Group 中运行；
  命令失败仍保留为明确的 `UNKNOWN` 证据，但若命令已结束且沙箱仍可响应，不会
  永久冻结后续 Turn；
- 只有连续相同 intent 获得相同 observation、确认没有进展时，才拒绝再次重复；
- 成对 lane 使用独立 Transcript、Workspace 与供应商缓存命名空间。

当前限制：若运行中断后只留下 `dispatch_started` 而没有 effect，JSONL 审计中会
保留该状态，但暂时不会在重启后自动对账。

## 快速开始

### 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker
- Dashboard 需要 Node.js 与 pnpm

文件和 Bash 工具只在加固后的容器内执行。宿主机文件系统、凭据、Home 目录、
环境变量和 Socket 都不会挂载到 Agent Workspace。

### 离线 Smoke

Smoke 只需要 Docker，不需要模型 API Key：

```bash
cd backend
uv sync
uv run jevloop smoke
```

成功时会输出包含 `"ok": true` 的 JSON，并真实覆盖共享 Kernel、持久化生命周期、
文件写入/读取、Bash 与容器清理。

### 配置模型

```bash
cd backend
cp env.example .env
# 设置 TYPESAFE_API_KEY 和 DEEPSEEK_API_KEY。
```

可选的飞书/Lark 工具使用当前 `lark-cli` 用户身份：

```bash
lark-cli auth login --domain im,docs
```

### 执行单个目标

```bash
uv run jevloop run \
  "创建 smoke.md，内容为 hello；读取确认后回答 done。"
```

除非显式传入 `--live`，Lark 写操作保持 Dry-run。沙箱文件和 Bash 操作会在隔离
容器中真实执行。普通运行允许容器访问网络；添加 `--offline-sandbox` 可使用确定性
`--network none` 模式。

### 启动 Dashboard

```bash
cd ..
pnpm install
pnpm build:frontend
pnpm serve
# http://127.0.0.1:8790
```

开发模式：

```bash
make dev
```

Dashboard 提供对话、逐步骤模型调用 Trace、Jev-vs-LLM 对比、Session 汇总、
暂停/继续控制和 JSONL 历史回放。回放不会产生新的 API 调用。

## 仓库结构

```text
backend/   Python Runtime、Driver、问题编译器、Ledger、Guardrail、Docker
           沙箱、Lark Adapter、Benchmark Runner 与 Dashboard API
frontend/  React 19 + TypeScript + Vite Dashboard
           对话、成对比较、指标、Session 历史与回放
docs/      设计记录、系统不变量、迁移计划与验收条件
```

建议从以下文件开始阅读：

- [`backend/jevloop/kernel.py`](backend/jevloop/kernel.py) — 共享 Loop 与执行语义；
- [`backend/jevloop/drivers.py`](backend/jevloop/drivers.py) — Jev 与纯 LLM Driver；
- [`backend/jevloop/model.py`](backend/jevloop/model.py) — Jev Client、问题编译与响应校验；
- [`backend/jevloop/transcript.py`](backend/jevloop/transcript.py) — 追加式对话账本；
- [`docs/architecture.md`](docs/architecture.md) — 完整架构与目标契约。

## 开发

```bash
# Backend
cd backend
uv sync
uv run pytest
uv run ruff check .

# Frontend
cd ../frontend
pnpm install
pnpm check
pnpm build

# 整个仓库
cd ..
make test
make check
```

## 项目状态

JevLoop 当前处于 Pre-alpha，并在持续开发中。它是对“类型化快速路径 Agent Loop”
的一项启发性实现，目前主要在常规的沙箱文件、Bash 和小型服务维护任务上测试。
仓库中存在可选 Provider Adapter，并不代表已经验证了广泛任务覆盖或生产可用性。
首个稳定版本前，API、事件 Schema、包名和实测行为都可能继续调整。

## 开源协议

项目采用 [Apache License 2.0](LICENSE)。归属信息见 [NOTICE](NOTICE)。

## 致谢

JevLoop 基于 [TypeSafe Jev](https://docs.typesafe.ai/introduction)，并受到
[`browser-use/jev-ultrafast`](https://github.com/browser-use/jev-ultrafast)
所展示的 operation/target speculative fan-out 设计启发。
