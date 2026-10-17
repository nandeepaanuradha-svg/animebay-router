"""Saved Messages command interface and live source monitoring."""

import asyncio
import contextlib
import logging
import re
import shlex
import signal
import sqlite3
import time
from datetime import datetime

from telethon import errors, events, utils, types

from .engine import Engine, Transport, pending_text
from .parser import parse_release, display_episode

log = logging.getLogger(__name__)

HELP = """AnimeBay Router — commands in Saved Messages only

.chats [name] — find joined channel IDs
.source <channel ID or @username> — watch NEW posts from now
.sources — list watched sources
.rmsource <channel ID> — stop watching (queued work is retained)
.add <key> <destination> <anime title> — create a paused route
.alias <key> <filename title> — add an exact title/season alias
.unalias <key> <filename title> — remove an alias
.spsearch <title words> — search SubsPlease’s current weekly schedule
.schedule <key> <SubsPlease show slug or URL>
.setsticker <key> — reply to a sticker kept in Saved Messages
.resume <key> — enable a configured route
.pause <key> — pause that route
.pause / .resume — pause/resume all delivery
.routes / .show <key> — view configuration
.next <key> — preview its next release message
.nextat <key> <YYYY-MM-DD HH:MM> — manual next release in Sri Lankan time
.nextat <key> off — remove manual override
.finished <key> on|off — mark a completed season/series
.pending — list unfinished episodes
.unmatched — filenames without a matching anime route
.inspect — reply to a video file to check parsing, without posting
.rescan <source ID> <count> — queue up to 5000 existing source posts, oldest first
.retry <key> — retry failed pending sends (Telegram waits still apply)
.refresh <key> — refresh the most recent completed episode’s schedule
.status / .help

Every route needs its sticker and schedule configured before .resume <key>.
Missing 1080p stays pending; the sticker and schedule follow only after 1080p.
"""


