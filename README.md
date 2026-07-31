# scalping

Bot scalpers for limited drops at online retailers, built with [Botasaurus](https://github.com/omkarcloud/botasaurus).

## Setup

```bash
uv sync
```

Requires Python 3.14+ (managed by uv via `.python-version`).

## Session capture

Capture a logged-in browser session once, then reuse it in scrapers via the same Botasaurus profile.

```bash
uv run python "session capture/target.py"
```

Log in to Target in the opened browser, then press Enter in the terminal. Cookies are written to `session capture/target_cookies.json` and the `target` tiny profile is persisted under `./profiles/`.
