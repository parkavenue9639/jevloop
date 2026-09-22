#!/usr/bin/env python3
"""Generate native SVG paired-loop views using only published, sanitized events."""

import argparse
from html import escape
import json
from pathlib import Path

from render_readme_panel import ASSETS, palette


TRACE = Path(__file__).parent / "evidence/fastapi-6turn-20260922/trace.jsonl"
RUN_ID = "ed280f843333"
COPY = {
    "en": {
        "eyebrow": "JEVLOOP / SAME REQUEST · PARALLEL AGENT LOOPS",
        "title": "One request. {jev} steps vs. {baseline}.",
        "goal": "Add an endpoint to update data.",
        "baseline": "LLM-only agent", "steps": "{} steps", "answered": "Answered",
        "columns": "STEP / OPERATION", "latency": "Decision call",
        "review": "LLM review", "denied": "DENIED",
        "operations": {"READ_FILE": "Read file", "WRITE_FILE": "Write file",
                       "BASH": "Run command", "ANSWER": "Answer"},
        "saving": "{}% less wall time",
        "note": ["{denied} denied attempt included.", "{assisted} steps used LLM assistance.",
                 "Fewer steps ≠ LLM-free execution."],
        "source": "Historical execution · FastAPI / turn 6 · 22 Sep 2026",
        "layout": "Rows align by step number, not time. Small timings are decision-call latency only.",
        "caveat": "One selected case; answer correctness and equivalence were not independently verified.",
    },
    "zh-CN": {
        "eyebrow": "JEVLOOP / 同一问题 · 双线并行执行",
        "title": "同一个问题，{jev} 步 vs. {baseline} 步。",
        "goal": "帮我新增一个改数据的接口",
        "baseline": "纯 LLM Agent", "steps": "{} 步", "answered": "已回答",
        "columns": "步骤 / 操作", "latency": "决策调用耗时",
        "review": "LLM 复核", "denied": "已拦截",
        "operations": {"READ_FILE": "读取文件", "WRITE_FILE": "写入文件",
                       "BASH": "执行命令", "ANSWER": "回答"},
        "saving": "总耗时减少 {}%",
        "note": ["包含 {denied} 次被拦截的尝试。", "{assisted} 步均使用了 LLM 辅助。",
                 "步骤更少，不等于无需 LLM。"],
        "source": "历史执行记录 · FastAPI / 第 6 轮 · 2026-09-22",
        "layout": "按步骤序号对齐，并非共享时间轴；右侧小字仅为决策调用耗时。",
        "caveat": "单次案例观测；双方回答的正确性与语义等价性未经独立验证。",
    },
}


def load_run(path=TRACE):
    lanes = {name: {"steps": [], "metrics": None} for name in ("jev", "baseline")}
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["run_id"] != RUN_ID:
            continue
        event = record["event"]
        lane = lanes.get(event.get("lane"))
        if lane is None:
            continue
        if event["type"] == "step" and event["step"]["decision"].get("operation"):
            lane["steps"].append(event["step"])
        elif event["type"] == "final":
            if lane["metrics"] is not None:
                raise ValueError("Duplicate final event")
            lane["metrics"] = event["metrics"]
    for lane in lanes.values():
        metrics = lane["metrics"]
        if not metrics or len(lane["steps"]) != metrics["routing"]["decision_steps"]:
            raise ValueError("Missing final metrics or inconsistent step count")
        if sum(bool(s["was_denied"]) for s in lane["steps"]) != metrics["denial_count"]:
            raise ValueError("Inconsistent denial count")
        if lane["steps"][-1]["decision"]["operation"] != "ANSWER":
            raise ValueError("This view requires both lanes to end with an answer")
    return lanes


