"""Project Jarvis: builds the morning briefing script the iPhone reads aloud.

Standard library only. Runs in GitHub Actions; writes output/briefing.txt,
output/date.txt and output/sources.md, and updates state.json.
"""
import json, os, re, sys, html, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
LONDON = ZoneInfo("Europe/London")
UA = {"User-Agent": "jarvis-briefing/1.0"}


def load(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return json.load(f)


def get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------- news ----------
def strip(text, limit=280):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(source, raw):
    items = []
    root = ET.fromstring(raw)
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        f = {}
        for c in el:
            t = c.tag.split("}")[-1]
            if t == "link":
                f.setdefault("link", (c.text or "").strip() or c.attrib.get("href", ""))
            elif t in ("title", "description", "summary", "pubDate", "published", "updated", "date"):
                f.setdefault(t, c.text or "")
        date = parse_date(f.get("pubDate") or f.get("published") or f.get("updated") or f.get("date"))
        if f.get("title") and f.get("link") and date:
            items.append({"source": source, "title": strip(f["title"], 200), "link": f["link"],
                          "date": date, "summary": strip(f.get("description") or f.get("summary"))})
    return items


def collect_news(cfg, state, now):
    items, failed = [], []
    for source, url in cfg["feeds"]:
        try:
            items += parse_feed(source, get(url))
        except Exception as e:
            failed.append(source)
            print(f"feed failed: {source}: {e}", file=sys.stderr)
    seen, uniq, titles = set(state["seen"]), [], set()
    for it in sorted(items, key=lambda x: x["date"], reverse=True):
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:60]
        if it["link"] in seen or key in titles or it["date"] > now + timedelta(hours=1):
            continue
        titles.add(key)
        uniq.append(it)
    fresh = [i for i in uniq if now - i["date"] <= timedelta(hours=30)]
    window = "the last 24 hours"
    if len(fresh) < 4:  # quiet day: widen, and say so
        fresh = [i for i in uniq if now - i["date"] <= timedelta(days=4)]
        window = "the last few days"
    # spread across sources so one busy feed cannot crowd out the rest
    picked, per = [], {}
    for it in fresh:
        if per.get(it["source"], 0) < 3:
            picked.append(it)
            per[it["source"]] = per.get(it["source"], 0) + 1
    return picked[: cfg["max_stories_to_model"]], window, failed


# ---------- weather ----------
def weather_facts(now):
    lat, lon = os.environ.get("JARVIS_LAT"), os.environ.get("JARVIS_LON")
    if not lat or not lon:
        return None
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&current=temperature_2m,apparent_temperature,wind_speed_10m"
           "&hourly=temperature_2m,precipitation_probability,wind_gusts_10m"
           "&daily=temperature_2m_max,temperature_2m_min"
           "&wind_speed_unit=mph&timezone=Europe%%2FLondon&forecast_days=1" % (lat, lon))
    try:
        d = json.loads(get(url))
        h = d["hourly"]
        day = [(int(t[11:13]), p or 0, g or 0) for t, p, g in
               zip(h["time"], h["precipitation_probability"], h["wind_gusts_10m"]) if 7 <= int(t[11:13]) <= 22]
        wet = [hr for hr, p, _ in day if p >= 50]
        peak = max(p for _, p, _ in day)
        hi, lo = d["daily"]["temperature_2m_max"][0], d["daily"]["temperature_2m_min"][0]
        feels = d["current"]["apparent_temperature"]
        return {
            "now_c": round(d["current"]["temperature_2m"]), "feels_c": round(feels),
            "high_c": round(hi), "low_c": round(lo), "peak_rain_chance": peak,
            "rain_likely_hours": wet, "max_gust_mph": round(max(g for _, _, g in day)),
            "umbrella": peak >= 40,
            "clothing": "warm coat" if feels < 6 else "jacket" if feels < 12 else "hoodie or light layer" if feels < 17 else "no extra layer",
        }
    except Exception as e:
        print(f"weather failed: {e}", file=sys.stderr)
        return None


def hours_phrase(hours):
    if not hours:
        return ""
    fmt = lambda h: f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"
    return f"most likely between {fmt(hours[0])} and {fmt(hours[-1] + 1)}"


def weather_sentence(w):
    if not w:
        return "I could not get weather data this morning."
    s = (f"It is {w['now_c']} degrees and feels like {w['feels_c']}. "
         f"Today's high is {w['high_c']} and the low is {w['low_c']}. ")
    if w["umbrella"]:
        s += f"Rain chance peaks at {w['peak_rain_chance']} percent, {hours_phrase(w['rain_likely_hours'])}, so take an umbrella. ".replace(", ,", ",")
    else:
        s += f"Rain is unlikely, peaking at {w['peak_rain_chance']} percent. "
    if w["max_gust_mph"] >= 35:
        s += f"It will be windy, with gusts up to {w['max_gust_mph']} miles per hour. "
    return s + f"Clothing: {w['clothing']}."