class App:
    def __init__(self, client, store, schedule, poll_seconds=15):
        self.client = client
        self.store = store
        self.schedule = schedule
        self.poll_seconds = poll_seconds
        self.me = None
        self.command_lock = asyncio.Lock()
        self.scan_lock = asyncio.Lock()
        self.extra_tasks = set()
        self.engine = Engine(store, schedule, Transport(client, store), self.notify)

    async def notify(self, text):
        try:
            for start in range(0, len(text), 3500):
                # Every chunk has a non-command prefix, including chunks that
                # contain filenames or help text beginning with a dot.
                await self.client.send_message("me", "🤖 " + text[start:start + 3500], parse_mode=None, link_preview=False)
        except errors.FloodWaitError as exc:
            self.store.set("send_not_before", int(time.time()) + exc.seconds + 1)
            log.warning("Saved Messages notification delayed by Telegram")
        except Exception as exc:
            log.warning("Could not send a Saved Messages notification: %s", type(exc).__name__)

    async def run(self):
        self.me = await self.client.get_me()
        if self.me.bot:
            raise ValueError("Log in as a Telegram user account, not a bot")
        await self.client.get_dialogs(limit=200)  # warm the entity cache for joined IDs
        self.client.add_event_handler(self.on_message, events.NewMessage())
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        tasks = [asyncio.create_task(self.poll_loop()), asyncio.create_task(self.worker_loop()),
                 asyncio.create_task(self.client.run_until_disconnected()), asyncio.create_task(stop.wait())]
        log.info("Router running. Configure it with .help in Saved Messages.")
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.cancelled() and task.exception():
                    raise task.exception()
        finally:
            for task in tasks + list(self.extra_tasks):
                task.cancel()
            await asyncio.gather(*tasks, *self.extra_tasks, return_exceptions=True)
            self.client.remove_event_handler(self.on_message)

    async def on_message(self, event):
        message = event.message
        if event.chat_id == self.me.id:
            if (message.out and message.sender_id == self.me.id and not message.fwd_from
                    and (message.raw_text or "").startswith(".")):
                async with self.command_lock:
                    try:
                        await self.command(message)
                    except errors.FloodWaitError as exc:
                        self.store.set("send_not_before", int(time.time()) + exc.seconds + 1)
                        log.warning("Command hit Telegram wait: %s seconds", exc.seconds)
                    except (ValueError, KeyError, IndexError, sqlite3.IntegrityError) as exc:
                        await self.notify(f"⚠️ {exc}\nUse .help for command syntax.")
                    except Exception as exc:
                        log.exception("Command failed")
                        await self.notify(f"⚠️ Command failed: {type(exc).__name__}: {str(exc)[:250]}")
            return
        if self.store.one("SELECT chat_id FROM sources WHERE chat_id=?", (event.chat_id,)):
            self.ingest(event.chat_id, message)

    def ingest(self, chat_id, message):
        if not message or not message.document:
            return
        # Stickers and unrelated documents are ignored by the filename parser.
        filename = message.file.name or ""
        caption = message.message or ""
        self.store.capture(chat_id, message.id, filename, caption)

    async def poll_loop(self):
        while True:
            for source in self.store.rows("SELECT * FROM sources"):
                try:
                    async with self.scan_lock:
                        async for message in self.client.iter_messages(source["chat_id"], min_id=source["cursor"],
                                                                        reverse=True, limit=200):
                            self.ingest(source["chat_id"], message)
                            self.store.cursor(source["chat_id"], message.id)
                except errors.FloodWaitError as exc:
                    # Only scanning waits here; the output worker continues.
                    log.warning("Source scanning waits %s seconds", exc.seconds)
                    await asyncio.sleep(exc.seconds + 1)
                except Exception as exc:
                    key = f"source_error_{source['chat_id']}"
                    if int(time.time()) - self.store.get(key, 0) > 21600:
                        self.store.set(key, int(time.time()))
                        await self.notify(f"⚠️ Cannot read source {source['chat_id']}: {type(exc).__name__}. "
                                          "Check that this account can access the channel. Its cursor was retained.")
                    log.warning("Source %s: %s", source["chat_id"], type(exc).__name__)
            await asyncio.sleep(self.poll_seconds)

    async def worker_loop(self):
        next_refresh = 0
        while True:
            await self.engine.tick()
            if time.time() >= next_refresh:
                await self.engine.refresh()
                next_refresh = time.time() + 60
            await asyncio.sleep(1)

    async def channel(self, value, destination=False):
        ref = int(value) if re.fullmatch(r"-?\d+", value) else value
        entity = await self.client.get_entity(ref)
        if not isinstance(entity, types.Channel):
            raise ValueError("Choose a channel or supergroup this account has joined")
        if destination:
            if not entity.broadcast:
                raise ValueError("The destination must be a broadcast channel")
            rights = await self.client.get_permissions(entity, self.me)
            if not rights.is_creator and not rights.post_messages:
                raise ValueError("This Telegram account needs permission to post in the destination channel")
        elif getattr(entity, "noforwards", False):
            raise ValueError("This source protects its content. Enable forwarding in your channel’s settings first")
        return utils.get_peer_id(entity)

    def require_route(self, key):
        route = self.store.route(key)
        if not route:
            raise ValueError(f"Unknown route: {key}")
        return route

    async def rescan(self, chat_id, count):
        try:
            async with self.scan_lock:
                messages = await self.client.get_messages(chat_id, limit=count)
                for message in reversed(messages):
                    self.ingest(chat_id, message)
            await self.notify(f"Rescan complete: examined {len(messages)} messages in {chat_id}. "
                              "Existing episode/quality pairs were not queued twice.")
        except Exception as exc:
            await self.notify(f"Rescan stopped: {type(exc).__name__}: {str(exc)[:180]}. "
                              "Queued work is saved; rerun the rescan to cover the remaining posts.")

    async def command(self, message):
        args = shlex.split(message.raw_text)
        cmd, args = args[0].lower(), args[1:]
        if cmd == ".help":
            return await self.notify(HELP)
        if cmd == ".chats":
            query = " ".join(args).casefold()
            found = []
            async for dialog in self.client.iter_dialogs():
                if isinstance(dialog.entity, types.Channel) and query in (dialog.name or "").casefold():
                    found.append(f"{dialog.id} | {dialog.name}")
                    if len(found) == 30:
                        break
            return await self.notify("\n".join(found) or "No joined channels matched.")
        if cmd == ".source":
            if len(args) != 1:
                raise ValueError("Usage: .source <channel ID or @username>")
            chat_id = await self.channel(args[0])
            if self.store.one("SELECT key FROM routes WHERE destination=?", (chat_id,)):
                raise ValueError("A destination cannot also be an input source")
            if self.store.one("SELECT chat_id FROM sources WHERE chat_id=?", (chat_id,)):
                return await self.notify("Already watching this source; its cursor has been preserved.")
            latest = await self.client.get_messages(chat_id, limit=1)
            cursor = latest[0].id if latest else 0
            self.store.execute("INSERT INTO sources VALUES (?,?)", (chat_id, cursor))
            return await self.notify(f"Watching new posts in {chat_id}, after message {cursor}. "
                                      f"For existing files use .rescan {chat_id} 100")
        if cmd == ".sources":
            return await self.notify("\n".join(f"{r['chat_id']} | cursor {r['cursor']}" for r in
                                      self.store.rows("SELECT * FROM sources")) or "No source channels configured.")
        if cmd == ".rmsource":
            if len(args) != 1:
                raise ValueError("Usage: .rmsource <numeric source ID>")
            self.store.execute("DELETE FROM sources WHERE chat_id=?", (int(args[0]),))
            return await self.notify("Stopped watching that source. Queued episodes are retained.")
        if cmd == ".add":
            if len(args) < 3:
                raise ValueError("Usage: .add <key> <destination> <anime title>")
            key, destination, title = args[0], args[1], " ".join(args[2:])
            if not re.fullmatch(r"[a-z0-9_]{1,32}", key):
                raise ValueError("Route keys use 1–32 lowercase letters, digits or underscores")
            if len(title) > 200:
                raise ValueError("Anime title is too long")
            chat_id = await self.channel(destination, destination=True)
            if self.store.one("SELECT chat_id FROM sources WHERE chat_id=?", (chat_id,)):
                raise ValueError("An input source cannot also be a destination")
            self.store.add_route(key, title, chat_id)
            matched = self.store.rematch()
            return await self.notify(f"Created paused route {key}: {title} → {chat_id}.\n"
                                      f"Matched {matched} previously unmatched files.\n"
                                      "Add filename aliases, set its schedule and sticker, then .resume " + key)
        if cmd in (".alias", ".unalias"):
            if len(args) < 2:
                raise ValueError(f"Usage: {cmd} <key> <filename title>")
            self.require_route(args[0])
            title = " ".join(args[1:])
            if cmd == ".alias":
                self.store.add_alias(args[0], title)
                matched = self.store.rematch()
                return await self.notify(f"Alias saved. Matched {matched} waiting files.")
            from .parser import normalize
            self.store.execute("DELETE FROM aliases WHERE route=? AND normalized=?", (args[0], normalize(title)))
            return await self.notify("Alias removed. Already queued jobs are retained.")
        if cmd == ".spsearch":
            if not args:
                raise ValueError("Usage: .spsearch <title words>")
            result = await self.schedule.search(" ".join(args))
            lines = []
            from .schedule import next_slot
            for entry in result[:20]:
                at = datetime.fromtimestamp(next_slot(entry, time.time()), self.schedule.timezone)
                lines.append(f"{entry['title']}\n{entry['slug']} | {at.strftime('%A %I:%M %p')}")
            return await self.notify("\n\n".join(lines) or "Not found in SubsPlease’s current weekly schedule. "
                                      "Try its Japanese title, a shorter word, or set the show slug manually.")
        if cmd == ".schedule":
            if len(args) != 2:
                raise ValueError("Usage: .schedule <key> <SubsPlease show slug or URL>")
            self.require_route(args[0])
            from .schedule import slug_from
            slug = slug_from(args[1])
            self.store.execute("UPDATE routes SET slug=? WHERE key=?", (slug, args[0]))
            self.store.execute("UPDATE jobs SET refresh_at=1 WHERE route=? AND done=1", (args[0],))
            return await self.notify(f"SubsPlease show saved: {slug}\nUse .next {args[0]} to check its schedule.")
        if cmd == ".setsticker":
            if len(args) != 1:
                raise ValueError("Reply to a sticker with .setsticker <key>")
            self.require_route(args[0])
            reply = await message.get_reply_message()
            if not reply or not reply.sticker:
                raise ValueError("Send or forward a sticker to Saved Messages, then reply to it with this command")
            self.store.execute("UPDATE routes SET sticker_chat=?,sticker_id=? WHERE key=?",
                               (self.me.id, reply.id, args[0]))
            return await self.notify("Sticker saved for " + args[0] + ". Keep that sticker message in Saved Messages.")
        if cmd in (".pause", ".resume"):
            if not args:
                self.store.set("paused", cmd == ".pause")
                return await self.notify("All delivery paused." if cmd == ".pause" else "Global pause cleared. Enabled routes will continue.")
            if len(args) != 1:
                raise ValueError(f"Usage: {cmd} [key]")
            route = self.require_route(args[0])
            if cmd == ".resume":
                if not route["sticker_id"]:
                    raise ValueError("Set this route’s sticker first with .setsticker " + args[0])
                if not route["slug"] and not route["finished"] and not (route["override_at"] or 0) > time.time():
                    raise ValueError("Set its SubsPlease schedule first with .schedule " + args[0] + " <slug>")
            self.store.execute("UPDATE routes SET enabled=? WHERE key=?", (cmd == ".resume", args[0]))
            return await self.notify(args[0] + (" enabled." if cmd == ".resume" else " paused."))
        if cmd == ".routes":
            lines = [f"{r['key']} | {'ON' if r['enabled'] else 'PAUSED'} | {r['title']} → {r['destination']}"
                     for r in self.store.rows("SELECT * FROM routes ORDER BY key")]
            return await self.notify("\n".join(lines) or "No routes. Start with .add")
        if cmd == ".show":
            if len(args) != 1:
                raise ValueError("Usage: .show <key>")
            route = self.require_route(args[0])
            aliases = [a["title"] for a in self.store.rows("SELECT title FROM aliases WHERE route=?", (args[0],))]
            return await self.notify(f"{route['key']} | {route['title']}\nDestination: {route['destination']}\n"
                                      f"Enabled: {bool(route['enabled'])}\nSubsPlease: {route['slug'] or 'not set'}\n"
                                      f"Sticker: {'set' if route['sticker_id'] else 'not set'}\n"
                                      f"Completed: {bool(route['finished'])}\nAliases:\n" + "\n".join(aliases))
        if cmd == ".next":
            if len(args) != 1:
                raise ValueError("Usage: .next <key>")
            text, _ = await self.schedule.message(self.require_route(args[0]))
            return await self.notify(text)
        if cmd == ".nextat":
            if len(args) < 2:
                raise ValueError("Usage: .nextat <key> <YYYY-MM-DD HH:MM> or .nextat <key> off")
            self.require_route(args[0])
            value = " ".join(args[1:])
            at = None
            if value.lower() != "off":
                date = datetime.fromisoformat(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=self.schedule.timezone)
                at = int(date.timestamp())
                if at <= time.time():
                    raise ValueError("Choose a future date and time")
            self.store.execute("UPDATE routes SET override_at=? WHERE key=?", (at, args[0]))
            self.store.execute("UPDATE jobs SET refresh_at=1 WHERE id=(SELECT MAX(id) FROM jobs WHERE route=? AND done=1)", (args[0],))
            return await self.notify("Manual next-release time updated. Use .next " + args[0] + " to preview.")
        if cmd == ".finished":
            if len(args) != 2 or args[1] not in ("on", "off"):
                raise ValueError("Usage: .finished <key> on|off")
            self.require_route(args[0])
            self.store.execute("UPDATE routes SET finished=? WHERE key=?", (args[1] == "on", args[0]))
            self.store.execute("UPDATE jobs SET refresh_at=1 WHERE id=(SELECT MAX(id) FROM jobs WHERE route=? AND done=1)", (args[0],))
            return await self.notify("Completed-season setting updated.")
        if cmd == ".pending":
            return await self.notify(pending_text(self.store))
        if cmd == ".unmatched":
            rows = self.store.rows("SELECT * FROM inbox WHERE status='unmatched' ORDER BY message_id DESC LIMIT 20")
            return await self.notify("\n\n".join(f"{r['chat_id']}/{r['message_id']}\n{r['filename']}\nTitle: {r['reason']}"
                                                 for r in rows) or "No unmatched files.")
        if cmd == ".inspect":
            reply = await message.get_reply_message()
            if not reply or not reply.document:
                raise ValueError("Forward a file to Saved Messages, then reply to it with .inspect")
            release = parse_release(reply.file.name or "", reply.message or "")
            route = self.store.match(release.title)
            return await self.notify(f"Title: {release.title}\nEpisode: {display_episode(release.episode)}\n"
                                      f"Quality: {release.quality}\nRoute: {route['key'] if route else 'UNMATCHED'}\nNo file was posted.")
        if cmd == ".rescan":
            if len(args) != 2 or not 1 <= int(args[1]) <= 5000:
                raise ValueError("Usage: .rescan <source ID> <count between 1 and 5000>")
            chat_id, count = int(args[0]), int(args[1])
            if not self.store.one("SELECT chat_id FROM sources WHERE chat_id=?", (chat_id,)):
                raise ValueError("Register that source first with .source")
            task = asyncio.create_task(self.rescan(chat_id, count))
            self.extra_tasks.add(task)
            task.add_done_callback(self.extra_tasks.discard)
            return await self.notify("Rescan started. Matching files will be queued for enabled routes.")
        if cmd == ".retry":
            if len(args) != 1:
                raise ValueError("Usage: .retry <key>")
            self.require_route(args[0])
            self.store.execute("UPDATE jobs SET retry_at=0,attempts=0,error='',notified_at=0 WHERE route=? AND done=0", (args[0],))
            return await self.notify("Pending failures will retry. Any Telegram-required wait still applies.")
        if cmd == ".refresh":
            if len(args) != 1:
                raise ValueError("Usage: .refresh <key>")
            self.require_route(args[0])
            self.schedule.retry_at = 0
            await self.schedule.load(force=True)
            self.store.execute("UPDATE jobs SET refresh_at=1 WHERE id=(SELECT MAX(id) FROM jobs WHERE route=? AND done=1)", (args[0],))
            return await self.notify("Schedule refresh queued for the most recent completed episode.")
        if cmd == ".status":
            counts = {name: self.store.one(f"SELECT COUNT(*) AS n FROM {name}")["n"]
                      for name in ("sources", "routes", "jobs")}
            pending = self.store.one("SELECT COUNT(*) AS n FROM jobs WHERE done=0")["n"]
            wait = max(0, self.store.get("send_not_before", 0) - int(time.time()))
            return await self.notify(f"AnimeBay Router running\nGlobal pause: {self.store.get('paused', False)}\n"
                                      f"Sources: {counts['sources']} | Routes: {counts['routes']}\n"
                                      f"Episodes: {counts['jobs']} | Pending: {pending}\nTelegram wait: {wait}s\n"
                                      f"Timezone: {self.schedule.timezone.key}")
        await self.notify("Unknown command. Use .help")
