"""Rebuild stories/index.json from every stories/YYYY-MM-DD.json file."""
import json, pathlib, re
root = pathlib.Path(__file__).resolve().parent.parent / "stories"
items = []
for f in sorted(root.glob("*.json")):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f.name):
        continue
    s = json.loads(f.read_text(encoding="utf-8"))
    items.append({k: s.get(k) for k in ("date", "no", "title", "series", "part", "topic", "hook") if s.get(k) is not None})
items.sort(key=lambda x: x["date"], reverse=True)
(root / "index.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"index.json: {len(items)} stories")
