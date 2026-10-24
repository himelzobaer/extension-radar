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
- [x] Phase 1 test run
- [x] Module C: Chrome Seeder
- [x] Module D: Weekly Snapshot
- [x] Module E: Diff Engine

---

## 12. Current Status

Phase: 2 সম্পন্ন (2026-10-24)। প্রথম আসল snapshot user এর অনুরোধে 2026-10-24 এ local থেকে চালানো হইছে (978 ID: ok 931, removed 47, error 0; STALE alert 59; related থেকে +300 ID, Chrome_IDs এখন 1278)। তাই সোমবার 2026-10-26 এর Actions run এ snapshot guard এর কারণে skip হবে। পরের snapshot 2026-11-02, তখন প্রথম REMOVED/RATING_DROP/DEV_CHANGE আসবে।
প্রথম analysis: 47টা removed এর সবগুলাই GitHub README link থেকে আসা (MV2 graveyard)। README PRO version এ নতুন করে লেখা হইছে (schema, flags, ER diagram)।
Repo: github.com/himelzobaer/extension-radar (public)। Secrets: GOOGLE_SERVICE_ACCOUNT_JSON, SHEET_ID। Telegram: Script Properties এ বসানো, hourly trigger।

শেষ কাজ (2026-10-24):
- Phase 2: chrome_seeder.py, chrome_snapshot.py, diff_engine.py। মোট 27টা mocked test pass।
- Seeder real Sheet এ চালানো হইছে: Chrome_IDs এ 978টা ID।
- Snapshot + diff শুধু in-memory sheet এ test হইছে (20টা আসল ID: 7 ok, 13 removed, হাতে যাচাই করে দেখা গেছে সত্যিই removed)। Real Sheet এ snapshot চালানো হয়নি, যাতে 6 দিনের guard সোমবারের run আটকে না দেয়।
- Structure এ যোগ: Chrome_IDs tab; Master এ chrome_id কলাম; Snapshots এ developer_id, error কলাম; Alerts এ detail কলাম।

Phase 1 এর ফলাফল (2026-10-24): GitHub 103 repo (MV2 52), Firefox 500 add-on, FIREFOX_ONLY 139, MV2_FORK 51। আজ CWS search ৩ বার scrape হইছে, তাই সোমবারের firefox step guard এর কারণে skip হবে (এটা স্বাভাবিক)।

পরের কাজ:
- সোমবারের run এর পর Snapshots/Alerts/Logs যাচাই করা (snapshot এ 978 ID, প্রায় 75 মিনিট)
- 2 সপ্তাহ পর প্রথম REMOVED/RATING_DROP/DEV_CHANGE flag যাচাই করা
- Firefox author বনাম Chrome developer মিলিয়ে copycat match ধরা (uBlock Origin case)

Known issues:
- লোকাল Python 3.10, CI তে 3.11। Code দুইটাতেই চলে।
- FIREFOX_ONLY নামের মিল দিয়ে ধরা হয়, তাই কিছু ভুল থাকে। uBlock Origin miss হয় কারণ Chrome এ একই নামের copycat আছে। NoScript, Trusted Shops, Copyfish, LanguageTool false positive। Heuristic আর বাড়ানো হবে না।
- CWS এ official API নাই। Search আর detail দুইটাতেই canary আছে (Dark Reader), parse fail করলে run থামে বা item "unknown" হয়।
- অনেক Chrome listing এ publisher link নাই, তাই Snapshots এর developer খালি থাকে (যেমন EndNote Click, PDF Viewer)। ওইগুলাতে DEV_CHANGE ধরা যাবে না, তবে ভুল flag ও হবে না। Parser এ developer এর বিকল্প উৎস খোঁজা বাকি।
- Related ID থেকে প্রতি run এ সর্বোচ্চ 300টা নতুন ID যোগ হয়, তাই 2000 এ পৌঁছাতে প্রায় 4 সপ্তাহ লাগবে।
- Snapshots tab প্রতি সপ্তাহে বড় হয়। Diff পুরা tab পড়ে, তাই কয়েক মাস পর পুরানো row archive করতে হবে।
