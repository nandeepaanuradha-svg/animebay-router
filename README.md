# AnimeBay Router

A Python Telegram **userbot** that watches your encoded-episode channel and copies each anime to its configured channel, preserving the original file captions and formatting without a “Forwarded from” header.

**Start here:** follow sections 1–3 to install and configure it, then section 4 to keep it running with **PM2**. The [PM2 guide](docs/PM2.md) explains settings, updates, logs, restarts and troubleshooting.

For every episode, it publishes:

1. `📺 One Piece 📺` followed by `📗 Episode 02 📗` in the same heading message.
2. The 720p file as soon as it arrives.
3. The 1080p file when it arrives.
4. The sticker you saved for that anime route.
5. The expected next **SubsPlease** release date and time in **Sri Lankan time**.

It copies existing Telegram messages without downloading or re-uploading the video files. It does not encode or download anime.

## What is included

- A persistent SQLite queue, upload-pair tracking, input cursors and send progress.
- Exact title aliases that keep seasons separate. No fuzzy guessing of destination channels.
- Separate per-channel stickers, including Telegram animated/video stickers.
- An early 1080p file waits until its matching 720p file arrives.
- One unfinished episode at a time per destination; other channels keep working.
- Duplicate source messages and repeated uploads of the same episode/quality are skipped.
- Telegram flood waits and failed sends stay pending and retry.
- Live source events plus polling to collect posts missed during disconnects or restarts.
- Saved Messages commands, schedule previews, manual date overrides, completed-season notices and optional historical rescans.
- VPS deployment with PM2, plus alternative systemd and Docker Compose setups.

## About SubsPlease release times

SubsPlease exposes a **weekly release timetable**, not a guaranteed, episode-specific future timestamp or a confirmed hiatus calendar. The userbot reads `https://subsplease.org/api/?f=schedule&tz=Asia%2FColombo`, respects the timezone returned by the provider, and calculates the next weekly slot. It explicitly labels this date as **expected**. It never substitutes Japanese TV broadcast times.

The release-information message is mandatory after the sticker. If SubsPlease is unavailable or your show is absent, the userbot posts an honest “schedule unavailable / not listed” message and retries. It refreshes the latest completed episode’s message about every 15 minutes, instead of adding repeated notices. Recently cached data can be used for up to two hours with a visible cache warning. An unresolved notice is retried for up to 14 days; `.refresh <key>` can request another check.

The userbot cannot promise that SubsPlease will release on time or recognize every break. Use `.nextat` for a known delayed date, and `.finished` when a season ends. Weekly estimates stay attached to the original posting date; they do not silently roll forward each week.

## 1. Install on an Ubuntu/Debian VPS

Python **3.11 or newer** is required. These commands use `/opt/animebay-router` and assume you are logged in as root:

```bash
apt update
apt install -y git python3 python3-venv nano
python3 --version
mkdir -p /opt
git clone https://github.com/nandeepaanuradha-svg/animebay-router.git /opt/animebay-router
cd /opt/animebay-router
```

If the repository is **private**, use your authenticated GitHub CLI instead of the `git clone` command:

```bash
gh repo clone nandeepaanuradha-svg/animebay-router /opt/animebay-router
cd /opt/animebay-router
```

If `gh` is unavailable, install it with `apt install -y gh`, then run `gh auth login` to sign into GitHub before cloning. An existing authenticated GitHub CLI session can be reused. If you downloaded the ZIP instead, extract it so the project is at `/opt/animebay-router`, then enter that folder.

