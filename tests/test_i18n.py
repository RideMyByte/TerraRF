"""Every literal UI string t("…") in the frontend has a German translation with the same placeholders."""
import json
import pathlib
import re

STATIC = pathlib.Path(__file__).resolve().parent.parent / "app" / "static"
CALL = re.compile(r'\bt\(\s*"((?:[^"\\]|\\.)*)"')
PLACEHOLDER = re.compile(r"\{\w+\}")


def _literals():
    found = set()
    for path in (STATIC / "js").rglob("*.js"):
        for m in CALL.finditer(path.read_text(encoding="utf-8")):
            found.add(json.loads(f'"{m.group(1)}"'))
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    found |= set(re.findall(r'data-i18n(?:-title|-placeholder)?="([^"]+)"', html))
    return found


def test_german_covers_every_literal():
    german = json.loads((STATIC / "locales" / "de.json").read_text(encoding="utf-8"))
    missing = sorted(s for s in _literals() if s not in german)
    assert not missing, f"missing in de.json: {missing[:10]}"


def test_placeholders_match():
    german = json.loads((STATIC / "locales" / "de.json").read_text(encoding="utf-8"))
    wrong = [k for k, v in german.items() if set(PLACEHOLDER.findall(k)) != set(PLACEHOLDER.findall(v))]
    assert not wrong, wrong
