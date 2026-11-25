"""Pull stories that the daily task left in the Google Sheet relay and write them
to stories/YYYY-MM-DD.json. A row only overwrites an existing file when its
note column says "replace". Prints CHANGED=1 when something was written."""
import csv, io, json, os, pathlib, re, sys, urllib.request

SHEET = os.environ.get("INBOX_SHEET", "1uVlLcXpLCFtd3GdYHq-RsV_ceCTL27n-l91ZrF835MA")
ROOT = pathlib.Path(__file__).resolve().parent.parent
url = f"https://docs.google.com/spreadsheets/d/{SHEET}/export?format=csv&gid=0"
raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "daoyi-sync"}), timeout=60).read().decode("utf-8")
csv.field_size_limit(10_000_000)
rows = list(csv.reader(io.StringIO(raw)))
changed = []
for r in rows[1:]:
    if len(r) < 2:
        continue
    date, body = r[0].strip(), r[1].strip()
    note = (r[2] if len(r) > 2 else "").strip().lower()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not body:
        continue
    try:
        s = json.loads(body)
    except Exception as e:
        print("skip bad json", date, e); continue
    if s.get("date") != date or not s.get("title"):
        print("skip mismatched", date); continue
    p = ROOT / "stories" / f"{date}.json"
    if p.exists() and "replace" not in note:
        continue
    new = json.dumps(s, ensure_ascii=False, indent=2)
    if p.exists() and p.read_text("utf-8") == new:
        continue
    p.write_text(new, "utf-8"); changed.append(date)
print("written:", changed)
if changed and os.environ.get("GITHUB_ENV"):
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write("CHANGED=1\nDATES=" + ",".join(changed) + "\n")
