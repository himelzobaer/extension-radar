# CLAUDE.md: Extension Opportunity Radar

এই ফাইলটা প্রতিটা session এর শুরুতে পড়বে। এখানে project এর context, নিয়ম আর বর্তমান অবস্থা লেখা আছে।

---

## 1. Project কী এবং কেন

আমি একজন solo developer, strict zero-dollar infrastructure এ কাজ করি। আমার লক্ষ্য Chrome extension rebuild ও flip করে আয় করা। এই project টা একটা automated pipeline যেটা extension এর সুযোগ খুঁজে, পরিবর্তন ধরে, score করে Google Sheets এ জমা করে আর Telegram এ alert দেয়।

তিন ধরনের সুযোগ খুঁজি:

1. **Open Source Fork:** GitHub এ এমন extension repo যার star ভালো, কিন্তু maintainer চলে গেছে, license MIT বা Apache, আর code এখনো Manifest V2 তে আটকে আছে। Fork করে MV3 তে আপডেট করে publish করা হবে।
2. **MV2 Graveyard:** যেসব জনপ্রিয় extension Manifest V2 বন্ধ হওয়ার কারণে Chrome Web Store থেকে সরানো হইছে, কিন্তু user রা এখনো ওই function খুঁজতেছে। সাথে Firefox এ জনপ্রিয় কিন্তু Chrome এ নাই এমন add-on।
3. **Clean Alternative:** যেসব জনপ্রিয় extension বিক্রি হয়ে adware হয়ে গেছে। Signal: rating হঠাৎ পড়া, developer নাম বদল, নতুন permission।

এই tool শুধু সুযোগ খোঁজা আর data সংগ্রহের জন্য। কোনো extension এর code, নাম বা logo কপি করার কাজে এটা ব্যবহার হবে না।

---

## 2. Tech Stack (সব free tier, কোনো ব্যতিক্রম না)

- **Language:** Python 3.11+
- **Runner:** GitHub Actions, public repo (Actions free রাখার জন্য), weekly cron + manual trigger (workflow_dispatch)
- **Storage:** Google Sheets, service account দিয়ে gspread
- **Alert:** Google Apps Script time trigger, Telegram Bot API
- **Secrets:** শুধু GitHub Secrets আর লোকাল .env (gitignore করা)

কোনো paid service, paid API বা paid proxy যোগ করবে না। নতুন dependency যোগ করার আগে আমাকে জিজ্ঞেস করবে।

---

## 3. প্রস্তাবিত Repo Structure

শুরুর আগে এই structure যাচাই করে দেখাবে, দরকার হলে বদলের প্রস্তাব দিবে।

- CLAUDE.md
- README.md (step by step setup guide)
- requirements.txt
- .env.example
- config/queries.yaml (GitHub search query, threshold, category list)
- radar/github_module.py (Module A)
- radar/firefox_module.py (Module B)
- radar/chrome_seeder.py (Module C)
- radar/chrome_snapshot.py (Module D)
- radar/diff_engine.py (Module E)
- radar/scoring.py
- radar/sheets.py (Sheets read/write helper)
- radar/utils.py (rate limit, retry, logging)
- scripts/setup_sheet.py (tab আর header auto তৈরি)
- apps_script/telegram_alert.gs
- .github/workflows/radar.yml
- tests/

---

## 4. Modules

### Phase 1 (আগে এটা, Phase 1 শেষ না করে Phase 2 তে যাবে না)

**Module A: GitHub Module**
- GitHub official REST search API ব্যবহার করবে, GITHUB_TOKEN দিয়ে
- Query গুলা config/queries.yaml থেকে পড়বে। শুরুর query:
  - topic:chrome-extension stars:>300 pushed:<2024-10-01 license:mit
  - topic:chrome-extension stars:>300 pushed:<2024-10-01 license:apache-2.0
  - topic:browser-extension stars:>300 archived:true
- প্রতিটা repo থেকে নিবে: name, url, stars, forks, last_push, license, archived, open_issues, description
- manifest.json পড়ে manifest_version বের করবে
- README তে Chrome Web Store link থাকলে সেটা রাখবে
- Search API rate limit মেনে চলবে

**Module B: Firefox Module**
- Firefox Add-ons official public API (AMO API v5) ব্যবহার করবে
- User সংখ্যা দিয়ে sort করে জনপ্রিয় add-on আনবে
- নিবে: name, slug, users, rating, rating_count, last_updated, category, description
- নাম দিয়ে Chrome Web Store এ search করবে। মিল না পেলে Firefox_Only flag