Create an isolated Python environment and install the bot:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
nano .env
```

Get your own `API_ID` and `API_HASH` from [Telegram API development tools](https://my.telegram.org). Fill them in `.env`. Leave the other settings at their defaults for Sri Lanka.

```dotenv
API_ID=YOUR_NUMERIC_API_ID
API_HASH=YOUR_32_CHARACTER_API_HASH
TIMEZONE=Asia/Colombo
DATA_DIR=data
SOURCE_POLL_SECONDS=15
```

These are placeholders; no account credentials are included in this project. A BotFather token is not needed.

`.env` is your private settings file. `.venv` is the folder containing this project's Python packages. You do not need to activate `.venv` when using the commands in this guide; they select its Python directly.

## 2. Log in to your Telegram account

```bash
cd /opt/animebay-router
.venv/bin/python -m anime_router --login
```

Enter your phone number, Telegram login code and two-step verification password if prompted, directly in your VPS terminal. The account must be able to read the source and post to every destination channel. Join private channels with this account in Telegram first.

The login session is saved in `data/account.session`. Keep the whole `data` directory private: it contains an active account session as well as channel mappings and queue history. Do not share it, commit it to GitHub or run two processes against it.

Start the userbot in the foreground for initial setup:

```bash
.venv/bin/python -m anime_router
```

## 3. Configure your first anime

All commands below are sent by **you** in your Telegram **Saved Messages**. They are ignored in every other chat. Example channel IDs are placeholders; replace them with your own.

1. Find your channels:

   ```text
   .chats Encodes
   .chats One Piece
   ```

2. Add the anime destination. This creates a **paused** route with the short key `op`:

   ```text
   .add op -1001234567890 One Piece
   ```

3. The text before ` - <episode>` in the source filename must match a registered title alias. The display title added above is already an alias. Add any additional filename titles as needed:

   ```text
   .alias op One Piece S1
   ```

4. Find the SubsPlease show and store the slug returned by the search:

   ```text
   .spsearch One Piece
   .schedule op one-piece
   .next op
   ```

   The `one-piece` slug is an example of the format. Use the exact show/season entry you intend. You can also pass a SubsPlease `/shows/.../` URL to `.schedule`. A show absent from the current timetable produces a visible “not listed” notice.

5. Send or forward your desired sticker to Saved Messages. **Reply to that sticker** with:

   ```text
   .setsticker op
   ```

   Keep the sticker message in Saved Messages so the userbot can reuse it.

6. Check the configuration and enable this route:

   ```text
   .show op
   .resume op
   ```

7. Add your encoded-episode source:

   ```text
   .source -1009876543210
   ```

   It starts with **new** source posts after this command. The same source can contain many different anime. Repeat steps 2–6 for each destination. Additional sources are supported.

If you register the source before all routes are configured, recognized but unmapped filenames remain in `.unmatched`. Adding the matching title/alias queues them automatically; paused routes remain paused.

## Your screenshot’s season/title example

Your display heading can omit `S2` while the exact filename alias retains it:

```text
.add ice -1001111222233 The Ramparts Of Ice
.alias ice The Ramparts Of Ice S2
.spsearch Koori
```

Then set the correct SubsPlease slug returned by the search, save `ice`’s sticker, and `.resume ice`. Separate remakes and seasons should have distinct route keys such as `ice_s2`; they may point to the same destination channel if desired. Do not give two routes the same alias.

## Filenames

Supported examples:

```text
The Ramparts Of Ice S2 - 02 [AnimebaySL][720p x265].mkv
The Ramparts Of Ice S2 - 02 [AnimebaySL][1080p x265].mkv
One Piece - 1123 [SLAnimebay][720p x265].mkv
[SubsPlease] 86 - Eighty Six - 03 (1080p) [12345678].mkv
Anime S02E05 [720p].mkv
Anime - 12.5 [1080p].mkv
```

`S02E05` parses as title `Anime S2`, episode `05`. Seasons, parts and title numbers are retained. Matching ignores punctuation, spaces and letter case. Decimal episode numbers and revision suffixes such as `02v2` are recognized; a revision does **not** overwrite an already queued/sent episode/quality. The first matching upload wins.

Episode ranges, batches, archives, files without a supported quality, and ambiguous filenames are ignored. If a Telegram document has no filename, a complete video filename on a caption line can be used instead. The actual file caption is copied unchanged.

For a no-post check, forward a sample video file to Saved Messages and reply with `.inspect`. If parsing succeeds but routing does not, use the displayed title in `.alias`.

## Missing files and ordering

720p is published immediately after its heading. If 1080p is missing, the episode stays pending across restarts; the userbot does not send the closing sticker or schedule early. If 1080p arrives first, it waits without posting a heading. There is no automatic quality timeout.

Later episodes for the **same destination** wait for the unfinished episode, so separate episode sequences do not become interleaved. `.pending` shows the wait. Correct the missing upload in the source to continue. Episodes are processed in the order they are first queued, not sorted by episode number.

The queue records each stage before sending and saves its Telegram `random_id`. A retry reuses that ID, including after a crash, to reduce duplicates in the send/commit window. This relies on Telegram’s deduplication behavior; it is not a permanent exactly-once guarantee after arbitrary server-side retention periods. Do not delete the database and expect the bot to remember prior sends.

## Historical episodes

To route the newest 100 existing source posts, oldest first:

```text
.rescan -1009876543210 100
```

This **queues real posts** to matching enabled destinations. Limit: 5000 source messages per command. A repeated scan skips recorded episode/quality pairs. `.pause` can hold delivery while you inspect `.pending`; `.resume` clears the global pause. If a manual rescan is interrupted, rerun it; completed queue entries are retained.

Each historical episode gets the full heading/files/sticker/release-information sequence. The next release is calculated when that episode finishes publishing, so a large backlog may produce repeated weekly estimates.

## Delays, completed seasons and recovery

Use real dates you have verified; this example is only command syntax:

```text
.nextat op 2026-10-25 19:00
.next op
.nextat op off
.finished op on
.finished op off
.retry op
.refresh op
```

`.nextat` interprets dates without an offset in `TIMEZONE` (Sri Lanka by default). A manual override applies to the next release, not a recurring weekly rule. `.finished on` replaces the next-release notice with a season/series-completed notice. Keep separate seasons in separate routes to avoid episode-number collisions.

Saved Messages receives a notice when a send repeatedly fails, such as when posting permission was removed or the saved sticker was deleted. Fix the issue and use `.retry <key>`. `.refresh <key>` requests an update of the most recent completed episode’s schedule message. Telegram’s required waits are always respected.

Commands are listed in `.help`. Additional controls include `.routes`, `.sources`, `.unmatched`, `.status`, `.pause <key>`, `.resume <key>`, `.unalias <key> <title>` and `.rmsource <numeric ID>`.

## 4. Keep it running with PM2

PM2 keeps the bot running after you close SSH, restarts it after a crash, and can restore it after a VPS reboot. The bot stays a Python program; Node.js is only needed for PM2.

After the foreground check above, press **Ctrl+C**. Use **one** process manager for this bot. If you previously used the included systemd service, stop and disable that service before switching to PM2; if you used Docker Compose, stop that container first. Other bots can keep running under their existing managers.

Install Node.js/npm if needed, then PM2:

```bash
apt install -y nodejs npm
node --version
npm install -g pm2@latest
```

Use a Node.js version supported by your installed PM2 release; a supported Node.js LTS release is preferable. If your distribution provides an old Node.js version, use the [official Node.js installation instructions](https://nodejs.org/en/download) before installing PM2.

Start the included configuration:

```bash
cd /opt/animebay-router
pm2 start ecosystem.config.js
pm2 status animebay-router
pm2 logs animebay-router --lines 50
```

Press **Ctrl+C** to leave the log viewer; the bot keeps running. Send `.status` in Telegram Saved Messages to check that it is responding.

Enable automatic recovery after reboot:

```bash
pm2 startup
```

If PM2 prints an additional command, run that exact command. Then save the process list:

```bash
pm2 save
```

Use the same Linux user for PM2 start, startup, save and future maintenance. The included ecosystem file chooses the correct `.venv` Python and project directory automatically, runs one instance, and leaves credentials in `.env`.

Useful commands:

```bash
pm2 restart animebay-router
pm2 stop animebay-router
pm2 logs animebay-router --lines 100
```

After editing `.env`, restart the bot so it reads the new settings. See [the full PM2 guide](docs/PM2.md) for updates, private configuration, switching managers and troubleshooting.

## Alternative: run with systemd

Use this option instead of PM2 or Docker for this bot.

After login and the initial foreground check, press **Ctrl+C**. Do not leave that foreground instance running alongside the service.

```bash
cd /opt/animebay-router
cp deploy/animebay-router.service /etc/systemd/system/animebay-router.service
systemctl daemon-reload
systemctl enable --now animebay-router
systemctl status animebay-router --no-pager
```

Logs:

```bash
journalctl -u animebay-router -n 100 --no-pager
journalctl -u animebay-router -f
```

Stop/restart:

```bash
systemctl stop animebay-router
systemctl restart animebay-router
```

If you install elsewhere, edit the paths in the service file. The `data` directory must exist before starting the service; the login step creates it.

## Alternative: Docker Compose

Use this instead of PM2 or the direct systemd service. Create `.env` first as described above:

```bash
docker compose build
docker compose run --rm userbot python -m anime_router --login
docker compose up -d
docker compose logs -f --tail=100
```

The `./data` bind mount preserves your login and queue. Stop it with `docker compose down`; keep `data` for the next start.

## Backups and repository hygiene

Stop the userbot before backing up the entire `data` directory and your `.env`. Store that backup privately. Restart it afterwards. To move the bot, transfer the private state alongside the source code. Retaining the state retains duplicate history and pending episodes.

The included `.gitignore` excludes `.env`, sessions and databases, and the Docker image only copies source code and requirements. Before a GitHub push, check `git status` and ensure no private runtime files were already tracked. A `.gitignore` cannot remove an earlier committed secret.

## Validation and limits

Run the offline behavioral tests after installing requirements:

```bash
python -m unittest discover -s tests -v
```

The tests cover parsing, exact season aliases, duplicate handling, restarts between posting stages, reversed quality order, per-channel ordering, schedule outages and recovery, timezone/date rollover, flood waits and copy request flags. The SubsPlease API was also checked live during development. Actual Telegram delivery still needs a test in your own private channel after account login and configuration.

Protected source channels cannot be copied; the userbot reports the restriction. It does not attempt to bypass it. Existing destination posts are not scanned for duplicate history, source edits/deletions are not mirrored, and inline keyboards are not recreated. The file captions and their entities are preserved by Telegram’s server-side copy operation.

Provider/API changes may require an update. This project runs locally on your VPS; it does not send your credentials or session to an external bot service.

Technical references: [SubsPlease schedule](https://subsplease.org/schedule/), [Telegram message copying and random IDs](https://core.telegram.org/method/messages.forwardMessages), [Telethon documentation](https://docs.telethon.dev/en/stable/).
