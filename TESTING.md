# Validation record — 17 October 2026

- 30 offline behavioral tests passed on Python 3.12 with Telethon 1.45.0.
- The live SubsPlease API was queried through the project schedule client: 56 weekly entries were received with timezone `Asia/Colombo`. The entry count is only an observation from this run.
- The command-line help entry point loaded successfully.
- systemd unit syntax was verified after temporarily substituting local executable and file paths; the delivered unit retains its documented `/opt/animebay-router` installation paths.
- Docker was not built here because the Docker CLI is unavailable in this environment.
- No Telegram account was logged in and no channel posts were sent during development. Test actual delivery in a private channel after your own login and route setup.
- PM2 follow-up: the 30 behavioral tests passed again; Node.js syntax and configuration checks verified the virtual-environment executable, working directory, module arguments, single instance and non-secret environment settings.
- PM2 7.0.4 was installed for an isolated launch check. This host rejected PM2's local socket connections with `EPERM`, so an actual PM2-managed start/restart could not be verified here. The configuration follows PM2's documented binary/interpreter and lifecycle options; verify `online`, logs and Telegram `.status` on your VPS.

Run the included tests with:

```bash
python -m unittest discover -s tests -v
```
