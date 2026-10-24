# Extension Opportunity Radar

Chrome extension rebuild/flip এর সুযোগ খোঁজার automated pipeline। পুরা stack free tier এ চলে: GitHub Actions, Google Sheets, Apps Script আর Telegram।

**Phase 1:**
- **Module A, GitHub:** abandoned আর MV2 extension repo খোঁজে (`radar/github_module.py`)
- **Module B, Firefox:** জনপ্রিয় Firefox add-on গুলোর মধ্যে কোনগুলা Chrome Web Store এ নাই সেটা বের করে (`radar/firefox_module.py`)
- **Scoring:** auto চেক করে, আর manual কলাম খালি রাখে (`radar/scoring.py`)
- **Telegram alert:** নতুন alert এর summary পাঠায় (`apps_script/telegram_alert.gs`)

**Phase 2:**
- **Module C, Chrome Seeder:** category page, keyword search, GitHub README আর Firefox match থেকে extension ID জোগাড় করে Chrome_IDs tab বানায় (`radar/chrome_seeder.py`)
- **Module D, Weekly Snapshot:** প্রতিটা ID এর store page থেকে users, rating, version, developer নিয়ে Snapshots tab এ রাখে। প্রতিটা page এর related extension থেকে নতুন ID যোগ করে, `chrome.max_ids` পর্যন্ত (`radar/chrome_snapshot.py`)
- **Module E, Diff Engine:** আগের সপ্তাহের snapshot এর সাথে তুলনা করে REMOVED, RATING_DROP, DEV_CHANGE আর STALE flag দেয় (`radar/diff_engine.py`)

---

## 1. Local setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
python -m pytest -q
```

## 2. Google service account বানানো

1. https://console.cloud.google.com এ যান, তারপর নতুন project বানান (free)।
2. **APIs & Services → Library** এ গিয়ে **Google Sheets API** আর **Google Drive API** দুইটাই Enable করুন।
3. **APIs & Services → Credentials → Create credentials → Service account** এ যান। একটা নাম দিন, role লাগবে না।
4. Service account খুলুন, তারপর **Keys → Add key → Create new key → JSON**। এতে একটা ফাইল download হবে।
5. ফাইলটা project folder এ `service_account.json` নামে রাখুন (এটা gitignore করা আছে)। `.env` এ `GOOGLE_SERVICE_ACCOUNT_FILE=./service_account.json` বসান।

## 3. Google Sheet বানানো ও share করা

1. https://sheets.new দিয়ে একটা খালি Sheet বানান।
2. URL থেকে ID কপি করুন: `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`। এরপর `.env` এ `SHEET_ID=` বসান।
3. **Share** এ ক্লিক করে service account এর email (`...@...iam.gserviceaccount.com`, JSON এর `client_email`) **Editor** হিসেবে যোগ করুন।
4. Tab আর header বানান:

```bash
python scripts/setup_sheet.py
```

Tab গুলা: Master, Snapshots, GitHub_Repos, Firefox_Only, Alerts, Scored, Logs। Script টা আবার চালালেও কোনো data মুছবে না।

## 4. GitHub token (local run এর জন্য)

https://github.com/settings/tokens এ যান, তারপর **Fine-grained token** বানান। "Public repositories (read-only)" যথেষ্ট। এরপর `.env` এ `GITHUB_TOKEN=` বসান।
Token না দিলেও চলবে, তবে search অনেক ধীর হবে (10 req/min)।

## 5. Local এ চালানো

```bash
python -m radar.github_module      # শুধু Module A
python -m radar.firefox_module     # শুধু Module B (সপ্তাহে একবার; --force দিলে guard ignore করবে)
python -m radar.scoring            # শুধু scoring
python -m radar.chrome_seeder      # Module C: একবার চালান (পরে মাঝে মাঝে)
python -m radar.chrome_snapshot    # Module D (সপ্তাহে একবার; --force, --limit 20 test এর জন্য)
python -m radar.diff_engine        # Module E
python -m radar.main               # সব একসাথে (seed বাদে)
python -m radar.main --only github,scoring
```

প্রতিটা run এর সময়, কয়টা item process হইছে আর কয়টা error হইছে, সব **Logs** tab এ যায়।

## 6. GitHub Actions (weekly auto run)

1. Repo টা **public** রাখুন, তাহলে Actions free থাকবে।
2. **Settings → Secrets and variables → Actions → New repository secret** এ গিয়ে এগুলা যোগ করুন:
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: service account JSON ফাইলের **পুরা content**
   - `SHEET_ID`: Sheet এর ID
   - `GITHUB_TOKEN` যোগ করতে হবে না, Actions নিজেই দেয়।
3. Workflow (`.github/workflows/radar.yml`) প্রতি সোমবার 03:00 UTC তে চলে। হাতে চালাতে চাইলে: **Actions → Extension Radar → Run workflow**।

## 7. Telegram alert

1. Telegram এ **@BotFather** কে `/newbot` পাঠান। Bot এর **token** কপি করুন।
2. নতুন bot কে যেকোনো একটা message পাঠান। তারপর browser এ `https://api.telegram.org/bot<TOKEN>/getUpdates` খুলুন। সেখানে `"chat":{"id":...}` থেকে **chat ID** নিন।
3. Sheet খুলে **Extensions → Apps Script** এ যান। `apps_script/telegram_alert.gs` এর পুরা code paste করে save করুন।
4. **Project Settings (⚙) → Script Properties** এ যোগ করুন:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Editor এ `sendTestMessage` select করে **Run** দিন। প্রথমবার permission চাইবে, allow করুন।
6. এরপর `installTrigger` একবার Run করুন। এটা প্রতি ঘণ্টায় Alerts tab চেক করবে, নতুন row পেলে summary পাঠাবে, তারপর `sent = TRUE` করে দিবে।