# ---------- model ----------
SYSTEM = """You write a spoken morning briefing for {name}, a UK-based MSc Data Science graduate aiming for junior AI engineering, automation engineering, data engineering and Python roles. He knows Python, SQL, FastAPI, pandas, scikit-learn, JavaScript and React, and has done real business automation, data enrichment and CRM workflow work.

HIS BACKGROUND (use it to personalise; never read it out as a list, and never invent experience that is not written here):
{profile}

The text is read aloud by a text-to-speech voice. Write for the ear: plain sentences, natural transitions, warm, efficient and professional. No markdown, bullets, headings, symbols, emoji, URLs or bracketed notes. Write numbers and abbreviations so they are spoken correctly. No filler, hype or motivational cliches. Separate sections with one blank line.

HARD RULES
- News: use ONLY the stories supplied. Never add facts, figures, names or dates that are not in the supplied title and summary. If a summary is thin, say what is known and stop. Name the source of each story.
- If the stories are from {window} rather than the last 24 hours, say so once.
{weather_rule}- Career: you have no live job-market data. Present career points as general guidance, never as current hiring evidence, and never mention specific vacancies.
- Total length 900 to 1100 words.

STRUCTURE
1. Opening: {opening}
{weather_step}3. Technology news: pick the 3 or 4 most significant stories for him. Skip repetitive or trivial ones. For each: what happened, why it matters, whether it looks like a real development or mostly marketing, and a verdict of learn, test or ignore. Then make it personal: name one specific piece of his background above that this connects to, the concrete skill he would gain by acting on it, and the advantage that gives him over other junior candidates. If a story has no honest link to his background, say so briefly and move on rather than forcing one.
4. Automation opportunity: one tool, integration or workflow worth investigating, ideally suggested by today's stories, tied to business process automation, CRM, data enrichment, reporting, document processing or API integration. Say what problem it solves, whether it is free to try and what skill it builds. Do not repeat: {opportunities}.
5. Learning moment: teach today's concept, "{concept}". Simple explanation first, then one concrete practical example, then how it shows up in real engineering work. Assume he already covered: {covered}.
6. Career: one specific portfolio, CV, GitHub or interview improvement that builds on a named part of his background, for example how to turn a piece of his placement work into a portfolio project or interview story. Framed as general guidance.
7. Mission: one task he can finish in 15 to 30 minutes today, measurable and linked to the learning moment or a story. Do not repeat: {missions}.

After the briefing, add two final lines exactly in this form, which will be removed before speaking:
OPPORTUNITY: <four to eight word label>
MISSION: <one sentence summary>"""


ERRORS = []


def load_profile():
    text = os.environ.get("JARVIS_PROFILE", "").strip()
    path = os.path.join(ROOT, "profile.txt")
    if not text and os.path.exists(path):
        text = open(path, encoding="utf-8").read().strip()
    return text[:6000] or "No further background supplied."


ENDPOINTS = [
    # (url, model-name transform)
    ("https://models.github.ai/inference/chat/completions", lambda m: m),
    ("https://models.inference.ai.azure.com/chat/completions", lambda m: m.split("/")[-1]),
]


def http_json(url, payload=None, timeout=120):
    """Request JSON and decode it; on failure, say exactly what came back."""
    headers = {"Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
               "Accept": "application/json", "X-GitHub-Api-Version": "2022-11-28", **UA}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status, ctype, enc = r.read(), r.status, r.headers.get("Content-Type", ""), r.headers.get("Content-Encoding", "")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url}: {e.read()[:300]!r}")
    if enc == "gzip" or raw[:2] == b"\x1f\x8b":
        import gzip
        raw = gzip.decompress(raw)
    try:
        return json.loads(raw)
    except ValueError:
        raise RuntimeError(f"non-JSON reply from {url}: status {status}, type {ctype!r}, "
                           f"{len(raw)} bytes, starts {raw[:200]!r}")


def catalog():
    try:
        return ", ".join(m.get("id", "?") for m in http_json("https://models.github.ai/catalog/models", timeout=30))
    except Exception as e:
        return f"catalog unavailable: {e}"


def call_model(cfg, system, user):
    if not os.environ.get("GITHUB_TOKEN"):
        raise RuntimeError("no GITHUB_TOKEN")
    for url, name in ENDPOINTS:
        for model in cfg["models"]:
            try:
                reply = http_json(url, {"model": name(model), "temperature": 0.5, "max_tokens": 2400,
                                        "messages": [{"role": "system", "content": system},
                                                     {"role": "user", "content": user}]})
                text = reply["choices"][0]["message"]["content"].strip()
                if len(text.split()) < 300:
                    raise RuntimeError(f"response too short ({len(text.split())} words)")
                print(f"model used: {name(model)} via {url}", file=sys.stderr)
                return text
            except Exception as e:
                ERRORS.append(f"{name(model)} via {url}: {e}")
                print(f"model failed: {ERRORS[-1]}", file=sys.stderr)
    raise RuntimeError(" || ".join(ERRORS))


