#!/usr/bin/env python3
"""Render GitHub-safe README panels from public evidence and frontend colors.

Run from any directory; no dependencies, browser, server, or private logs needed.
Use --check to verify committed assets without writing files.
"""

import argparse
from html import escape
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/fastapi-6turn-20260922/summary.json"
STYLES = ROOT / "frontend/src/styles.css"
ASSETS = ROOT / "docs/assets"

LABELS = {
    "en": {
        "eyebrow": "PAIRED RUN / FASTAPI / 6 TURNS",
        "title": "One session. Two execution paths.",
        "subtitle": "Observed results from published run evidence · 22 Sep 2026",
        "metrics": ["Total wall time", "Estimated total cost", "LLM calls"],
        "reduction": "{}% lower",
        "baseline": "LLM-only",
        "details": "WHAT HAPPENED INSIDE THE LOOP",
        "calls": "Model calls",
        "direct": "Direct Jev steps",
        "reviews": "LLM reviews",
        "reviews_detail": "{} upheld · {} overridden",
        "calls_detail": "vs {} LLM-only calls",
        "direct_detail": "Jev decisions without an LLM call",
        "outcome": "Both lanes answered {}/{} turns",
        "caveat": "One observed case, not a general benchmark. Answered does not mean verified correct.",
        "source": "Source: sanitized public summary.json · Static SVG generated from data, not a screenshot",
    },
    "zh-CN": {
        "eyebrow": "成对运行 / FASTAPI / 六轮对话",
        "title": "同一场会话，两条执行路径。",
        "subtitle": "已公开运行证据中的观测结果 · 2026 年 9 月 22 日",
        "metrics": ["总耗时", "预估总成本", "LLM 调用次数"],
        "reduction": "降低 {}%",
        "baseline": "纯 LLM",
        "details": "LOOP 内部实际发生了什么",
        "calls": "模型调用",
        "direct": "Jev 直通步骤",
        "reviews": "LLM 复核",
        "reviews_detail": "{} 次维持 · {} 次改判",
        "calls_detail": "对照组：{} 次纯 LLM 调用",
        "direct_detail": "无需调用 LLM 的 Jev 决策",
        "outcome": "两条路径均已回答 {}/{} 轮",
        "caveat": "单次案例观测，不代表普遍性能；已回答不等于任务正确性已验证。",
        "source": "数据来源：公开脱敏 summary.json · 由数据生成的静态 SVG，非截图",
    },
}


def palette():
    css = STYLES.read_text(encoding="utf-8")
    # Read the same default theme as the live dashboard, not the dark override.
    return {
        name: re.search(rf"--color-{name}:\s*(#[0-9a-fA-F]{{6}});", css)[1]
        for name in ("surface", "surface2", "ink", "ink2", "line", "accent")
    }


def render(summary, lang, colors):
    labels = LABELS[lang]
    jev, baseline = summary["lanes"]["jev"], summary["lanes"]["baseline"]
    turns = summary["turns"]
    assert jev["answered_turns"] == baseline["answered_turns"] == turns
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="644" '
        f'viewBox="0 0 1120 644" role="img" aria-labelledby="title desc" lang="{lang}">',
        f'<title id="title">{escape(labels["title"])}</title>',
        f'<desc id="desc">{escape(labels["caveat"])}</desc>',
        '<g font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Arial, '
        'Noto Sans CJK SC, Microsoft YaHei, sans-serif">',
    ]

    def rect(x, y, w, h, color, stroke=None):
        border = f' stroke="{colors[stroke]}"' if stroke else ""
        parts.append(f'<rect x="{x}" y="{y}" width="{w:.2f}" height="{h}" '
                     f'fill="{colors[color]}"{border}/>')

    def text(x, y, value, size=18, color="ink", weight=400):
        parts.append(f'<text x="{x}" y="{y}" font-size="{size}" '
                     f'font-weight="{weight}" fill="{colors[color]}">'
                     f'{escape(str(value))}</text>')

    rect(0.5, 0.5, 1119, 643, "surface", "line")
    text(36, 38, labels["eyebrow"], 15, "accent", 600)
    text(36, 84, labels["title"], 34, weight=600)
    text(36, 116, labels["subtitle"], 17, "ink2")

    metrics = [
        ("wall_time_ms", lambda n: f"{n / 1000:.1f}s"),
        ("estimated_cost_usd", lambda n: f"${n:.6f}"),
        ("llm_calls", str),
    ]
    for index, (key, fmt) in enumerate(metrics):
        x = 36 + index * 356
        j, b = jev[key], baseline[key]
        rect(x, 146, 336, 228, "surface", "line")
        text(x + 20, 179, labels["metrics"][index], 18, "ink2")
        text(x + 20, 222, fmt(j), 34, weight=600)
        text(x + 20, 252, labels["reduction"].format(round(100 * (1 - j / b))),
             18, "accent", 600)
        maximum = max(j, b)
        for y, name, value, color in (
            (282, "JevLoop", j, "accent"),
            (329, labels["baseline"], b, "ink2"),
        ):
            text(x + 20, y, name, 15, "ink2")
            text(x + 166, y, fmt(value), 16)
            rect(x + 20, y + 9, 296, 7, "surface2")
            rect(x + 20, y + 9, 296 * value / maximum, 7, color)

    text(36, 414, labels["details"], 15, "ink2", 600)
    values = [
        (labels["calls"], f'{jev["jev_calls"]} Jev + {jev["llm_calls"]} LLM',
         labels["calls_detail"].format(baseline["llm_calls"])),
        (labels["direct"], f'{jev["direct_jev_steps"]} / {jev["jev_calls"]}',
         labels["direct_detail"]),
        (labels["reviews"], str(jev["escalations"]),
         labels["reviews_detail"].format(jev["escalations_upheld"],
                                         jev["escalations_overridden"])),
    ]
    for index, (label, value, detail) in enumerate(values):
        x = 36 + index * 356
        text(x, 450, label, 18, "ink2")
        text(x, 486, value, 28, weight=600)
        text(x, 515, detail, 16, "ink2")

    rect(36, 540, 1048, 1, "line")
    text(36, 570, labels["outcome"].format(turns, turns), 18, weight=600)
    text(36, 598, labels["caveat"], 16, "ink2")
    text(36, 625, labels["source"], 14, "ink2")
    parts.extend(["</g>", "</svg>", ""])
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated assets are stale")
    args = parser.parse_args()
    summary = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    colors = palette()
    stale = []
    for lang in LABELS:
        path = ASSETS / f"fastapi-session-{lang}.svg"
        content = render(summary, lang, colors)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(content, encoding="utf-8")
            print(f"generated: {path.relative_to(ROOT)}")
    if stale:
        parser.exit(1, "Stale assets; run python3 docs/render_readme_panel.py:\n" + "\n".join(stale) + "\n")
    if args.check:
        print("verified: README panels match public evidence and frontend palette")


if __name__ == "__main__":
    main()
