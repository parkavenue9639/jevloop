"""Evidence and GitHub-safe SVG checks for the paired execution graphic."""

import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from render_paired_loop import COPY, RUN_ID, TRACE, load_run, palette, render


class PairedLoopTests(unittest.TestCase):
    def test_published_run(self):
        lanes = load_run()
        self.assertEqual(len(lanes["jev"]["steps"]), 5)
        self.assertEqual(len(lanes["baseline"]["steps"]), 14)
        self.assertEqual(lanes["jev"]["metrics"]["elapsed_ms"], 26421)
        self.assertEqual(lanes["baseline"]["metrics"]["elapsed_ms"], 64059)
        self.assertTrue(lanes["jev"]["steps"][2]["was_denied"])

    def test_svg_preserves_every_operation(self):
        lanes = load_run()
        for lang in COPY:
            svg = render(lanes, lang, palette())
            self.assertEqual(svg, render(lanes, lang, palette()))
            root = ET.fromstring(svg)
            self.assertEqual(root.attrib["lang"], lang)
            for name, lane in lanes.items():
                group = next(el for el in root.iter() if el.get("data-lane") == name)
                rows = [el for el in group.iter() if el.get("data-step")]
                self.assertEqual([el.get("data-operation") for el in rows],
                                 [step["decision"]["operation"] for step in lane["steps"]])
                self.assertEqual([el.get("data-step") for el in rows],
                                 [str(i + 1) for i in range(len(lane["steps"]))])
            allowed = {"svg", "g", "rect", "text", "title", "desc"}
            for el in root.iter():
                self.assertIn(el.tag.split("}")[-1], allowed)
                self.assertFalse(any(k.lower().startswith("on") or "href" in k for k in el.attrib))
            self.assertNotIn("data:image", svg)
            self.assertNotIn("/Users/", svg)

    def test_rejects_incomplete_or_inconsistent_evidence(self):
        records = [json.loads(line) for line in TRACE.read_text().splitlines()]
        selected = [r for r in records if r["run_id"] == RUN_ID]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            for metric in ("missing-final", "step-count", "denial-count"):
                modified = json.loads(json.dumps(selected))
                if metric == "missing-final":
                    modified = [r for r in modified if r["event"]["type"] != "final"]
                else:
                    final = next(r["event"] for r in modified if r["event"]["type"] == "final")
                    if metric == "step-count":
                        final["metrics"]["routing"]["decision_steps"] += 1
                    else:
                        final["metrics"]["denial_count"] += 1
                path.write_text("\n".join(json.dumps(r) for r in modified))
                with self.subTest(metric=metric), self.assertRaises(ValueError):
                    load_run(path)


if __name__ == "__main__":
    unittest.main()
