"""Dependency-free checks for the published topology example and README embeds."""
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
SVG = "{http://www.w3.org/2000/svg}"


class TopologyAssetsTest(unittest.TestCase):
    def test_source_is_two_consecutive_llm_assisted_iterations(self):
        data = json.loads((ASSETS / "topology-example.json").read_text())
        self.assertEqual(data["source"]["run_id"], "43418c7bbefb")
        self.assertEqual([x["seq"] for x in data["events"]], [n for n in range(3, 24) if n != 13])
        offsets = [x["offset_ms"] for x in data["events"]]
        self.assertEqual(offsets[0], 0)
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(data["source"]["timing"]["speed"], 3)
        self.assertEqual(data["source"]["timing"]["end_seq"], 28)
        self.assertGreater(data["source"]["timing"]["end_offset_ms"], offsets[-1])
        events = [x["event"] for x in data["events"]]
        self.assertEqual([e["step"] for e in events if e["type"] == "attempt_started"], [1, 2])
        self.assertEqual([e["response"]["operation"] for e in events if e["type"] == "jev_response"], ["WRITE_FILE", "ANSWER"])
        self.assertEqual([e["kind"] for e in events if e["type"] == "llm_started"], ["parameter_authoring", "authoring"])
        self.assertIn("main.py", next(e for e in events if e["type"] == "step")["step"]["outcome"]["action"])
        self.assertEqual(events[-1]["step"]["decision"]["operation"], "ANSWER")
        text = json.dumps(data)
        for private in ('"messages"', '"content"', '"reason"', '"text"', '/Users/', 'api_key', 'Authorization'):
            self.assertNotIn(private, text)

    def test_next_loop_adds_real_file_and_batch_candidates(self):
        data = json.loads((ASSETS / "topology-example.json").read_text())
        first, second = [x["event"]["questions"] for x in data["events"] if x["event"]["type"] == "jev_request"]
        self.assertEqual([len(first), len(second)], [8, 14])
        self.assertEqual(len(set(second) - set(first)), 6)
        for phase in ("inspect", "verify"):
            key = f"target__{phase}__read_file"
            self.assertEqual(set(first[key]["criteria"]), {".gitkeep", "LLM_PARAMETERS"})
            self.assertEqual(set(second[key]["criteria"]), {"main.py", ".gitkeep", "LLM_PARAMETERS"})
            self.assertNotIn(f"target_mode__{phase}__read_file", first)
            self.assertIn(f"target_mode__{phase}__read_file", second)

    def test_vectors_are_safe_complete_and_sharp(self):
        for lang in ("en", "zh-CN"):
            path = ASSETS / f"decision-topology-{lang}.svg"
            root = ET.parse(path).getroot()
            self.assertEqual(root.attrib["viewBox"], "0 0 1742 872")
            for tag in ("script", "foreignObject", "image", "animate"):
                self.assertEqual(root.findall(f".//{SVG}{tag}"), [])
            text = path.read_text()
            self.assertNotIn("var(--", text)
            self.assertNotIn("color-mix(", text)
            self.assertIn("LLM", text)
            self.assertIn("ANSWER", text)
            self.assertIn("RESPOND", text)
            self.assertEqual(sum("flow-fan " in el.attrib.get("class", "") for el in root.iter()), 16)
            self.assertEqual(sum("is-new-candidate" in el.attrib.get("class", "") for el in root.iter()), 2)
            self.assertEqual(sum("is-new-head" in el.attrib.get("class", "") for el in root.iter()), 6)

    def test_readmes_embed_animation_and_link_static_alternative(self):
        for readme, lang in (("README.md", "en"), ("README.zh-CN.md", "zh-CN")):
            text = (ROOT / readme).read_text()
            for ext in ("gif", "svg"):
                filename = f"decision-topology-{lang}.{ext}"
                self.assertIn(f"docs/assets/{filename}", text)
                self.assertTrue((ASSETS / filename).is_file())
            gif = (ASSETS / f"decision-topology-{lang}.gif").read_bytes()
            self.assertEqual(gif[:6], b"GIF89a")
            self.assertLess(len(gif), 3_000_000, "README animation became too large")

    def test_palette_matches_other_readme_panels(self):
        reference = ET.parse(ASSETS / "agent-loop-paired-en.svg").getroot()
        paper = reference.find(f".//{SVG}rect").attrib["fill"]
        self.assertEqual(paper, "#faf9f6")
        for lang in ("en", "zh-CN"):
            path = ASSETS / f"decision-topology-{lang}.svg"
            root = ET.parse(path).getroot()
            self.assertEqual(root.find(f"{SVG}rect").attrib["fill"], paper)
            text = path.read_text()
            for color in ("#141413", "#6e6b64", "#c96442", "#f0eee5"):
                self.assertIn(color, text)
            for dark_color in ("#0f1924", "#101c28", "#e2ecf3", "#ff947c"):
                self.assertNotIn(dark_color, text)


if __name__ == "__main__":
    unittest.main()
