/** Render README vectors from the real React flow component and a public fixture.
 * node docs/render_topology.mjs [--check] [--frames /absolute/temp/directory]
 * Optional PNG/GIF conversion is separate; this renderer performs no networking.
 */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { replaySchedule } from "./topology_timing.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const requireFrontend = createRequire(join(root, "frontend/package.json"));
const requireVite = createRequire(requireFrontend.resolve("vite"));
const { buildSync } = requireVite("esbuild");
const fixture = JSON.parse(readFileSync(join(root, "docs/assets/topology-example.json"), "utf8"));
assert.equal(fixture.schema, 2);
assert.deepEqual(fixture.events.map(x => x.seq), Array.from({ length: 21 }, (_, i) => i + 3).filter(n => n !== 13));
const requests = fixture.events.filter(x => x.event.type === "jev_request");
assert.deepEqual(requests.map(x => Object.keys(x.event.questions).length), [8, 14]);
assert.deepEqual(fixture.events.filter(x => x.event.type === "jev_response").map(x => x.event.response.operation), ["WRITE_FILE", "ANSWER"]);
assert.equal(fixture.events.at(-1).event.step.outcome.status, "done");
const firstKeys = new Set(Object.keys(requests[0].event.questions));
const newHeads = Object.keys(requests[1].event.questions).filter(key => !firstKeys.has(key));
assert.equal(newHeads.length, 6);
assert.ok(!JSON.stringify(requests[0]).includes("main.py"));
assert.ok(JSON.stringify(requests[1]).includes("main.py"));
const allowedEventKeys = new Set(["type", "lane", "step", "operation", "kind", "status", "binding_mode", "needs_authoring", "escalated", "attempt_id", "questions", "response"]);
for (const { event } of fixture.events) for (const key of Object.keys(event)) assert.ok(allowedEventKeys.has(key), key);
const source = JSON.stringify(fixture);
assert.ok(!/api[_-]?key|authorization|\/Users\/|\/var\/|https?:\/\/|"messages"|"content"|"text"|"reason"/i.test(source), "Unexpected private content in public fixture");

