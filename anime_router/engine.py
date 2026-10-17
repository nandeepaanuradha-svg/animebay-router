"""Ordered delivery and Telegram transport, with a durable outbox."""

import asyncio
import json
import logging
import time

from telethon import errors, functions, types

from .parser import display_episode

log = logging.getLogger(__name__)


class Transport:
    def __init__(self, client, store):
        self.client = client
        self.store = store
        self.last_send = 0.0

    async def send(self, destination, op):
        # Serialize actual sends and retain the same random_id across retries.
        await asyncio.sleep(max(0, 1.2 - (time.monotonic() - self.last_send)))
        peer = await self.client.get_input_entity(destination)
        payload = op["payload"]
        if payload["kind"] == "copy":
            source = await self.client.get_input_entity(payload["chat_id"])
            request = functions.messages.ForwardMessagesRequest(
                from_peer=source, id=[payload["message_id"]], to_peer=peer,
                random_id=[op["random_id"]], drop_author=True,
                drop_media_captions=False)
        else:
            text = payload["text"]
            entities = ([types.MessageEntityBold(0, len(text.encode("utf-16-le")) // 2)]
                        if op["stage"] == "heading" else None)
            request = functions.messages.SendMessageRequest(peer=peer, message=text,
                       random_id=op["random_id"], no_webpage=True, entities=entities)
        try:
            result = await self.client(request)
        except errors.RandomIdDuplicateError:
            # Telegram accepted an earlier attempt before the local commit.
            # Text IDs are useful for later edits, but copies need no later edit.
            if payload["kind"] == "text":
                async for message in self.client.iter_messages(peer, limit=100):
                    if message.message == payload["text"]:
                        return message.id
            return None
        finally:
            self.last_send = time.monotonic()
        for update in getattr(result, "updates", []):
            if isinstance(update, types.UpdateMessageID) and update.random_id == op["random_id"]:
                return update.id
        if isinstance(result, types.UpdateShortSentMessage):
            return result.id
        for update in getattr(result, "updates", []):
            if isinstance(update, (types.UpdateNewMessage, types.UpdateNewChannelMessage)):
                return update.message.id
        # A send accepted without its ID is never resent with a fresh random_id.
        return None

    async def edit(self, destination, message_id, text):
        try:
            await self.client.edit_message(destination, message_id, text,
                                           parse_mode=None, link_preview=False)
        except errors.MessageNotModifiedError:
            pass


class Engine:
    def __init__(self, store, schedule, transport, notify):
        self.store = store
        self.schedule = schedule
        self.transport = transport
        self.notify = notify

    async def tick(self):
        now = int(time.time())
        if self.store.get("paused", False) or now < self.store.get("send_not_before", 0):
            return
        # One open episode per destination. Waiting in one channel does not
        # prevent other channels receiving their episodes.
        seen = set()
        for job in self.store.pending():
            if not job["enabled"] or job["destination"] in seen:
                continue
            seen.add(job["destination"])
            if job["retry_at"] > now:
                continue
            try:
                await self.process(job)
            except errors.FloodWaitError as exc:
                self.store.set("send_not_before", now + exc.seconds + 1)
                log.warning("Telegram requested a wait of %ss", exc.seconds)
                return
            except Exception as exc:
                await self.failed(job, exc)

    async def failed(self, job, exc):
        error = f"{type(exc).__name__}: {str(exc)[:240]}"
        attempts = job["attempts"] + 1
        now = int(time.time())
        delay = min(3600, 5 * (2 ** min(attempts, 10)))
        self.store.execute("UPDATE jobs SET error=?,attempts=?,retry_at=? WHERE id=?",
                           (error, attempts, now + delay, job["id"]))
        log.warning("Episode job %s: %s", job["id"], error)
        if now - job["notified_at"] > 21600:
            self.store.execute("UPDATE jobs SET notified_at=? WHERE id=?", (now, job["id"]))
            await self.notify(f"⚠️ {job['title']} · Episode {display_episode(job['episode'])}\n"
                              f"{error}\nKept pending. Fix the issue, then use .retry {job['route']}.")

    async def process(self, job):
        media = self.store.qualities(job["id"])
        if "720p" not in media:
            return
        route = self.store.route(job["route"])
        stages = ["heading", "720p", "1080p", "sticker", "schedule"]
        for stage in stages:
            existing = self.store.operation(job["id"], stage)
            if existing and existing["done"]:
                continue
            if stage in ("720p", "1080p"):
                if stage not in media:
                    return
                item = media[stage]
                payload = {"kind": "copy", "chat_id": item["chat_id"], "message_id": item["message_id"]}
            elif stage == "heading":
                payload = {"kind": "text", "text": f"📺 {job['title']} 📺\n📗 Episode {display_episode(job['episode'])} 📗"}
            elif stage == "sticker":
                if not route["sticker_id"] or not route["sticker_chat"]:
                    raise ValueError("Set this route’s sticker before continuing")
                payload = {"kind": "copy", "chat_id": route["sticker_chat"], "message_id": route["sticker_id"]}
            else:
                anchor = job["anchor"] or int(time.time())
                self.store.execute("UPDATE jobs SET anchor=? WHERE id=?", (anchor, job["id"]))
                text, at = await self.schedule.message(route, job["episode"], anchor)
                payload = {"kind": "text", "text": text, "expected_at": at}
            op = existing or self.store.prepare(job["id"], stage, payload)
            message_id = await self.transport.send(job["destination"], op)
            self.store.sent(op, message_id)
            # Start the next stage on the next tick; commands and incoming
            # releases remain responsive between network operations.
            return

    async def refresh(self):
        now = int(time.time())
        if self.store.get("paused", False) or now < self.store.get("send_not_before", 0):
            return
        jobs = self.store.rows("SELECT j.* FROM jobs j JOIN routes r ON j.route=r.key "
                               "WHERE j.done=1 AND j.refresh_at>0 AND j.refresh_at<=? AND r.enabled=1 "
                               "AND j.id=(SELECT MAX(j2.id) FROM jobs j2 WHERE j2.route=j.route AND j2.done=1) "
                               "ORDER BY j.refresh_at LIMIT 10", (now,))
        for job in jobs:
            op = self.store.operation(job["id"], "schedule")
            if not op or not op["done"]:
                continue
            try:
                text, at = await self.schedule.message(self.store.route(job["route"]), job["episode"], job["anchor"])
                # Keep the anchor fixed: a late refresh must not silently
                # reinterpret the next episode as the following week's episode.
                expires = (at is not None and now > at + 86400) or now > job["anchor"] + 14 * 86400
                if text != op["payload"]["text"]:
                    if op["message_id"] is None:
                        raise ValueError("Schedule message ID was not returned; update it manually")
                    await self.transport.edit(job["destination"], op["message_id"], text)
                    payload = dict(op["payload"], text=text, expected_at=at)
                    self.store.execute("UPDATE outbox SET payload=? WHERE job=? AND stage='schedule'",
                                       (json.dumps(payload, ensure_ascii=False), job["id"]))
                self.store.execute("UPDATE jobs SET refresh_at=? WHERE id=?",
                                   (0 if expires else now + 900, job["id"]))
            except errors.FloodWaitError as exc:
                self.store.set("send_not_before", now + exc.seconds + 1)
                return
            except Exception as exc:
                # Stop repeating a missing/deleted-message alert. The owner can
                # explicitly request another update with .refresh route-key.
                self.store.execute("UPDATE jobs SET refresh_at=0 WHERE id=?", (job["id"],))
                await self.notify(f"⚠️ Could not update the airing message for {job['route']} episode {job['episode']}: "
                                  f"{type(exc).__name__}. Use .refresh {job['route']} after fixing it.")


def pending_text(store):
    result = []
    for job in store.pending()[:30]:
        qualities = store.qualities(job["id"])
        if job["error"]:
            state = job["error"]
        elif not job["enabled"]:
            state = "route paused"
        elif "720p" not in qualities:
            state = "waiting for 720p"
        elif "1080p" not in qualities:
            state = "waiting for 1080p"
        else:
            state = "ready / publishing"
        result.append(f"{job['route']} · Episode {display_episode(job['episode'])}: {state}")
    return "\n".join(result) or "No pending episodes."