def render(lanes, lang, colors):
    c = COPY[lang]
    counts = {name: len(lane["steps"]) for name, lane in lanes.items()}
    title = c["title"].format(**counts)
    bottom = 300 + max(counts.values()) * 36 + 64
    height = bottom + 113
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1040" height="{height}" '
             f'viewBox="0 0 1040 {height}" role="img" aria-labelledby="title desc" lang="{lang}">',
             f'<title id="title">{escape(title)}</title>',
             f'<desc id="desc">{escape(c["goal"] + ". " + c["layout"] + " " + c["caveat"])}</desc>',
             '<g font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Arial, '
             'Noto Sans CJK SC, Microsoft YaHei, sans-serif">']

    def rect(x, y, width, h, fill="surface", radius=0):
        parts.append(f'<rect x="{x}" y="{y}" width="{width}" height="{h}" rx="{radius}" '
                     f'fill="{colors[fill]}" stroke="{colors["line"]}"/>')

    def text(x, y, value, size=18, color="ink", weight=400, end=False):
        parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
                     f'fill="{colors[color]}" text-anchor="{"end" if end else "start"}">'
                     f'{escape(str(value))}</text>')

    rect(0.5, 0.5, 1039, height - 1)
    text(24, 32, c["eyebrow"], 16, "accent", 600)
    text(24, 78, title, 36, weight=650)
    rect(24, 100, 992, 48, "surface2", 8)
    text(40, 131, "USER", 15, "ink2")
    text(106, 131, c["goal"], 21)

    for name, x in (("jev", 24), ("baseline", 532)):
        lane = lanes[name]
        metrics = lane["metrics"]
        count = counts[name]
        end_y = 300 + count * 36
        parts.append(f'<g data-lane="{name}">')
        rect(x, 172, 484, end_y + 48 - 172, radius=10)
        text(x + 16, 206, "JevLoop" if name == "jev" else c["baseline"], 23, weight=650)
        text(x + 16, 253, f'{metrics["elapsed_ms"] / 1000:.1f}s', 38,
             "accent" if name == "jev" else "ink", 650)
        text(x + 166, 252, c["steps"].format(count), 24, weight=600)
        text(x + 16, 286, c["columns"], 14, "ink2")
        text(x + 468, 286, c["latency"], 14, "ink2", end=True)
        for i, step in enumerate(lane["steps"]):
            d = step["decision"]
            y = 300 + i * 36
            parts.append(f'<g data-step="{i + 1}" data-operation="{escape(d["operation"])}">')
            rect(x + 10, y, 464, 32, "surface2" if step["was_denied"] else "surface", 5)
            text(x + 21, y + 23, f"{i + 1:02}", 17, "ink2")
            text(x + 60, y + 23, c["operations"][d["operation"]], 19)
            if step["was_denied"] or step["has_escalation"]:
                text(x + 230, y + 22, c["denied"] if step["was_denied"] else c["review"],
                     16, "accent", 600)
            ms = d["latency_ms"]
            text(x + 458, y + 23, f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms",
                 17, "ink2", end=True)
            parts.append("</g>")
        text(x + 16, end_y + 29,
             f'{c["answered"]} · {c["steps"].format(count)} · {metrics["elapsed_ms"] / 1000:.1f}s',
             19, weight=600)
        if name == "jev":
            saving = round(100 * (1 - metrics["elapsed_ms"] / lanes["baseline"]["metrics"]["elapsed_ms"]))
            text(x + 16, end_y + 102, c["saving"].format(saving), 27, "accent", 600)
            for i, line in enumerate(c["note"]):
                text(x + 16, end_y + 143 + i * 29,
                     line.format(denied=metrics["denial_count"],
                                 assisted=metrics["routing"]["llm_assisted_jev_steps"]), 18, "ink2")
        parts.append("</g>")
    text(24, bottom + 20, c["source"] + f" · {RUN_ID}", 16, "ink2")
    text(24, bottom + 51, c["layout"], 16, "ink2")
    text(24, bottom + 82, c["caveat"], 16, "ink2")
    parts.extend(["</g>", "</svg>", ""])
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    lanes, colors = load_run(), palette()
    for lang in COPY:
        path = ASSETS / f"agent-loop-paired-{lang}.svg"
        content = render(lanes, lang, colors)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                parser.exit(1, f"Stale asset: {path.name}; run python3 docs/render_paired_loop.py\n")
        else:
            path.write_text(content, encoding="utf-8")
        print(f'{"verified" if args.check else "generated"}: {path.name}')


if __name__ == "__main__":
    main()