const consoleCss = readFileSync(join(root, "frontend/src/console.css"), "utf8");
const styles = readFileSync(join(root, "frontend/src/styles.css"), "utf8");
const variables = {};
for (const text of [styles.match(/@theme\s*\{([^}]+)\}/s)[1], consoleCss.match(/:root\s*\{([^}]+)\}/s)[1]]) {
  for (const [, key, value] of text.matchAll(/(--[\w-]+):\s*([^;]+);/g)) variables[key] = value;
}
// Match the published paired-loop and summary vectors, independently of the
// interactive console's selected theme. Keep light-mode semantic lane colors.
Object.assign(variables, {
  "--color-surface": "#faf9f6", "--color-surface2": "#f0eee5",
  "--color-ink": "#141413", "--color-ink2": "#6e6b64",
  "--color-line": "#e3dfd3", "--color-accent": "#c96442",
  "--console-panel": "#faf9f6", "--console-canvas": "#f0eee5",
});
const ink = variables["--color-ink"], mutedInk = variables["--color-ink2"];
const accent = variables["--color-accent"], paper = variables["--color-surface"];
// Resolve theme variables and color-mix for portable SVG rasterizers. No HTML,
// scripts, remote fonts or external styles are embedded in the published SVG.
function mix(a, percent, b) {
  const channels = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
  const x = channels(a), y = channels(b), t = Number(percent) / 100;
  return "#" + x.map((n, i) => Math.round(n * t + y[i] * (1 - t)).toString(16).padStart(2, "0")).join("");
}
const toneColors = { jev: variables["--color-accent"], llm: variables["--console-llm"], tool: variables["--console-tool"], evidence: variables["--console-evidence"], neutral: variables["--color-ink2"], critical: variables["--color-critical"] };
let rules = [];
for (const [, selector, body] of consoleCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
  const selectors = selector.trim().split(",").map(s => s.trim()).filter(s => /^\.(flow-|fan-panel|option-|orbit-|jev-orbit|commit-junction|record-capsule|accent-)/.test(s));
  if (!selectors.length) continue;
  let declarations = body.replace(/(?:animation[\w-]*|transition|filter|cursor|outline|transform-box|transform-origin):[^;]+;?/g, "");
  declarations = declarations.replace(/var\((--[\w-]+)\)/g, (all, key) => variables[key] ?? all);
  declarations = declarations.replace(/font:\s*(?:(\d{3})\s+)?([\d.]+)px(?:\/[\d.]+)?\s+([^;]+);/g,
    (_, weight, size, family) => `font-size:${size}px;font-weight:${weight ?? 400};font-family:${family};`);
  const resolveMix = s => s.replace(/color-mix\(in srgb, (#[\da-f]{6}) (\d+)%, (#[\da-f]{6})\)/gi, (_, a, p, b) => mix(a, p, b));
  if (declarations.includes("var(--fan-tone)")) {
    for (const [i, tone] of ["jev", "tool", "llm"].entries()) {
      rules.push(`${selectors.map(s => `.fan-level-${i + 1} ${s}`).join(",")}{${resolveMix(declarations.replaceAll("var(--fan-tone)", toneColors[tone]))}}`);
    }
  } else if (declarations.includes("var(--node-tone)")) {
    for (const [tone, color] of Object.entries(toneColors)) {
      const scoped = selectors.map(s => /^\.(flow-wire|flow-svg-node)(?=[ .:#]|$)/.test(s)
        ? s.replace(/^(\.[\w-]+)/, `$1.tone-${tone}`) : `.tone-${tone} ${s}`);
      rules.push(`${scoped.join(",")}{${resolveMix(declarations.replaceAll("var(--node-tone)", color))}}`);
    }
  } else rules.push(`${selectors.join(",")}{${resolveMix(declarations)}}`);
}
const css = rules.join("\n") + `\n.flow-packet{opacity:0!important}.flow-node-visual{transform:none!important}.fan-panel{fill:${variables["--color-surface2"]}}.flow-fan.is-muted{opacity:.72}.flow-caption{fill:${mutedInk}}.flow-option.is-new-candidate .option-body{stroke:${toneColors.evidence};stroke-width:2;fill:${mix(toneColors.evidence,8,paper)}}.flow-fan.is-new-head .fan-panel{stroke:${toneColors.evidence};stroke-width:1.5}`;
const labels = {
  en: ["Loop 1 begins · restore context", "Loop 1 · only .gitkeep is a known file candidate", "Loop 1 · Jev chooses ACT → WRITE_FILE", "Loop 1 · LLM authors complete WRITE_FILE arguments", "Loop 1 · accepted call recorded before dispatch", "Loop 1 · main.py written; execution evidence recorded", "Loop 2 · NEW main.py candidate + six new batch-choice heads", "Loop 2 · Jev chooses RESPOND → ANSWER", "Loop 2 · LLM authors the answer from the updated transcript", "Loop 2 · result recorded; expanded candidates remain visible"],
  zh: ["第 1 轮开始 · 恢复上下文", "第 1 轮 · 已知文件候选只有 .gitkeep", "第 1 轮 · Jev 选择 ACT → WRITE_FILE", "第 1 轮 · LLM 生成 WRITE_FILE 完整参数", "第 1 轮 · 派发前先记录已接受的调用", "第 1 轮 · 写入 main.py，记录执行证据", "第 2 轮 · 新增 main.py 候选与 6 组批量选择分支", "第 2 轮 · Jev 选择 RESPOND → ANSWER", "第 2 轮 · LLM 从更新后的 transcript 生成回答", "第 2 轮 · 结果已记录，扩充后的候选池仍可见"],
};
const stops = [3, 4, 5, 7, 9, 12, 15, 16, 18, 23];
const schedule = replaySchedule(fixture.events, stops, fixture.source.timing);
const temporary = mkdtempSync(join(tmpdir(), "jevloop-readme-render-"));
try {
  const bundle = join(temporary, "renderer.cjs");
  buildSync({ entryPoints: [join(root, "frontend/scripts/readme-topology.tsx")], outfile: bundle, bundle: true, platform: "node", format: "cjs", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, logLevel: "silent" });
  const { renderTopology } = createRequire(import.meta.url)(bundle);
  const check = process.argv.includes("--check");
  const frameIndex = process.argv.indexOf("--frames");
  const framesDir = frameIndex >= 0 ? resolve(process.argv[frameIndex + 1]) : null;
  if (framesDir) mkdirSync(framesDir, { recursive: true });
  for (const lang of ["en", "zh"]) {
    const suffix = lang === "zh" ? "zh-CN" : "en";
    const raw = stops.map(seq => renderTopology(fixture.events.filter(x => x.seq <= seq).map(x => x.event), lang));
    const sizes = raw.map(s => s.match(/viewBox="0 0 (\d+) (\d+)"/).slice(1).map(Number));
    const width = sizes[0][0], height = Math.max(...sizes.map(x => x[1]));
    assert.ok(sizes.every(x => x[0] === width));
    const nodes = s => [...s.matchAll(/transform="(translate\([^)]+\))" class="flow-svg-node/g)].map(x => x[1]);
    raw.forEach(s => assert.deepEqual(nodes(s), nodes(raw[0]), "Main nodes moved between frames"));
    const groups = s => (s.match(/class="flow-fan /g) ?? []).length;
    assert.deepEqual(raw.map(groups), [0, 8, 9, 9, 9, 9, 14, 16, 16, 16], "Cross-iteration candidate changes disappeared from the replay");
    const frames = raw.map((s, i) => {
      if (i >= 6) {
        // Green means newly offered, NEVER selected. Only recorded Jev choices
        // get the component's coral selection highlight and check mark.
        s = s.replace(/<g\b[^>]*class="flow-option[^>]*>/g, tag => tag.includes(": main.py ·") ? tag.replace('class="flow-option', 'class="flow-option is-new-candidate') : tag);
        s = s.replace(/<g class="(flow-fan[^"]*)">([\s\S]*?<\/title>)/g, (all, classes, content) => newHeads.some(key => content.includes(key)) ? `<g class="${classes} is-new-head">${content}` : all);
        assert.equal((s.match(/is-new-candidate/g) ?? []).length, 2);
        assert.equal((s.match(/is-new-head/g) ?? []).length, 6);
      }
      const old = sizes[i][1];
      s = s.replace(/^<svg[^>]*>/, `<svg x="0" y="118" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" color="${mutedInk}">`)
        .replaceAll(`V${old - 24} H16`, `V${height - 24} H16`).replaceAll(`y="${old - 34}"`, `y="${height - 34}"`)
        .replaceAll(`height="${old}" fill=`, `height="${height}" fill=`);
      const title = lang === "zh" ? "JevLoop / 工具结果成为下一轮候选" : "JevLoop / tool results become next-loop candidates";
      const note = lang === "zh" ? "真实事件日志 3 倍速 · 连续两轮约 4.29 秒 → 1.44 秒 · LLM 补参 / 撰写，非直通或性能基准" : "3x recorded event timing / two loops: 4.29s → 1.44s / LLM-assisted, not a benchmark";
      const headCount = i === 0 ? 0 : i < 6 ? 8 : 14;
      const fixed = groups(raw[i]) - headCount;
      const badge = `${lang === "zh" ? "候选问题" : "CHOICE HEADS"}: ${headCount}${fixed ? ` + ${fixed} FIX` : ""}`;
      return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height + 158}" viewBox="0 0 ${width} ${height + 158}" role="img" aria-labelledby="title desc"><title id="title">${title}</title><desc id="desc">${labels[lang][i]}. ${note}</desc><style>${css}</style><rect width="100%" height="100%" rx="12" fill="${paper}"/><g font-family="sans-serif"><text x="28" y="36" fill="${ink}" font-size="23" font-weight="600">${title}</text><text x="${width - 28}" y="36" text-anchor="end" fill="${accent}" font-size="18">${badge}</text><text x="28" y="69" fill="${accent}" font-size="21">3× · ${labels[lang][i]}</text><text x="28" y="99" fill="${mutedInk}" font-size="15">${note}</text><text x="28" y="${height + 144}" fill="${mutedInk}" font-size="14">${lang === "zh" ? "绿色边框：本轮新增候选 / 分支（不代表选中） · 橙色：Jev 生效选择 · FIX：规则确定" : "Green outline: new candidate / head (not selected) / Coral: consumed choice / FIX: rule-defined"}</text></g>${s}</svg>\n`;
    });
    const destination = join(root, `docs/assets/decision-topology-${suffix}.svg`);
    if (check) assert.equal(readFileSync(destination, "utf8"), frames.at(-1), `Stale SVG: ${destination}`);
    else writeFileSync(destination, frames.at(-1));
    if (framesDir) {
      frames.forEach((s, i) => writeFileSync(join(framesDir, `${suffix}-${i}.svg`), s));
      const manifest = schedule.frames.map(({ stage, duration }) => ({ name: `${suffix}-${stage}`, duration }));
      writeFileSync(join(framesDir, `${suffix}.json`), JSON.stringify(manifest, null, 2));
      writeFileSync(join(framesDir, `${suffix}.txt`), manifest.map(({ name, duration }) => `file '${name}.png'\noption framerate 50\nduration ${duration}\n`).join("") + `file '${manifest.at(-1).name}.png'\noption framerate 50\n`);
    }
    console.log(`${check ? "Checked" : "Rendered"} ${suffix}: ${frames.length} stages, fixed ${width} × ${height + 158}`);
  }
} finally { rmSync(temporary, { recursive: true, force: true }); }