### Phase 2 (Phase 1 test run সফল হওয়ার পর)

**Module C: Chrome Seeder**
- Chrome Web Store এর category ও search page থেকে extension ID সংগ্রহ করে baseline বানাবে
- একবার চালানোর জন্য, পরে মাঝে মাঝে নতুন ID যোগ করবে

**Module D: Weekly Snapshot**
- প্রতিটা ID এর store page থেকে নিবে: name, users, rating, rating_count, last_updated, version, developer
- Snapshots tab এ তারিখসহ append
- Page না পাইলে status: removed

**Module E: Diff Engine**
আগের snapshot এর সাথে তুলনা করে flag দিবে:
- **REMOVED:** আগে ছিল, এখন page নাই
- **RATING_DROP:** rating threshold এর বেশি পড়ছে (config এ, শুরুতে 0.5)
- **DEV_CHANGE:** developer name বদলাইছে
- **STALE:** last_updated 12 থেকে 24 মাসের মধ্যে এবং users 10,000 এর বেশি

---

## 5. Chrome Scraping এর কঠোর নিয়ম

- Chrome Web Store এর official API নাই, তাই page scraping। প্রতিটা request এর মাঝে random delay রাখবে
- সপ্তাহে একবারের বেশি চালাবে না
- Parser defensive হবে: HTML বদলালে পুরা pipeline crash করবে না, ওই row এ error log করে বাকিগুলা চালাবে
- Retry এ exponential backoff, সর্বোচ্চ retry সংখ্যা config এ

---

## 6. Google Sheet Structure

| Tab | কী থাকবে |
|---|---|
| Master | প্রতিটা item এর latest তথ্য, source column সহ (github, firefox, chrome) |
| Snapshots | Chrome এর সপ্তাহভিত্তিক history |
| GitHub_Repos | Module A এর output |
| Firefox_Only | Chrome এ নাই এমন Firefox add-on |
| Alerts | flag পাওয়া item: flag_type, date, link, sent status |
| Scored | auto score + manual চেকের কলাম |
| Logs | প্রতিটা run এর সময়, processed সংখ্যা, error |

scripts/setup_sheet.py সব tab আর header auto তৈরি করবে। Duplicate row এড়াতে প্রতিটা item এর unique key রাখবে (GitHub এর জন্য repo full name, Chrome এর জন্য extension ID, Firefox এর জন্য slug)।

---

## 7. Scoring (Scored tab)

**Auto চেক (script করবে):**
- আগের users 10K+ ছিল কি না
- STALE, REMOVED বা RATING_DROP এর মধ্যে পড়ে কি না
- License fork করার অনুমতি দেয় কি না। GPL হলে note: নিজের version ও open source রাখতে হবে

**Manual কলাম (খালি রাখবে, আমি পূরণ করবো):**
- এক সপ্তাহে বানানো যায়?
- Heavy backend বা paid API লাগে না?
- Chrome নিজে এই feature built-in করে নাই?
- Keyword এ এখনো search আছে?

---

## 8. Telegram Alert (apps_script/telegram_alert.gs)

- Pipeline শেষ হওয়ার পর Alerts tab এর নতুন row পড়বে
- Summary পাঠাবে: কোন flag এ কয়টা নতুন item, top 5 item এর name, users আর link
- পাঠানোর পর sent status আপডেট করবে, যাতে একই item বারবার না আসে
- Bot token আর chat ID Script Properties এ রাখবে, code এ না

---

## 9. Coding Conventions

- প্রতিটা module আলাদাভাবে চালানো যাবে (python -m radar.github_module) এবং একসাথেও
- সব threshold, query আর limit config এ, hardcode না
- Type hint আর ছোট docstring রাখবে
- Logging ব্যবহার করবে, print না
- Secret কখনো log বা output এ যাবে না
- প্রতিটা module এর জন্য অন্তত একটা basic test, যেখানে network call mock করা থাকবে

---

## 10. কাজের ধরন

- নতুন কোনো module এর কোড লেখার আগে ছোট plan দেখাবে, আমি approve করলে শুরু করবে
- কোনো জায়গায় assumption করতে হলে আগে জিজ্ঞেস করবে
- Phase 1 শেষে test run চালায়ে দেখাবে Sheet এ data ঠিকমতো আসছে
- প্রতিটা বড় কাজ শেষে নিচের Status section আপডেট করবে
- আমার সাথে বাংলায় কথা বলবে, technical term আর file name ইংরেজিতে রাখবে

