"""Generate natural narration (zh-TW neural voice) for each story.

Runs on GitHub Actions (needs internet). For every stories/YYYY-MM-DD.json it
writes audio/YYYY-MM-DD.mp3 and audio/YYYY-MM-DD.json (segment start times, so
the app can highlight the paragraph being read). Skips stories whose narration
text has not changed.
"""
import asyncio, hashlib, json, pathlib, re, subprocess, sys, tempfile
import edge_tts

ROOT = pathlib.Path(__file__).resolve().parent.parent
VOICE = "zh-TW-HsiaoChenNeural"
RATE = "-6%"
GAP = 0.7  # seconds of silence between paragraphs

pron = {k: v for k, v in json.loads((ROOT / "scripts/pronounce.json").read_text("utf-8")).items() if not k.startswith("_") and k != v}
keys = sorted(pron, key=len, reverse=True)

def speakable(t):
    t = str(t)
    for k in keys:
        t = t.replace(k, pron[k])
    t = re.sub(r"[「」『』]", "", t)
    t = t.replace("——", "，").replace("…", "，").replace("vs.", "和")
    return t.strip()

def paras(x):
    return [p.strip() for p in (x if isinstance(x, list) else str(x or "").split("\n")) if str(p).strip()]

def segments(s):
    seg = [("", f"{s.get('title','')}。")]
    if s.get("source"):
        seg.append(("", "出自" + re.sub(r"[《》〈〉（）()・]", " ", s["source"]).strip() + "。"))
    for i, p in enumerate(paras(s.get("story"))):
        seg.append((f"story-{i}", p))
    if s.get("verses"):
        seg.append(("", "接下來，我們一句一句來看。"))
        for vi, v in enumerate(s["verses"]):
            seg.append((f"verse-{vi}", (v.get("label", "") + "。" if v.get("label") else "") + "".join(v.get("lines", []))))
            for ni, n in enumerate(v.get("notes", [])):
                seg.append((f"note-{vi}-{ni}", n.get("term", "") + "。" + "".join(paras(n.get("text")))))
    if s.get("examples"):
        seg.append(("", "再看看生活裡的例子。"))
        for i, x in enumerate(s["examples"]):
            seg.append((f"ex-{i}", x.get("title", "") + "。" + "".join(paras(x.get("text")))))
    if s.get("reflection"):
        seg.append(("take-r", "今天的省思。" + "".join(paras(s["reflection"]))))
    if s.get("practice"):
        seg.append(("take-p", "今天的功課。" + "".join(paras(s["practice"]))))
    return seg

def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return float(out.strip() or 0)

async def synth(text, path):
    for attempt in range(3):
        try:
            await edge_tts.Communicate(speakable(text), VOICE, rate=RATE).save(str(path))
            if path.stat().st_size > 0:
                return
        except Exception as e:
            print("  retry", attempt + 1, e)
            await asyncio.sleep(3)
    raise RuntimeError("TTS failed: " + text[:30])

async def build(story_path, kind="story"):
    s = json.loads(story_path.read_text("utf-8"))
    date = s["date"]
    if kind == "story":
        seg = segments(s)
        out_base = ROOT / "audio" / date
    else:
        script = (s.get("talk") or {}).get("script")
        if not script:
            return False
        seg = [("", p) for p in paras(script)]
        out_base = ROOT / "audio" / f"{date}-talk"
    digest = hashlib.sha1((VOICE + RATE + json.dumps(seg, ensure_ascii=False) + json.dumps(pron, ensure_ascii=False)).encode()).hexdigest()[:12]
    meta_path = out_base.with_suffix(".json")
    if meta_path.exists() and json.loads(meta_path.read_text("utf-8")).get("hash") == digest and out_base.with_suffix(".mp3").exists():
        return False
    print(f"narrating {date} ({kind}): {len(seg)} segments")
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        silence = td / "gap.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(GAP), "-c:a", "libmp3lame", "-b:a", "48k", str(silence)], check=True)
        files, marks, t = [], [], 0.0
        for k, (key, text) in enumerate(seg):
            f = td / f"{k:03d}.mp3"
            await synth(text, f)
            raw = td / f"{k:03d}r.mp3"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(f), "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "48k", str(raw)], check=True)
            d = duration(raw)
            marks.append({"key": key, "start": round(t, 2), "end": round(t + d, 2)})
            files += [raw, silence]
            t += d + GAP
        lst = td / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in files))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "libmp3lame", "-b:a", "48k", "-ar", "24000", "-ac", "1", str(out_base.with_suffix(".mp3"))], check=True)
    meta_path.write_text(json.dumps({"hash": digest, "voice": VOICE, "duration": round(t - GAP, 2), "segments": marks}, ensure_ascii=False), "utf-8")
    return True

async def main():
    (ROOT / "audio").mkdir(exist_ok=True)
    only = sys.argv[1:]
    changed = False
    for p in sorted((ROOT / "stories").glob("????-??-??.json")):
        if only and p.stem not in only:
            continue
        changed |= await build(p, "story")
        changed |= await build(p, "talk")
    print("changed" if changed else "nothing to do")

asyncio.run(main())