---

## Config

সব query, limit, delay আর threshold থাকে `config/queries.yaml` এ। গুরুত্বপূর্ণ কয়েকটা:

| Key | কাজ |
|---|---|
| `github.queries` | GitHub search query |
| `firefox.max_addons` | কয়টা top add-on চেক করবে (default 500) |
| `firefox.min_days_between_runs` | Chrome scrape guard (default 6 দিন) |
| `chrome_search.match_threshold` | নাম কতটা মিললে "Chrome এ আছে" ধরবে |
| `chrome_search.delay_*_seconds` | Chrome request এর মাঝে random delay |
| `http.max_retries` | exponential backoff সহ সর্বোচ্চ retry |
| `alerts.firefox_only_min_users` | এর কম user হলে alert হবে না |

## Alert flags (Phase 1)

- `MV2_FORK`: repo টা এখনো Manifest V2 তে, আর license fork করার অনুমতি দেয় (MIT/Apache/GPL…)
- `FIREFOX_ONLY`: Firefox এ জনপ্রিয় (≥10K user) কিন্তু Chrome এ নামের মিল পাওয়া যায়নি

## Alert flags (Phase 2, Chrome)

- `REMOVED`: আগের সপ্তাহে store এ ছিল, এখন নাই (page `empty-title` এ redirect হয়)
- `RATING_DROP`: rating `diff.rating_drop_threshold` (0.5) এর বেশি কমছে
- `DEV_CHANGE`: developer এর নাম বা publisher ID বদলাইছে। এটা সাধারণত extension বিক্রি হওয়ার signal
- `STALE`: 12-24 মাস ধরে update হয়নি, আর users 10K এর বেশি

প্রথম snapshot এর পর শুধু STALE আসবে, কারণ বাকি তিনটা flag এর জন্য দুই সপ্তাহের snapshot লাগে।

`FIREFOX_ONLY` নামের মিল দিয়ে ধরা হয়, তাই কিছু false positive আসতে পারে। `chrome_match` কলামে সবচেয়ে কাছের Chrome result দেখা যায়, সেটা দেখে manual যাচাই করবেন।