---

## 11. Deliverables Checklist

- [x] Repo structure
- [x] config/queries.yaml
- [x] scripts/setup_sheet.py
- [x] Module A: GitHub
- [x] Module B: Firefox
- [x] Scoring
- [x] GitHub Actions workflow
- [x] Apps Script Telegram alert
- [x] README setup guide (service account বানানো, Sheet share করা, GitHub Secrets যোগ করা, Telegram bot token বসানো)
- [x] Phase 1 test run (data Sheet এ আসছে; Firefox matching fix এর পর re-run বাকি)
- [ ] Module C: Chrome Seeder
- [ ] Module D: Weekly Snapshot
- [ ] Module E: Diff Engine

---

## 12. Current Status

Phase: 1 সম্পন্ন (2026-10-24)। পরের কাজ: Telegram script install, GitHub repo push + Secrets, তারপর Phase 2 plan।
তৃতীয় run 2026-10-24 (কড়া matching, --force): FIREFOX_ONLY 139, Alerts 190 (FIREFOX_ONLY 139 + MV2_FORK 51), error 0। আজ CWS ৩ বার scrape হইছে, এই সপ্তাহে আর --force না।
নাম দিয়ে matching এর সীমা (heuristic আর বাড়াবো না):
- uBlock Origin miss: Chrome এ "uBlock Origin: Facebook, YouTube & Sites" নামে একটা copycat আছে, নাম হুবহু মিলে যায়
- কড়া নিয়মে কিছু false positive: NoScript Security Suite, Trusted Shops, Copyfish, LanguageTool
- Phase 2 আইডিয়া: Firefox author আর Chrome developer নাম মিলিয়ে copycat/ভুল match ধরা (Module D detail page থেকে developer পাওয়া যাবে)
Re-run 2026-10-24 (পুরানো FIREFOX_ONLY row মুছে, --force): FIREFOX_ONLY 188 → 101। কিন্তু containment নিয়মের কারণে uBlock Origin ভুলভাবে "uBlock" এর সাথে match হইছে। দ্বিতীয় fix: বাড়তি শব্দ অন্য নামে থাকতে হবে বা generic_words এ থাকতে হবে (config)। Sheet এ এখনো প্রথম fix এর data।
Test run 2026-10-24 (gh account himelzobaer এর token দিয়ে): GitHub 103 repo (MV2 52, MV3 28, manifest পাওয়া যায়নি 23), Firefox 500 add-on, FIREFOX_ONLY 188, Alerts 239, Scored 603, error 0।
সমস্যা পাওয়া গেছে: 188 FIREFOX_ONLY এর অনেকগুলা false positive (SponsorBlock, 1Password, Keepa...)। কারণ: লম্বা নামে CWS search 0 result দেয়, আর ratio-only matching। Fix: মূল নাম দিয়ে search, token containment match, "Lite/MV3" variant guard (21 test pass)। Sheet এর পুরানো FIREFOX_ONLY row গুলা এখনো পুরানো logic এর।
শেষ কাজ (2026-10-24): Phase 1 এর সব code, 19টা mocked test (সব pass), আর real GitHub/AMO/CWS API দিয়ে in-memory sheet এ live smoke test (ঠিকমতো চলছে)।
Structure এ যা যোগ হইছে: radar/main.py (পুরা pipeline), radar/chrome_search.py (CWS search parser, Phase 2 তেও কাজে লাগবে), .gitignore।
Phase 1 alert flag: MV2_FORK (GitHub, MV2 + fork-friendly license), FIREFOX_ONLY (≥10K user)।
পরের কাজ: user service account + SHEET_ID দিবে → setup_sheet.py → real Sheet এ Phase 1 test run → তারপর Phase 2 plan।
Known issues:
- লোকাল Python 3.10, CI তে 3.11। Code দুইটাতেই চলে।
- FIREFOX_ONLY নামের মিল দিয়ে ধরা হয়, তাই কিছু false positive আসে (যেমন "AdBlocker for Firefox")। Manual যাচাই করতে হবে।
- CWS এ কোনো official API নাই। Canary query ("dark reader") থেকে result না আসলে সব item "unknown" হয়ে যায়, crash করে না।
