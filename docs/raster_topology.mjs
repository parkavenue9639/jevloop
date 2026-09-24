/** Optional image build; install @resvg/resvg-js in a temporary prefix, not the app.
 * node docs/raster_topology.mjs <frames-directory> <resvg-package-path>
 * Requires ffmpeg on PATH. Model calls, browser automation and network are absent.
 */
import { createRequire } from "node:module";
import { readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

assert.ok(process.argv[2] && process.argv[3], "Pass a frames directory and a resvg package path");
const frames = resolve(process.argv[2]);
const { Resvg } = createRequire(import.meta.url)(resolve(process.argv[3]));
const assets = join(dirname(fileURLToPath(import.meta.url)), "assets");
for (const lang of ["en", "zh-CN"]) {
  const manifest = JSON.parse(readFileSync(join(frames, `${lang}.json`), "utf8"));
  for (const { name } of manifest) {
    assert.match(name, /^(en|zh-CN)-\d+(?:-enter-\d+)?$/);
    const svg = readFileSync(join(frames, `${name}.svg`));
    const png = new Resvg(svg, { font: { loadSystemFonts: true, defaultFontFamily: "Arial", sansSerifFamily: "Arial", monospaceFamily: "Menlo" } }).render().asPng();
    writeFileSync(join(frames, `${name}.png`), png);
  }
  const duration = manifest.reduce((sum, frame) => sum + frame.duration, 0).toFixed(2);
  const finalDelay = String(Math.round(manifest.at(-1).duration * 100));
  const result = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", join(frames, `${lang}.txt`), "-filter_complex", "split[a][b];[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=bayer:bayer_scale=3", "-fps_mode", "vfr", "-t", duration, "-final_delay", finalDelay, "-loop", "0", join(assets, `decision-topology-${lang}.gif`)], { stdio: "inherit" });
  assert.equal(result.status, 0, result.error?.message ?? "ffmpeg failed");
  const probe = spawnSync("ffprobe", ["-v", "error", "-show_entries", "stream=duration", "-of", "json", join(assets, `decision-topology-${lang}.gif`)], { encoding: "utf8" });
  assert.equal(probe.status, 0, probe.stderr || "ffprobe failed");
  assert.ok(Math.abs(Number(JSON.parse(probe.stdout).streams[0].duration) - Number(duration)) < .011, "Encoded GIF changed replay timing");
  console.log(`Rendered ${lang} GIF (${duration}s, original journal intervals / 3)`);
}
