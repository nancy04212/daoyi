"""Generate natural narration (zh-TW neural voice) for each story.

Runs on GitHub Actions (needs internet). For every stories/YYYY-MM-DD.json it
writes audio/YYYY-MM-DD.mp3 and audio/YYYY-MM-DD.json (segment start times, so
the app can highlight the paragraph being read). Skips stories whose narration
text has not changed.
"""
import asyncio, base64, hashlib, json, os, pathlib, re, subprocess, sys, tempfile, time, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
GAP = 0.7  # seconds of silence between paragraphs

# --- voice provider: Gemini (most natural) > OpenAI > Edge (free fallback) ---
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
GEMINI_MODELS = [m for m in [os.environ.get("GEMINI_TTS_MODEL", "").strip(), "gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts"] if m]
GEMINI_VOICE = os.environ.get("GEMINI_VOICE", "Sulafat").strip() or "Sulafat"
OPENAI_MODEL = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts").strip()
OPENAI_VOICE = os.environ.get("OPENAI_VOICE", "marin").strip()
STYLE = ("請用台灣口音的華語朗讀。你是一位溫暖、真誠的說書人，像在和好朋友分享一段很有感觸的故事："
         "語氣自然、有抑揚頓挫，句子之間有適當的停頓，講到對話時帶一點人物的情緒，講到道理時放慢、篤定。"
         "不要像播報新聞，也不要過度誇張。只唸出內容本身。")
PROVIDER = "gemini" if GEMINI_KEY else "openai" if OPENAI_KEY else "edge"
VOICE = {"gemini": f"gemini:{GEMINI_VOICE}", "openai": f"openai:{OPENAI_MODEL}:{OPENAI_VOICE}", "edge": "zh-TW-HsiaoChenNeural"}[PROVIDER]
RATE = "-6%"
_gemini_model = None

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
    if s.get("hook"):
        seg.append(("hook", s["hook"]))
    for i, p in enumerate(paras(s.get("story"))):
        seg.append((f"story-{i}", p))
    if s.get("verses"):
        seg.append(("", "接下來，我們一句一句來看。"))
        for vi, v in enumerate(s["verses"]):
            seg.append((f"verse-{vi}", (v.get("label", "") + "。" if v.get("label") else "") + "".join(v.get("lines", []))))
            for ni, n in enumerate(v.get("notes", [])):
                seg.append((f"note-{vi}-{ni}", n.get("term", "") + "。" + "".join(paras(n.get("text")))))
    m = s.get("modern") or {}
    if m:
        seg.append(("", "這段話，放到今天怎麼看？"))
        for k, label in (("plain", "白話來說，"), ("why", ""), ("gap", "")):
            if m.get(k):
                seg.append((f"modern-{k}", label + "".join(paras(m[k]))))
    if s.get("scenarios"):
        seg.append(("", "用在生活裡，可以這樣做。"))
        for i, x in enumerate(s["scenarios"]):
            seg.append((f"sc-{i}", f"{x.get('situation','')}。以前可能會{x.get('before','')}換個做法：{x.get('after','')}" + (f"當下對自己說：{x['line']}" if x.get("line") else "")))
    if s.get("examples"):
        seg.append(("", "再看看生活裡的例子。"))
        for i, x in enumerate(s["examples"]):
            seg.append((f"ex-{i}", x.get("title", "") + "。" + "".join(paras(x.get("text")))))
    if s.get("reflection"):
        seg.append(("take-r", "今天的省思。" + "".join(paras(s["reflection"]))))
    if s.get("practice"):
        seg.append(("take-p", "今天的功課。" + "".join(paras(s["practice"]))))
    ch = s.get("challenge") or {}
    if ch:
        seg.append(("challenge", "最後，今天的實踐挑戰：" + ch.get("title", "") + "。" + "".join(f"第{'一二三四五'[i]}，{x}。" for i, x in enumerate(ch.get("steps", [])[:5])) + "晚上記得回來打卡。"))
    return seg

def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return float(out.strip() or 0)

def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()

_auth_mode = [0]
_dead = set()
def _gemini_auth():
    # AI Studio keys go in x-goog-api-key; some newer key types need a Bearer header
    return {"x-goog-api-key": GEMINI_KEY} if _auth_mode[0] == 0 else {"Authorization": f"Bearer {GEMINI_KEY}"}

def gemini_synth(text, path):
    global _gemini_model
    models = ([_gemini_model] if _gemini_model else []) + [m for m in GEMINI_MODELS if m != _gemini_model and m not in _dead]
    last = None
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {"contents": [{"parts": [{"text": STYLE + "\n\n" + text}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}}}}}
        for attempt in range(6):
            try:
                data = json.loads(_post(url, body, _gemini_auth()))
                part = next(p for p in data["candidates"][0]["content"]["parts"] if "inlineData" in p)
                pcm = path.with_suffix(".pcm"); pcm.write_bytes(base64.b64decode(part["inlineData"]["data"]))
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(pcm), str(path)], check=True)
                _gemini_model = model
                time.sleep(float(os.environ.get("GEMINI_PACE", "4")))
                return
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors="ignore"); last = f"{model}: {e.code} {msg[:1500]}"
                if e.code == 429 and ("quota" in msg.lower()) and ("per day" in msg.lower() or "limit: 0" in msg.lower() or attempt >= 1):
                    print(f"  {model}: quota exhausted -> trying next model"); print("   ", re.sub(r"\s+", " ", msg)[:600])
                    _dead.add(model)
                    if _gemini_model == model: _gemini_model = None
                    break
                if e.code == 401 and _auth_mode[0] < 1:
                    _auth_mode[0] += 1; print("  switching auth header"); continue
                if e.code in (404, 400) and ("not found" in msg.lower() or "not supported" in msg.lower() or e.code == 404):
                    print("  model unavailable:", model); break
                wait = 20 * (attempt + 1) if e.code == 429 else 5
                print(f"  {e.code}, retry in {wait}s"); time.sleep(wait)
            except Exception as e:
                last = f"{model}: {e}"; print("  retry", e); time.sleep(5)
    raise RuntimeError("Gemini TTS failed: " + str(last))

def openai_synth(text, path):
    body = {"model": OPENAI_MODEL, "voice": OPENAI_VOICE, "input": text, "instructions": STYLE, "response_format": "mp3"}
    for attempt in range(5):
        try:
            path.write_bytes(_post("https://api.openai.com/v1/audio/speech", body, {"Authorization": f"Bearer {OPENAI_KEY}"}))
            return
        except urllib.error.HTTPError as e:
            print("  openai", e.code, e.read()[:200]); time.sleep(10 * (attempt + 1))
    raise RuntimeError("OpenAI TTS failed")

async def synth(text, path):
    spoken = speakable(text)
    if PROVIDER == "gemini":
        return await asyncio.to_thread(gemini_synth, spoken, path)
    if PROVIDER == "openai":
        return await asyncio.to_thread(openai_synth, spoken, path)
    import edge_tts
    for attempt in range(3):
        try:
            await edge_tts.Communicate(spoken, VOICE, rate=RATE).save(str(path))
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
    print(f"narrating {date} ({kind}) with {VOICE}: {len(seg)} segments")
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
    meta_path.write_text(json.dumps({"hash": digest, "voice": VOICE, "provider": PROVIDER, "duration": round(t - GAP, 2), "segments": marks}, ensure_ascii=False), "utf-8")
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
