"""Fixed, derived FastAPI task families; no model calls or runner side effects.

Goals are ordinary user requests. Facts, fixtures and executable checks belong
to the evaluation harness, not runtime parameter selection or agent policy.
"""

import hashlib
import json
import shlex
import textwrap


def _code(value):
    return textwrap.dedent(value).strip() + "\n"


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


SETTINGS = {
    "catalog_label": "Lantern Catalog", "revision": "cedar-v1",
    "warehouse": "east-depot", "default_limit": 2,
}
ITEMS = [
    {"id": 101, "name": "Paper Kite", "stock": 7},
    {"id": 102, "name": "Brass Compass", "stock": 12},
    {"id": 103, "name": "Canvas Pouch", "stock": 4},
]
UPDATED_SETTINGS = {
    "catalog_label": "Lantern Catalog", "revision": "cedar-v2",
    "warehouse": "west-depot", "default_limit": 1,
}
UPDATED_ITEMS = [
    {"id": 101, "name": "Paper Kite", "stock": 3},
    {"id": 104, "name": "Orion Thermos", "stock": 19},
]


def seed_files():
    """Small runnable project, freshly allocated for each independent family."""
    return {
        "README.md": _code('''
            # Lantern Catalog

            Maintainer: orchard-ops. This is a small FastAPI inventory service.
            app.py exports app. routes/items.py handles the item list.
            config/settings.json holds operational settings; data/items.json is
            the inventory. docs/runbook.md describes the stockout procedure.

            GET / returns {"message": "hello world"}. GET /items returns a JSON
            array of {id, name, stock}; its optional positive limit overrides
            default_limit in settings. There is no search filter yet.

            DATA_FILE overrides data/items.json; SETTINGS_FILE overrides
            config/settings.json. Both are paths, read on each request.
            Use TestClient for checks; a long-running server is unnecessary.
        '''),
        "app.py": _code('''
            from fastapi import FastAPI
            from routes.items import router

            app = FastAPI()
            app.include_router(router)

            @app.get("/")
            def hello():
                return {"message": "hello world"}
        '''),
        "routes/__init__.py": "",
        "routes/items.py": _code('''
            import json
            import os
            from pathlib import Path
            from fastapi import APIRouter, Query

            router = APIRouter()
            ROOT = Path(__file__).resolve().parents[1]

            def read_settings():
                path = Path(os.environ.get("SETTINGS_FILE", ROOT / "config/settings.json"))
                return json.loads(path.read_text(encoding="utf-8"))

            def read_items():
                path = Path(os.environ.get("DATA_FILE", ROOT / "data/items.json"))
                return json.loads(path.read_text(encoding="utf-8"))

            @router.get("/items")
            def items(limit: int | None = Query(default=None, ge=1)):
                count = limit if limit is not None else read_settings()["default_limit"]
                return read_items()[:count]
        '''),
        "config/settings.json": _json(SETTINGS),
        "data/items.json": _json(ITEMS),
        "docs/runbook.md": _code('''
            # Stockout runbook

            Never invent replacement inventory. Route stockout requests to
            amber-shelf and record ticket OPS-417 for the operator.
            The settings revision is an operational label, not a data checksum.
            After an operator changes settings or inventory, report current
            file values rather than treating an earlier answer as authoritative.
        '''),
    }


def _injections(files):
    return [{"name": name, "content": content} for name, content in files.items()]


def _probe(marker, source):
    """The marker is emitted only after assertions succeed in the Python probe."""
    source = _code(source) + f"print({marker!r})\n"
    return {"command": "python -B -c " + shlex.quote(source), "contains": marker}


def _unchanged(marker, files):
    hashes = {name: hashlib.sha256(content.encode()).hexdigest()
              for name, content in files.items()}
    return _probe(marker, f'''
        import hashlib
        from pathlib import Path
        expected = {hashes!r}
        for name, digest in expected.items():
            assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest, name
    ''')


