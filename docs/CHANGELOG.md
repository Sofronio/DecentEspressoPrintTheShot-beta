# Changelog

[中文](CHANGELOG.md) | English

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
