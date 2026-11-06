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
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
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
    assert 'type="module" src="/static/js/main.js' in html
