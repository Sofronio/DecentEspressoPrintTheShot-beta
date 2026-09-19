# Changelog

[中文](CHANGELOG_zh.md) | English

## v2.0-beta.3

> Re-published. The first `v2.0-beta.3` build was withdrawn — it was cut before the
> version-comparison and self-update fixes landed, so it could never update itself.

### 🔴 macOS: the packaged app did not run at all

Every macOS download up to `v2.0-beta.2` was broken in four independent ways, and
none of them reported an error. All four are fixed.

- **The hand-assembled `.app` was never re-signed.** The PyInstaller binary carries a
  bundle-shaped signature of its own; copying it into a hand-made bundle left the two
  contradicting each other, so `codesign --verify` failed and macOS reported
  *"is damaged and can't be opened"*. Right-clicking did not help — that is a
  signature failure, not a developer-verification block. The build script now
  re-signs the bundle and **fails the build** if the signature does not verify.
- **`ditto -c -k` wrote extended attributes as AppleDouble sidecar files inside the
  `.app`.** `ditto` understands them; Keka and `unzip` do not, and left them behind as
  ordinary files, breaking the seal. Whether the app worked depended on which tool you
  unzipped with. Now packaged with `--sequesterRsrc`, which moves the sidecars outside
  the bundle.
- **Data directories were relative paths.** A `.app` launched from Finder has `/` as
  its working directory, which is read-only, so creating `shots_data/` threw
  `OSError: [Errno 30]` and the process exited — the user saw *"I double-clicked it
  and nothing happened"*. Packaged builds now use
  `~/Library/Application Support/PrintTheShot/`; source runs are unchanged.
- **Packaged builds had no working stdout** (`argv_emulation`), so the startup banner
  and every traceback went nowhere. Output is now also written to `server.log` in the
  data directory.

### 🍎 macOS now ships two architectures

The build was **arm64-only** — `macos-latest` on GitHub Actions is an Apple Silicon
runner and nothing said so, so anyone on an Intel Mac could not run it.

| File | For |
|---|---|
| `PrintTheShot-macos-arm64.zip` | Apple Silicon (M-series) |
| `PrintTheShot-macos-intel.zip` | Intel |

A CI step now verifies the architecture against the runner and checks the signature on
every build, so neither problem can return unnoticed.

### ✨ Stopping the app, and knowing it started

A packaged macOS app is a background service: no window, no Dock icon, no terminal.

- **The web UI opens by itself on start**, so there is something to indicate the app
  came up.
- **A red "Stop service" button** in the status card — macOS only, because that is the
  one configuration with no other way to stop it. Windows has a console window to
  close; Linux is normally run from a terminal. The endpoint answers before stopping
  so the confirmation actually arrives, and it is **not restricted by source IP**:
  anything on the LAN can reach it.
- **A log file** at `~/Library/Application Support/PrintTheShot/server.log`.
- The app declares itself a `LSUIElement`, which fixes the Dock icon bouncing forever.
  (A PyInstaller `console=True` binary never completes the macOS launch handshake,
  while its `CFBundlePackageType` says `APPL` — so the icon sat in "launching" state
  indefinitely.)

### 🐛 Self-update never worked

Two separate bugs, each sufficient on its own:

- **The version comparison only looked at `major.minor`.** `2.0-beta.2` and
  `2.0-beta.3` both parsed to `(2, 0)` and compared equal, so "check for updates"
  reported "already up to date". Prereleases now include their number, and a final
  release sorts above its own prereleases.
- **The update URLs pointed at the original repository**, which contains v1.6. The
  remote version read as `1.6` forever. Pointing them at this project's own
  repository is now a one-line change.

Neither caused damage only because `1.6` sorts below the current version, keeping the
update button disabled. Had that repository's version ever been bumped, this would
have downgraded users and overwritten the server source with v1.6.

### 📄 Docs

- The macOS allow-once step is now part of the download table, not a note underneath
  it, and explicitly distinguishes *"cannot verify the developer"* from *"is damaged"* —
  the latter cannot be bypassed, and `xattr` does not fix it.
- Added `CHANGELOG_zh.md`; the link at the top of this file previously pointed at
  itself.

---

## v2.0-beta.2 (2026-08-05)

### ✨ New Features

**1. DeepSeek AI translation**
- ⚙ Settings sub-page: API key input, enable toggle, **live balance display** (official DeepSeek balance endpoint)
- **Custom languages**: type any language name (日本語 / Japanese / 日语 all work) or pick from common presets; AI batch-translates all ~85 UI strings; the language switcher extends automatically
- Card **🌐 translate button**: language-picker dialog (custom languages auto-added) → AI translates bean info (origin/flavor/roast) + brew profile → **re-renders the whole chart** in the target language, then pops the large view
- **Lightbox language chips**: instant switching between cached language versions, no API cost
- Translations live in cache only, **statistics are unaffected**; 8s timeout falls back to the original text, printing is never blocked

**2. Language consistency**
- Records always keep the original language; card titles / stats are translated at the display layer (static map + AI cache)
- `chart_lang` tracks each chart's language; the grid re-renders in the current UI language in the background (cache-only, max 36 per page)
- No residual text from other languages under any UI language

**3. Print / chart quality**
- Right columns: **width-measured wrapping** (fixes full-width Japanese overflow), font size auto-fit by text length, Latin scripts get 2px smaller
- Bean info auto-cleans `·` and `-` separators
- AI prompts now ask for concise output (no more overlong French titles)

**4. UI polish**
- Card actions are icons now: 🖨️ 📄 🖼️ 🌐
- Lightbox action bar: print / JSON download / PNG download / translate / language switch
- Settings moved to a sub-page (⚙ top-right)
- Status card shows the AI balance

**5. Data & tooling**
- Test-data generator supports `zh|en` datasets (beans/profiles/roast in both languages)
- Month of simulated data (31 days × ~50 shots = 1670)

### 🐛 Fixes
- Language switch crashing the page (apostrophes in EN strings breaking the JS injection → `&#39;` escaping)
- AI prompt used the internal language code (`lang1`) instead of the display name — translations were wrong
- Missing `import re` causing NameError in translation
- Lightbox showing stale browser-cached images
- Web title version number missing ("v" with nothing after)
- Uploads no longer auto-call the AI API (saves tokens; translation only happens on demand)

---

## v2.0-beta.1 (2026-08-04)

Initial public beta: PIL-based chart rendering (no matplotlib), bundled CJK font, standalone bilingual web UI, history persistence, date filter + pagination, 3-column statistics, GitHub Actions 3-platform builds, service self-update, plugin TXT download.
