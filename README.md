# Project Jarvis

Builds a spoken morning briefing each day with GitHub Actions. An iPhone Shortcut reads `output/briefing.txt` aloud at 6:50.

- `briefing.py` fetches RSS feeds and the Open-Meteo forecast, asks a GitHub Models LLM to write the script, and falls back to weather plus headlines if that fails.
- `config.json` holds the feeds, model list and learning curriculum. Edit it freely.
- `state.json` is the memory: curriculum position, past missions, stories already covered.
- `output/sources.md` lists the links behind each day's stories.

Secrets needed: `JARVIS_LAT` and `JARVIS_LON` (your town's coordinates).