def _api_probe(marker, body, *, items=None, settings=None):
    """Run generated code in a temporary copy; no port or persistent data writes.

    DATA_FILE and SETTINGS_FILE refer to temporary files created before import.
    This is an ordinary correctness checker, not hostile-code containment.
    """
    setup = _code(f'''
        import json
        import os
        import shutil
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        from fastapi.testclient import TestClient

        original = Path.cwd()
        with tempfile.TemporaryDirectory(prefix="fastapi-family-check-") as scratch:
            scratch = Path(scratch)
            project = scratch / "project"
            shutil.copytree(original, project, ignore=shutil.ignore_patterns(
                ".git", ".venv", "__pycache__", ".pytest_cache"))
            data_path = scratch / "items.json"
            settings_path = scratch / "settings.json"
            data_path.write_text(json.dumps({items if items is not None else ITEMS!r}), encoding="utf-8")
            settings_path.write_text(json.dumps({settings if settings is not None else SETTINGS!r}),
                                     encoding="utf-8")
            os.environ["DATA_FILE"] = str(data_path)
            os.environ["SETTINGS_FILE"] = str(settings_path)
            os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
            os.chdir(project)
            sys.path.insert(0, str(project))
            from app import app
            with TestClient(app) as client:
    ''')
    return _probe(marker, setup + textwrap.indent(_code(body), " " * 8))


HELLO_CHECK = '''
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "hello world"}
'''

QUERY_CHECK = '''
    response = client.get("/items", params={"limit": 100})
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list) and len(items) >= 2
    assert all(isinstance(item["id"], int) and isinstance(item["name"], str)
               and item["name"] and isinstance(item["stock"], int)
               and item["stock"] >= 0 for item in items)
    assert len({item["id"] for item in items}) == len(items)
    query = items[0]["name"].upper()
    found = client.get("/items", params={"q": query, "limit": 100})
    assert found.status_code == 200
    assert found.json() == [item for item in items if query.casefold() in item["name"].casefold()]
    absent = client.get("/items", params={"q": "no-match-7de09", "limit": 100})
    assert absent.status_code == 200 and absent.json() == []
'''

PERSISTENCE_CHECK = '''
    expected = json.loads(data_path.read_text())
    response = client.get("/items", params={"limit": 100})
    assert response.status_code == 200 and response.json() == expected
    replacement = [{"id": 907, "name": "Probe Blue Lantern", "stock": 8}]
    data_path.write_text(json.dumps(replacement))
    refreshed = client.get("/items", params={"limit": 100})
    assert refreshed.status_code == 200 and refreshed.json() == replacement
'''

PATCH_CHECK = '''
    original_items = json.loads(data_path.read_text())
    item = original_items[0]
    changed = client.patch(f"/items/{item['id']}", json={"stock": 31})
    assert changed.status_code == 200
    expected = {**item, "stock": 31}
    assert changed.json() == expected
    saved = json.loads(data_path.read_text())
    assert saved == [expected, *original_items[1:]]
    assert client.get("/items", params={"limit": 100}).json() == saved
    before_missing = data_path.read_bytes()
    missing = client.patch("/items/999999", json={"stock": 1})
    assert missing.status_code == 404
    assert data_path.read_bytes() == before_missing
    invalid = client.patch(f"/items/{item['id']}", json={"stock": -1})
    assert 400 <= invalid.status_code < 500
    assert data_path.read_bytes() == before_missing
'''


def _scenario(category, description, turns):
    return {"id": "fastapi-" + category.replace("_", "-"), "category": category,
            "favor": "either", "description": description, "max_steps": 20, "turns": turns}


