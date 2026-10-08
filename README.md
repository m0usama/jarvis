# Project Jarvis

A spoken AI morning briefing for iPhone. GitHub Actions writes the script before dawn; an Apple Shortcut speaks live weather, then the briefing, at 6:50.

## How it works
1. `.github/workflows/briefing.yml` runs `briefing.py` up to five times between about 3am and 6:20am UK time. Once a full briefing exists for the day, later runs do nothing. Pressing Run workflow always rebuilds.
2. `briefing.py` reads the RSS feeds in `config.json`, removes duplicates and stories already covered, and asks a GitHub Models LLM (free, using the built-in token) to write a personalised briefing: news with a learn, test or ignore verdict, an automation opportunity, a learning moment from the curriculum, career advice and a 15 to 30 minute mission.
3. If the AI step fails, it writes a short headlines-only version instead and records the reason in `output/error.txt`.
4. The phone's shortcut gets live weather for its current location, has Apple Intelligence describe it, then reads `output/briefing.txt` aloud.

## Files
- `config.json`: feeds, model list and the 30-topic learning curriculum. Edit freely.
- `state.json`: the memory. Curriculum position, past missions and opportunities, stories already covered.
- `output/briefing.txt`: today's script. `date.txt`, `status.txt` (`full` or `basic`), `error.txt` and `sources.md` (links behind each story) sit beside it.

## Secrets
- `JARVIS_PROFILE` (recommended): your background and goals, used to personalise every section.
- `JARVIS_LAT` / `JARVIS_LON` (optional): only set these if you want the briefing itself to include weather for a fixed place. Leave them unset when the phone handles weather.