def clean_for_speech(text):
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>|\[\]]", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def fallback(name, date_spoken, w, stories, window, phone_weather=False):
    parts = [(f"It's {date_spoken}. " if phone_weather else f"Good morning {name}. It's {date_spoken}. ") +
             "The full briefing could not be generated today, so here is the short version.",
             ] + ([] if phone_weather else [weather_sentence(w)])
    if stories:
        parts.append(f"Headlines from {window}. " + " ".join(
            f"From {s['source']}: {s['title']}." for s in stories[:6]))
    else:
        parts.append("I could not reach any news sources this morning.")
    parts.append("That's everything for now.")
    return "\n\n".join(parts)


def main():
    cfg, state = load("config.json"), load("state.json")
    now = datetime.now(timezone.utc)
    today = now.astimezone(LONDON)
    date_spoken = today.strftime("%A the ") + str(today.day) + today.strftime(" of %B")
    out = os.path.join(ROOT, "output")
    os.makedirs(out, exist_ok=True)

    # second scheduled run is only a retry
    try:
        if open(os.path.join(out, "date.txt")).read().strip() == today.strftime("%Y-%m-%d") \
                and open(os.path.join(out, "status.txt")).read().strip() == "full" \
                and not os.environ.get("JARVIS_FORCE"):
            print("today's full briefing already exists; nothing to do")
            return
    except FileNotFoundError:
        pass

    stories, window, failed = collect_news(cfg, state, now)
    w = weather_facts(now)
    idx = state["curriculum_index"] % len(cfg["curriculum"])
    concept = cfg["curriculum"][idx]
    phone_weather = not os.environ.get("JARVIS_LAT")  # no coordinates: the phone speaks live weather itself
    system = SYSTEM.format(
        profile=load_profile(),
        opening=f'"Now for your intelligence briefing. It\'s {date_spoken}."  Do not say good morning; the phone has already greeted him and given the weather.'
                if phone_weather else f'"Good morning {cfg["name"]}. It\'s {date_spoken}, and here\'s your personal intelligence briefing."',
        weather_rule="- Do not mention the weather at all. The phone has already reported it.\n" if phone_weather else
                     "- Weather: use only the supplied weather facts. If none are supplied, say weather was unavailable.\n",
        weather_step="2. No weather section. Go straight from the opening to the news.\n" if phone_weather else
                     "2. Weather: temperature and feels-like, high and low, rain chance and timing, wind if notable, umbrella and clothing advice.\n",
        name=cfg["name"], window=window, date_spoken=date_spoken, concept=concept,
        covered="; ".join(cfg["curriculum"][:idx][-8:]) or "nothing yet",
        opportunities="; ".join(state["opportunities"][-14:]) or "none yet",
        missions="; ".join(state["missions"][-14:]) or "none yet")
    user = (("" if phone_weather else "WEATHER FACTS:\n" + (json.dumps(w) if w else "unavailable") + "\n\n") +
            f"STORIES (from {window}):\n" + "\n".join(
                f"{n}. [{s['source']}, {s['date'].astimezone(LONDON):%a %d %b %H:%M}] {s['title']} :: {s['summary']}"
                for n, s in enumerate(stories, 1)))

    status, error_note = "full", "none"
    try:
        if len(stories) < 2:
            raise RuntimeError("too few stories to brief on")
        text = call_model(cfg, system, user)
        m = re.search(r"^MISSION:\s*(.+)$", text, re.M)
        o = re.search(r"^OPPORTUNITY:\s*(.+)$", text, re.M)
        text = re.sub(r"^(MISSION|OPPORTUNITY):.*$", "", text, flags=re.M)
        if m: state["missions"] = (state["missions"] + [m.group(1).strip()])[-30:]
        if o: state["opportunities"] = (state["opportunities"] + [o.group(1).strip()])[-30:]
        state["curriculum_index"] += 1
        state["seen"] = (state["seen"] + [s["link"] for s in stories])[-400:]
    except Exception as e:
        print(f"falling back to basic briefing: {e}", file=sys.stderr)
        status = "basic"
        error_note = f"{e}\n\nModels available: {catalog()}"
        text = fallback(cfg["name"], date_spoken, w, stories, window, phone_weather)

    def write(name, content):
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            f.write(content.strip() + "\n")

    write("briefing.txt", clean_for_speech(text))
    write("date.txt", today.strftime("%Y-%m-%d"))
    write("status.txt", status)
    write("error.txt", error_note)
    write("sources.md", f"# Sources for {today:%Y-%m-%d} ({status})\n\nLearning concept: {concept}\n\n" +
          "\n".join(f"- [{s['title']}]({s['link']}) ({s['source']}, {s['date']:%d %b %H:%M} UTC)" for s in stories) +
          (f"\n\nFeeds that failed: {', '.join(failed)}" if failed else ""))
    with open(os.path.join(ROOT, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    print(f"wrote {status} briefing, {len(text.split())} words, {len(stories)} stories")


if __name__ == "__main__":
    main()