def suite_payload():
    """Return a fresh JSON-serializable payload accepted by bench.load_scenarios."""
    seed = seed_files()
    observation = [
        {"goal": "请了解当前这个 FastAPI 项目：概括主要目录及各自职责。不要修改项目。",
         "answer_contains": ["routes", "config", "data", "docs"],
         "inject_files": _injections(seed)},
        {"goal": "这个项目叫什么，由哪个维护团队负责？请依据项目说明回答，不修改文件。",
         "answer_contains": ["Lantern Catalog", "orchard-ops"]},
        {"goal": "项目配置中的版本标签、仓库位置和默认返回条数各是什么？不要改动配置。",
         "answer_contains": ["cedar-v1", "east-depot", "2"]},
        {"goal": "库存里数量最多和最少的商品分别是什么，各有多少？比较全部库存，不修改数据。",
         "answer_contains": ["Brass Compass", "12", "Canvas Pouch", "4"]},
        {"goal": "现有商品列表接口的路径是什么？没有指定条数时如何确定条数，传入条数的最小允许值是多少？不要改代码。",
         "answer_contains": ["/items", "default_limit", "1"]},
        {"goal": "运维文档要求缺货请求转交到哪里，记录哪个工单标识？只说明当前约定。",
         "answer_contains": ["amber-shelf", "OPS-417"]},
    ]
    for index, turn in enumerate(observation, 1):
        turn["bash"] = [_unchanged(f"FAMILY_REUSE_T{index}_OK", seed)]

    generation = [
        {"goal": "当前工作目录有哪些文件？先概括现状，不创建内容。",
         "answer_contains": [".gitkeep"],
         "bash": [_probe("FAMILY_GENERATION_T1_OK", '''
             from pathlib import Path
             assert {p.name for p in Path.cwd().iterdir()} == {".gitkeep"}
         ''')]},
        {"goal": "创建一个最小 FastAPI demo。入口固定为 app.py 中的 app 对象，GET / 返回状态 200 和 JSON 对象 {\"message\": \"hello world\"}。无需常驻服务。",
         "bash": [_api_probe("FAMILY_GENERATION_T2_OK", HELLO_CHECK)]},
        {"goal": "为现有 hello 接口在 test_app.py 中增加 pytest 测试，并验证它可以正常调用。使用 TestClient，不启动常驻服务。",
         "bash": [_api_probe("FAMILY_GENERATION_T3_OK", _code(HELLO_CHECK) + _code('''
             assert Path("test_app.py").is_file()
             result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p",
                                      "no:cacheprovider", "test_app.py"],
                                     capture_output=True, text=True, timeout=30)
             assert result.returncode == 0, result.stdout + result.stderr
         '''))]},
        {"goal": "增加 GET /items 查询接口，返回 JSON 数组。先放至少两条内存商品数据，每条含唯一整数 id、非空字符串 name 和非负整数 stock。可选 q 参数按名称做不区分大小写的子串筛选，无匹配返回空数组；保留 hello 接口。",
         "bash": [_api_probe("FAMILY_GENERATION_T4_OK", _code(HELLO_CHECK) + _code(QUERY_CHECK))]},
        {"goal": "把商品从内存改为 JSON 文件存储，默认 data/items.json，环境变量 DATA_FILE 可以指定另一个文件。每次查询读取当前文件，以便外部更新立即生效。保持现有商品、查询语义和 hello 接口。",
         "bash": [_api_probe("FAMILY_GENERATION_T5_OK", _code(HELLO_CHECK)
                             + _code(QUERY_CHECK) + _code(PERSISTENCE_CHECK)),
                  _probe("FAMILY_GENERATION_DATA_OK", '''
                      import json
                      from pathlib import Path
                      items = json.loads(Path("data/items.json").read_text())
                      assert isinstance(items, list) and len(items) >= 2
                      assert all(isinstance(item["id"], int) and isinstance(item["name"], str)
                                 and isinstance(item["stock"], int) for item in items)
                  ''')]},
        {"goal": "增加 PATCH /items/{id}，请求 JSON 为 {\"stock\": 非负整数}；存在时更新库存、写回同一个 DATA_FILE 并返回完整商品对象，未知 id 返回 404 且不改变文件。保留已有查询、外部文件刷新和 hello 行为。",
         "bash": [_api_probe("FAMILY_GENERATION_T6_OK", _code(HELLO_CHECK)
                             + _code(QUERY_CHECK) + _code(PATCH_CHECK) + _code(PERSISTENCE_CHECK))]},
    ]

    updates = {"config/settings.json": _json(UPDATED_SETTINGS),
               "data/items.json": _json(UPDATED_ITEMS)}
    stable = {name: seed[name] for name in ("config/settings.json", "data/items.json",
                                          "README.md", "docs/runbook.md")}
    mixed = [
        {"goal": "请了解当前 FastAPI 工程，概括主要目录及它们的职责，暂时不要修改。",
         "answer_contains": ["routes", "config", "data", "docs"],
         "inject_files": _injections(seed),
         "bash": [_unchanged("FAMILY_MIXED_T1_OK", seed)]},
        {"goal": "当前配置的版本标签、仓库位置是什么？全部库存中哪件商品最多，有多少？先报告现状，不改文件。",
         "answer_contains": ["cedar-v1", "east-depot", "Brass Compass", "12"],
         "bash": [_unchanged("FAMILY_MIXED_T2_OK", seed)]},
        {"goal": "给现有 GET /items 增加可选 q 参数，按名称做不区分大小写的子串筛选；先筛选再应用 limit，无匹配返回空数组。保留默认条数、JSON 文件即时读取和 hello 行为，不更改现有配置、数据或文档。",
         "bash": [_api_probe("FAMILY_MIXED_T3_OK", _code(HELLO_CHECK) + _code(QUERY_CHECK)
                             + _code('''
                                 assert client.get("/items").json() == json.loads(data_path.read_text())[:2]
                                 assert client.get("/items", params={"q": "compass", "limit": 1}).json()[0]["id"] == 102
                             ''')), _unchanged("FAMILY_MIXED_T3_INPUTS_OK", stable)]},
        {"goal": "验证刚新增的查询：大小写混用能找到商品、没有匹配时为空、指定条数仍然有效，且首页正常。不要修改配置、库存或文档，不必启动服务。",
         "bash": [_api_probe("FAMILY_MIXED_T4_OK", _code(HELLO_CHECK) + _code(QUERY_CHECK)
                             + _code('''
                                 assert len(client.get("/items", params={"limit": 1}).json()) == 1
                             ''')), _unchanged("FAMILY_MIXED_T4_INPUTS_OK", stable)]},
        {"goal": "同事刚修改了 config/settings.json 和 data/items.json。请查当前版本标签、仓库位置、默认条数，以及现在库存最多的商品及数量。不要沿用先前答案，也不要改动文件。",
         "answer_contains": ["cedar-v2", "west-depot", "1", "Orion Thermos", "19"],
         "inject_files": _injections(updates),
         "bash": [_unchanged("FAMILY_MIXED_T5_OK", updates)]},
        {"goal": "增加 GET /snapshot，返回对象含 revision、warehouse、item_count、total_stock，分别来自当前配置及全部库存，不受列表默认条数限制。每次请求读取当前 SETTINGS_FILE 和 DATA_FILE（未设置时用原路径），外部更新立即反映；保留 hello 和商品查询，不改现有配置、数据或文档。",
         "bash": [_api_probe("FAMILY_MIXED_T6_OK", _code(HELLO_CHECK) + _code(QUERY_CHECK)
                             + _code('''
                                 def expected_snapshot():
                                     settings = json.loads(settings_path.read_text())
                                     items = json.loads(data_path.read_text())
                                     return {"revision": settings["revision"],
                                             "warehouse": settings["warehouse"],
                                             "item_count": len(items),
                                             "total_stock": sum(item["stock"] for item in items)}
                                 response = client.get("/snapshot")
                                 assert response.status_code == 200 and response.json() == expected_snapshot()
                                 settings_path.write_text(json.dumps({"revision": "probe-live-v3",
                                     "warehouse": "probe-north", "default_limit": 1}))
                                 data_path.write_text(json.dumps([{"id": 333, "name": "Probe", "stock": 27}]))
                                 response = client.get("/snapshot")
                                 assert response.status_code == 200 and response.json() == expected_snapshot()
                                 assert client.get("/items").json() == json.loads(data_path.read_text())
                             '''), items=UPDATED_ITEMS, settings=UPDATED_SETTINGS),
                  _unchanged("FAMILY_MIXED_T6_INPUTS_OK", updates)]},
        {"goal": "接下来交接运维：项目文档规定缺货请求转交到哪里、记录哪个工单？配置的版本标签是否等同数据校验和？请说明约定，不修改文件。",
         "answer_contains": ["amber-shelf", "OPS-417"],
         "bash": [_unchanged("FAMILY_MIXED_T7_OK", {**updates,
                             "docs/runbook.md": seed["docs/runbook.md"]})]},
        {"goal": "请新增 docs/handoff.md 作为交接记录：写明当前版本标签、仓库、全部商品及各自库存，以及缺货转交位置和工单标识；说明 /snapshot 用于读取当前汇总。必须与当前文件一致，不修改配置或库存。",
         "bash": [_probe("FAMILY_MIXED_T8_OK", '''
             from pathlib import Path
             text = Path("docs/handoff.md").read_text(encoding="utf-8")
             for fact in ("cedar-v2", "west-depot", "Paper Kite", "3", "Orion Thermos",
                          "19", "amber-shelf", "OPS-417", "/snapshot"):
                 assert fact.casefold() in text.casefold(), fact
         '''), _unchanged("FAMILY_MIXED_T8_INPUTS_OK", updates)]},
    ]
    return {
        "suite": "fastapi-task-families-v1",
        "description": (
            "Fixed derived suite, not a verbatim README replay: 6 observation-reuse, "
            "6 generation and 8 mixed turns. Scenarios are independent; each lane retains "
            "its own state across turns. Executable checks are host-defined and require "
            "successful exit plus their marker. No model/tool choice is prescribed."),
        "scenarios": [
            _scenario("observation_reuse", "Explore and reuse immutable project evidence.", observation),
            _scenario("generation", "Evolve an empty workspace into a JSON-backed FastAPI app.", generation),
            _scenario("mixed", "Read, implement, verify, refresh external changes and document.", mixed),
        ],
    }
