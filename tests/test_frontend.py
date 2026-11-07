"""Frontend checks without a browser: JavaScript syntax (node --check) and that every named import exists."""
import pathlib
import re
import shutil
import subprocess

import pytest

STATIC = pathlib.Path(__file__).resolve().parent.parent / "app" / "static"
MODULES = sorted((STATIC / "js").rglob("*.js"))
EXPORT = re.compile(r"^export (?:async )?(?:function|const|let|class) ([\w$]+)", re.M)
EXPORT_LIST = re.compile(r"^export \{([^}]*)\}", re.M)
IMPORT = re.compile(r'^import \{([^}]*)\} from "([^"]+)"', re.M)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("path", MODULES + [STATIC / "app.js"], ids=lambda p: str(p.relative_to(STATIC)))
def test_syntax(path):
    # parsed as an ES module from stdin: `node --check file.js` let a broken template literal through (0.5.6)
    result = subprocess.run(["node", "--input-type=module", "--check"], input=path.read_text(encoding="utf-8"),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _exports(path):
    text = path.read_text(encoding="utf-8")
    names = set(EXPORT.findall(text))
    for group in EXPORT_LIST.findall(text):
        names |= {x.strip().split(" as ")[-1] for x in group.split(",") if x.strip()}
    return names


def test_named_imports_exist():
    exports = {p.resolve(): _exports(p) for p in MODULES}
    problems = []
    for path in MODULES:
        for names, source in IMPORT.findall(path.read_text(encoding="utf-8")):
            target = (path.parent / source).resolve()
            if target not in exports:
                problems.append(f"{path.name}: module {source} not found")
                continue
            for name in (x.strip().split(" as ")[0] for x in names.split(",")):
                if name and name not in exports[target]:
                    problems.append(f"{path.name}: {source} has no export {name}")
    assert not problems, problems


def test_index_loads_the_entry_module():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'type="module" src="/assets/__VERSION__/js/main.js"' in html


def test_versioned_assets():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.version import VERSION
    with TestClient(app) as c:
        html = c.get("/").text
        assert f'src="/assets/{VERSION}/js/main.js"' in html
        r = c.get(f"/assets/{VERSION}/js/core/state.js")
        assert r.status_code == 200 and "canEditProject" in r.text
        assert c.get("/assets/0.0.1/js/core/state.js").headers["cache-control"] == "no-cache"
        assert c.get(f"/assets/{VERSION}/locales/de.json").status_code == 200
        for bad in ("../api/site.py", "..%2Fapi%2Fsite.py", "core/../../index.html"):
            assert c.get(f"/assets/{VERSION}/js/{bad}").status_code == 404
        assert c.get(f"/assets/{VERSION}/pwa/icon-192.png").status_code == 404


def test_used_functions_are_imported():
    """A function exported by another module and called here must be imported or defined here."""
    exports = {p.resolve(): _exports(p) for p in MODULES}
    problems = []
    for path in MODULES:
        text = path.read_text(encoding="utf-8")
        local = set(re.findall(r"(?:function|const|let|var|class)\s+([\w$]+)", text))
        local |= {x.strip().split(" as ")[-1] for names, _ in IMPORT.findall(text) for x in names.split(",")}
        local |= set(re.findall(r"\(([\w$]+)\)\s*=>", text)) | set(re.findall(r"([\w$]+)\s*=>", text))
        code = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)
        for other, names in exports.items():
            if other == path.resolve():
                continue
            for name in names:
                if name not in local and re.search(r"(?<![\w$.])" + re.escape(name) + r"\(", code):
                    problems.append(f"{path.name}: calls {name}() from {other.name} without importing it")
    assert not problems, sorted(set(problems))
