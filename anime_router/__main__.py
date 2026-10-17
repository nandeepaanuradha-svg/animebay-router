import argparse
import asyncio
import fcntl
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

from . import __version__
from .app import App
from .schedule import Schedule
from .store import Store


async def main(args):
    load_dotenv()
    os.umask(0o077)
    try:
        api_id = int(os.environ.get("API_ID", "0"))
        api_hash = os.environ.get("API_HASH", "").strip()
        if api_id <= 0 or len(api_hash) != 32 or any(c not in "0123456789abcdefABCDEF" for c in api_hash):
            raise ValueError
    except ValueError:
        raise SystemExit("Fill API_ID and API_HASH in .env using your own https://my.telegram.org API credentials.")
    directory = Path(os.environ.get("DATA_DIR", "data")).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    lock = (directory / "router.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another router process is already using this data directory. Stop it before starting another.")
    client = TelegramClient(str(directory / "account"), api_id, api_hash,
                            app_version=f"AnimeBayRouter {__version__}",
                            flood_sleep_threshold=0, request_retries=3,
                            auto_reconnect=True, catch_up=True)
    store = None
    schedule = None
    try:
        if args.login:
            await client.start()
            me = await client.get_me()
            if me.bot:
                raise SystemExit("Use a Telegram user account, not a bot token.")
            print("Login saved. Start the router with: python -m anime_router")
            return
        await client.connect()
        if not await client.is_user_authorized():
            raise SystemExit("Log in first by running: python -m anime_router --login")
        store = Store(directory / "router.sqlite3")
        schedule = Schedule(store, timezone=os.environ.get("TIMEZONE", "Asia/Colombo"))
        poll_seconds = max(5, int(os.environ.get("SOURCE_POLL_SECONDS", "15")))
        await App(client, store, schedule, poll_seconds).run()
    finally:
        if schedule:
            await schedule.close()
        if store:
            store.close()
        await client.disconnect()
        lock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AnimeBay episode channel router")
    parser.add_argument("--login", action="store_true", help="Interactive first-time Telegram account login")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(main(arguments))
    except KeyboardInterrupt:
        pass
