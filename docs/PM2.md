# PM2 installation and maintenance

PM2 is a process manager. It launches the userbot in the background, keeps logs and restarts the process if it crashes. With `pm2 startup` and `pm2 save`, it can restore the bot after a VPS reboot. You do not need a PM2 cloud account for this local setup.

Complete the [README installation and Telegram login](../README.md) first. The commands here assume a Linux VPS, installation at `/opt/animebay-router`, and the same Linux user that performed the login.

## Understand the three files/folders

| Item | What it does | What you edit |
| --- | --- | --- |
| `.env` | Stores your Telegram API credentials and bot settings | Fill in your own API ID/hash; change timezone or poll interval if needed |
| `.venv/` | Holds a separate Python interpreter and installed dependencies for this project | Create it and install `requirements.txt`; do not edit its files |
| `ecosystem.config.js` | Tells PM2 how to start this Python module and restart it | Usually no changes needed |

Credentials are read by Python from `.env`; PM2 does not need to parse that file. The ecosystem file contains only non-secret runtime settings. There is no need for `--env production` or a `NODE_ENV` value for this bot.

## 1. Install PM2

If Node.js/npm are not installed:

```bash
apt update
apt install -y nodejs npm
node --version
npm --version
```

Use a supported Node.js LTS release that satisfies PM2's current requirements. If your distribution's package is too old, install a supported release using the [official Node.js instructions](https://nodejs.org/en/download).

Install PM2:

```bash
npm install -g pm2@latest
pm2 --version
```

The commands assume root. If you operate as a non-root user, use administrator permission only where needed for package installation; run the bot and its PM2 commands consistently as that non-root user. Mixing `pm2` and `sudo pm2` uses different PM2 process lists.

## 2. Check your Python setup

```bash
cd /opt/animebay-router
.venv/bin/python --version
.venv/bin/pip install -r requirements.txt
```

Fill `.env` using the README. For reference, its settings are:

| Variable | Value / purpose |
| --- | --- |
| `API_ID` | Your numeric API ID from `my.telegram.org` |
| `API_HASH` | Your 32-character API hash |
| `TIMEZONE` | `Asia/Colombo` by default |
| `DATA_DIR` | `data` by default; stores login, mappings and queue history |
| `SOURCE_POLL_SECONDS` | `15` by default; minimum applied by the program is 5 seconds |

Keep `.env` private:

```bash
chmod 600 .env
```

Log in once from the terminal, before PM2 starts the process:

```bash
.venv/bin/python -m anime_router --login
```

Enter the Telegram code and any two-step verification password directly in that terminal. Login is saved in `data/account.session`; PM2 then starts the bot without interactive prompts. If you already logged in with the same `data` directory, reuse that login.

## 3. Start the bot

Stop the foreground instance with **Ctrl+C** first. If you are migrating from another manager, stop its instance as explained below.

```bash
cd /opt/animebay-router
pm2 start ecosystem.config.js
pm2 status animebay-router
pm2 logs animebay-router --lines 50
```

The status should become `online`. Check that `.status` in Telegram Saved Messages replies. If the process repeatedly exits, read the logs; `online` immediately after startup is not proof that Telegram login/configuration is correct.

Press **Ctrl+C** to close the log viewer. This does not stop the bot. You can now disconnect SSH.

The ecosystem configuration uses:

- The `.venv/bin/python` executable under this project folder.
- `-m anime_router` as the Python arguments.
- The project folder as its working directory, so `.env` and `data` resolve consistently.
- One process in fork mode, with file watching disabled.
- A 15-second restart delay and up to 30 seconds for shutdown.
- A limit on repeated short-lived failures, so an invalid configuration does not restart forever.

## 4. Restore after a VPS reboot

```bash
pm2 startup
```

PM2 detects the init system. If it prints an additional command, copy and run that exact command. Then:

```bash
pm2 save
```

`pm2 save` saves the current user's full PM2 process list, including any other apps in that list. It does not stop those apps. Repeat it after changes to which applications should return at boot.

If you later change the Node.js installation path, update PM2's startup integration according to its [startup documentation](https://pm2.keymetrics.io/docs/usage/startup/).

## Daily commands

| Task | Command |
| --- | --- |
| View this bot's status | `pm2 status animebay-router` |
| View recent/live logs | `pm2 logs animebay-router --lines 100` |
| Restart after editing `.env` | `pm2 restart animebay-router` |
| Stop the bot | `pm2 stop animebay-router` |
| Start a stopped, registered bot | `pm2 restart animebay-router` |
| Remove this bot from PM2 | `pm2 delete animebay-router` |
| Save the current process list | `pm2 save` |

Use these commands for `animebay-router` specifically. Commands such as `pm2 stop all` affect your other bots too.

Stopping or deleting the PM2 process does not delete the Telegram session or the SQLite queue. To keep the bot stopped after reboot, stop/delete it and run `pm2 save`.

## Change settings

```bash
cd /opt/animebay-router
nano .env
pm2 restart animebay-router
```

The Python process reloads `.env` on restart. If you previously exported `API_ID`, `API_HASH` or other bot variables in the shell used to start PM2, those inherited environment values take precedence over `.env`. Use one settings source consistently. No exports are needed when following this guide.

If you intentionally change the ecosystem configuration, restart using the configuration file:

```bash
cd /opt/animebay-router
pm2 restart ecosystem.config.js
pm2 save
```

Changing `DATA_DIR` switches to a different state directory. Your saved login and duplicate history are in the original directory; keep the same directory unless you are deliberately migrating it.

## Update the code from GitHub

Stop this bot, pull the update, install any changed dependencies, and restart:

```bash
cd /opt/animebay-router
pm2 stop animebay-router
git pull --ff-only
.venv/bin/pip install -r requirements.txt
pm2 restart ecosystem.config.js
pm2 save
```

Do not continue to restart if `git pull` or the dependency installation failed; resolve that error first. `.env` and `data` are ignored by Git and are retained during a normal update. Do not replace them with the example settings or delete them.

## Switch from the included direct systemd service

If you previously enabled `animebay-router.service`, stop and disable **that service only**:

```bash
systemctl disable --now animebay-router
```

Then start the PM2 configuration from the same project directory. The existing `data` directory is reused, so pending episodes remain queued. PM2's own boot service is separate; disabling the direct bot service does not disable PM2 itself.

## Switch from Docker Compose

From the directory containing this project's `compose.yaml`:

```bash
docker compose down
```

The Compose setup stores state in `./data`. Use that same directory when switching to the local Python/PM2 setup, and ensure the Linux user running PM2 can read and write it. Then create `.venv`, install requirements and start PM2. Do not run both instances.

## Troubleshooting

**`Script not found` or `.venv/bin/python` missing:** run the commands below from the project folder, then start the ecosystem file again:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**`Fill API_ID and API_HASH`:** edit `.env` and supply your own credentials, then restart the PM2 process.

**`Log in first`:** stop the PM2 process, run the interactive `--login` command, then restart it.

**`Another router process is already using this data directory`:** a foreground, systemd, Docker or second PM2 instance is already running. Stop that instance. Do not delete session/database files to bypass this check.

**PM2 says `errored`:** check `pm2 logs animebay-router --lines 100`, fix the issue, then `pm2 restart animebay-router`. Repeated fast failures are intentionally limited.

**Bot is online but sends no episodes:** check `.sources`, `.routes`, `.unmatched` and `.pending` in Telegram Saved Messages. A route needs its sticker and schedule before `.resume <key>`. Adding a source starts from new posts; use an explicit `.rescan` only when you want to publish older source files.

**Nothing returns after reboot:** run `pm2 startup`, follow its printed command if any, then `pm2 save` under the same Linux user that runs the bot.

**You want to return to systemd or Docker:** stop/remove `animebay-router` from PM2 and save the process list before starting the other manager. Retain the existing state directory.

Official references: [PM2 quick start](https://pm2.keymetrics.io/docs/usage/quick-start/), [ecosystem options](https://pm2.keymetrics.io/docs/usage/application-declaration/), [startup and saved processes](https://pm2.keymetrics.io/docs/usage/startup/).
