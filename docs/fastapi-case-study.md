# FastAPI Paired Session Case Study

[English](#english) · [中文](#中文)

## English

### Scope

On 2026-09-22, one manually driven `paired_shadow` session evolved a small FastAPI project through six conventional turns:

1. inspect the workspace;
2. create a Hello World service;
3. start and test the service;
4. add a query endpoint;
5. move in-memory data to JSON storage;
6. add a mutation endpoint.

This is a case study of one session, not a statistically powered benchmark or an independent evaluation.

### Method

The JevLoop and LLM-only lanes received the same goal and used the same:

- LLM model;
- atomic sandbox tools;
- policy and budgets;
- immutable sandbox image;
- provider-cache policy.

Each lane had an isolated transcript, workspace, and provider-cache namespace. Both lanes completed all six turns and returned answers.

### Results

| Metric | JevLoop (Jev + small LLM) | LLM-only baseline | Observed difference |
| --- | ---: | ---: | ---: |
| Total wall time | **103.5s** | 206.7s | **50% faster (2.0×)** |
| Model calls | 22 Jev + 20 LLM | 52 LLM | 19% fewer total calls |
| Jev direct pass | 2/22 (9.1%) | — | — |
| Decision median latency | **1,080ms** | 1,552ms | 30% lower |
| Jev input / output tokens | 90.1k / 10.8k | — | — |
| LLM input / output tokens | **247.4k / 9.5k** | 1,394.0k / 23.6k | 82% fewer LLM input tokens |
| Total input / output tokens | **337.5k / 20.3k** | 1,394.0k / 23.6k | 75% fewer input tokens |
| Jev estimated cost | $0.003784 | — | — |
| LLM estimated cost | **$0.037088** | $0.132537 | 72% lower |
| Estimated total cost | **$0.040872** | $0.132537 | **69% lower (3.2×)** |
| Runtime steps | **22** | 52 | 58% fewer |
| Terminal outcome | answered 6/6 | answered 6/6 | both completed |

Jev handled 2 of 22 steps without an LLM call. Twelve low-confidence decisions were adjudicated: 3 were upheld and 9 were overridden.

The dashboard estimated cost with these rates:

- Jev input: $0.042/MTok; output: free;
- LLM input: $0.27/MTok;
- LLM cached input: $0.07/MTok;
- LLM output: $1.10/MTok.

### Interpretation and limitations

These numbers support only this case:

- the workload is conventional file editing, Python, shell, and local HTTP verification;
- it does not cover open-ended research, complex UI work, or high-stakes production operations;
- “answered” means both lanes completed and returned responses, not that this summary proves semantic equivalence on arbitrary tasks;
- wall time includes remote-model, network, and sandbox variance;
- costs are estimates from the stated token rates;
- authored-heavy, long-horizon, or poorly routed workloads can be neutral or worse than the LLM-only loop.

Use the fixed paired suite for repeatable regression testing:

```bash
make bench
```

### Evidence

Session: `3b6792da363e`

Runs, in order:

1. `6b8d05c853eb`
2. `4fd790278ced`
3. `580d508120ef`
4. `2fdbe673cb88`
5. `c50dafb5b94e`
6. `ed280f843333`

Stored dashboard runs can be replayed without issuing new model calls.

A content-redacted evidence bundle is committed at
[`docs/evidence/fastapi-6turn-20260922/`](evidence/fastapi-6turn-20260922/).
It retains event ordering, relative timing, routing, usage, cost, and
intent/effect fingerprints. Prompts, responses, tool content, recipients, and
cache-scope identifiers are removed. `manifest.json` records SHA256 hashes for
the original gitignored run files.

Recompute the summary and check the redaction contract:

```bash
python3 docs/evidence/fastapi-6turn-20260922/verify.py
```

## 中文

### 实验范围

2026-09-22 的一次 `paired_shadow` 手工 Session 用六个常规步骤演进一个小型 FastAPI 项目：

1. 查看工作区；
2. 创建 Hello World 服务；
3. 启动并测试服务；
4. 增加查询接口；
5. 把内存数据迁移到 JSON 文件；
6. 增加修改接口。

这是单次 Session 的 Case Study，不是具有统计功效的重复实验，也不是独立第三方评测。

### 实验方法

JevLoop 和纯 LLM 两条 lane 接收相同目标，并使用相同的：

- LLM 模型；
- 原子沙箱工具；
- 策略与预算；
- 不可变沙箱镜像；
- Provider Cache 策略。

每条 lane 使用独立的 Transcript、Workspace 和 Provider Cache 命名空间。双方均完成六轮并返回回答。

### 实验结果

| 指标 | JevLoop（Jev + 小 LLM） | 纯 LLM Baseline | 本次观测差异 |
| --- | ---: | ---: | ---: |
| 总 Wall time | **103.5s** | 206.7s | **快 50%（2.0×）** |
| 模型调用 | 22 Jev + 20 LLM | 52 LLM | 总调用少 19% |
| Jev 直通 | 2/22（9.1%） | — | — |
| 决策延迟中位数 | **1,080ms** | 1,552ms | 低 30% |
| Jev 输入 / 输出 Token | 90.1k / 10.8k | — | — |
| LLM 输入 / 输出 Token | **247.4k / 9.5k** | 1,394.0k / 23.6k | LLM 输入 Token 少 82% |
| 总输入 / 输出 Token | **337.5k / 20.3k** | 1,394.0k / 23.6k | 输入 Token 少 75% |
| Jev 预估成本 | $0.003784 | — | — |
| LLM 预估成本 | **$0.037088** | $0.132537 | 低 72% |
| 预估总成本 | **$0.040872** | $0.132537 | **低 69%（3.2×）** |
| Runtime Steps | **22** | 52 | 少 58% |
| 终态 | 6/6 已回答 | 6/6 已回答 | 双方均完成 |

22 个 Jev 步骤中有 2 个没有调用 LLM。共发生 12 次低置信复核，其中 3 次维持原判、9 次改判。

Dashboard 使用以下单价估算成本：

- Jev 输入：$0.042/MTok；输出免费；
- LLM 输入：$0.27/MTok；
- LLM 缓存输入：$0.07/MTok；
- LLM 输出：$1.10/MTok。

### 结论边界

这些数字只说明这个 Case：

- 任务是常规的文件编辑、Python、Shell 与本地 HTTP 验证；
- 不覆盖开放式研究、复杂 UI 或高风险生产操作；
- “已回答”表示两条 lane 都完成并返回结果，不代表该汇总证明了任意任务上的语义等价；
- Wall time 包含远程模型、网络和沙箱波动；
- 成本是按所列 Token 单价计算的估值；
- 撰写密集、长链路或路由效果不佳的任务可能与纯 LLM 持平，甚至更差。

可重复回归测试应使用固定成对 Suite：

```bash
make bench
```

### 实验证据

Session：`3b6792da363e`

六个 Run id 依次为：

1. `6b8d05c853eb`
2. `4fd790278ced`
3. `580d508120ef`
4. `2fdbe673cb88`
5. `c50dafb5b94e`
6. `ed280f843333`

已保存的 Dashboard Run 可以在不产生新模型调用的情况下回放。

仓库中已固化一份
[`docs/evidence/fastapi-6turn-20260922/`](evidence/fastapi-6turn-20260922/)
脱敏证据包。它保留事件顺序、相对时间、路由、用量、成本及 intent/effect
指纹；Prompt、响应正文、工具内容、收件人和 Cache Scope 标识均已移除。
`manifest.json` 记录了 Git 忽略目录中原始 Run 文件的 SHA256。

重新计算汇总指标并检查脱敏约束：

```bash
python3 docs/evidence/fastapi-6turn-20260922/verify.py
```
