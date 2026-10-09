"""Private Telegram transport, durable pairing, and household onboarding API."""

import asyncio
import contextlib
import hashlib
import io
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr

from .models import ConversationRequest

MEMORY_NOTICE = (
    "Your chat history is separate from other household members' chats. Messages, "
    "facts, and reported learning or behavior may be saved in shared household memory "
    "and visible in the household workspace. This is not a private journal. "
    "Telegram also carries these messages. Text chat is supported; voice, timers, "
    "and home-device control are not available here."
)
WELCOME = ("You're connected to Plata. " + MEMORY_NOTICE + "\n\n"
           "Try asking: Help me think of a rainy-day activity.\n"
           "/new starts a fresh chat without deleting shared memory. "
           "/disconnect removes your Telegram access. /help shows this information.")


class TelegramError(Exception):
    """A credential-free error suitable for the workspace."""

    def __init__(self, message, retry_after=5, permanent=False):
        super().__init__(message)
        self.retry_after = retry_after
        self.permanent = permanent


class TelegramAPI:
    def __init__(self):
        # httpx INFO logs contain the bot token in the request path.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self.client = httpx.AsyncClient(timeout=40)

    async def call(self, token, method, **payload):
        try:
            response = await self.client.post(f"https://api.telegram.org/bot{token}/{method}", json=payload)
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramError("Cannot reach Telegram. Check the server's Internet connection; retrying automatically.") from None
        if not isinstance(data, dict):
            raise TelegramError("Telegram returned an unreadable response. Retrying automatically.")
        if not data.get("ok"):
            code = data.get("error_code", response.status_code)
            messages = {
                401: "Telegram rejected the token. Reconnect with the current token from BotFather.",
                404: "This bot token did not work. Copy the full token from BotFather and try again.",
                409: "Another Telegram connection is active. Stop the other poller or remove its webhook before retrying.",
                403: "Telegram cannot deliver to this account. Unblock the bot and reconnect your account.",
                429: "Telegram is busy. Retrying after its rate limit expires.",
            }
            raise TelegramError(messages.get(code, "Telegram could not complete the request. Try again."),
                                min(3600, max(1, data.get("parameters", {}).get("retry_after", 5))),
                                permanent=code in {400, 403})
        return data["result"]

    async def close(self):
        await self.client.aclose()


def chunks(text, limit=4000):
    """Telegram counts UTF-16 units, including two units for astral characters."""
    part, size = [], 0
    for char in text:
        units = 2 if ord(char) > 0xFFFF else 1
        if size + units > limit:
            yield ''.join(part)
            part, size = [], 0
        part.append(char)
        size += units
    if part:
        yield ''.join(part)


class TelegramService:
    def __init__(self, router, knowledge, path=None, api=None):
        self.router, self.knowledge = router, knowledge
        self.path = path
        self.api = api
        self.db = None
        self.task = None
        self.lock = asyncio.Lock()
        self.error = None
        self.last_poll = None
        self.worker_lock = None

    def open(self):
        if self.db is not None:
            return
        path = self.path or Path(os.environ.get("DB_DIR", "./data")) / "telegram" / "state.db"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        # Create with restrictive mode before sqlite opens it (not after secret storage).
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        path.chmod(0o600)
        # One poller per database/process group, including multi-worker deployments.
        import fcntl
        self.worker_lock = open(path.with_suffix(".lock"), "a")
        try:
            fcntl.flock(self.worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.worker_lock.close()
            self.worker_lock = None
            raise RuntimeError("Telegram state is already in use. Run the agent server with one worker.") from None
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), token TEXT NOT NULL,
                username TEXT NOT NULL, bot_id INTEGER NOT NULL, offset INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS invites (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL,
                person_id TEXT NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL DEFAULT 'waiting',
                user_id INTEGER, chat_id INTEGER, label TEXT);
            CREATE TABLE IF NOT EXISTS accounts (user_id INTEGER PRIMARY KEY, chat_id INTEGER UNIQUE NOT NULL,
                person_id TEXT UNIQUE NOT NULL, label TEXT NOT NULL, conversation_id TEXT NOT NULL,
                first_reply REAL, delivery_error TEXT);
            CREATE TABLE IF NOT EXISTS inbox (update_id INTEGER PRIMARY KEY, payload TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued');
            CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
                text TEXT NOT NULL, first_reply INTEGER NOT NULL DEFAULT 0);
        ''')
        self.db.commit()

    def config(self):
        return self.db.execute("SELECT * FROM config WHERE id=1").fetchone()

    def status(self):
        config = self.config()
        return {"configured": bool(config), "username": config["username"] if config else None,
                "error": self.error, "last_poll": self.last_poll,
                "memory_notice": MEMORY_NOTICE,
                "accounts": [dict(r) for r in self.db.execute(
                    "SELECT user_id,person_id,label,first_reply,delivery_error FROM accounts")],
                "invitations": [{**dict(r), "state": "expired" if r["expires"] < time.time() else r["state"]}
                                for r in self.db.execute("SELECT id,person_id,expires,state,user_id,label FROM invites")]}

    async def start(self):
        self.open()
        if self.api is None:
            self.api = TelegramAPI()
        # A crash mid-generation has ambiguous side effects; never replay agent work.
        import json
        for row in self.db.execute("SELECT payload FROM inbox WHERE state='processing'").fetchall():
            chat = json.loads(row["payload"]).get("message", {}).get("chat", {}).get("id")
            if chat and self.db.execute("SELECT 1 FROM accounts WHERE chat_id=?", (chat,)).fetchone():
                self.queue(chat, "Plata restarted before finishing your last message. Please send it again.")
        self.db.execute("UPDATE inbox SET state='done',payload='{}' WHERE state='processing'")
        self.db.commit()
        self.launch()

    def launch(self):
        if self.config() and (not self.task or self.task.done()):
            self.task = asyncio.create_task(self.run())

    async def stop_worker(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def stop(self):
        await self.stop_worker()
        if self.api:
            await self.api.close()
        if self.db:
            self.db.close()
            self.db = None
        if self.worker_lock:
            self.worker_lock.close()
            self.worker_lock = None

    async def configure(self, token):
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
            raise ValueError("This token did not work. Copy the full token from BotFather and try again.")
        bot = await self.api.call(token, "getMe")
        webhook = await self.api.call(token, "getWebhookInfo")
        if webhook.get("url"):
            raise ValueError("This bot already uses a webhook. Create a dedicated bot for Plata, or remove the old connection first.")
        await self.api.call(token, "setMyDescription", description=(
            "Chat with your household's Plata. A household invitation and owner confirmation are required. "
            "Messages and facts may enter shared household memory and be visible in the household workspace. "
            "Telegram carries these messages. Text only; no voice or home-device control. "
            "Tap Start to request connection with these sharing settings."))
        await self.api.call(token, "setMyCommands", commands=[
            {"command": "help", "description": "Capabilities and shared-memory information"},
            {"command": "new", "description": "Fresh chat; shared memory is retained"},
            {"command": "disconnect", "description": "Remove your Telegram access"},
        ])
        # Finish in-flight work before changing the transport credential or identity.
        async with self.lock:
            await self.stop_worker()
            old = self.config()
            if old and old["bot_id"] != bot["id"]:
                self.clear_bindings()
                self.db.execute("DELETE FROM config")
            self.db.execute("""INSERT INTO config(id,token,username,bot_id) VALUES(1,?,?,?)
                ON CONFLICT(id) DO UPDATE SET token=excluded.token,username=excluded.username,bot_id=excluded.bot_id""",
                            (token, bot["username"], bot["id"]))
            self.db.commit()
            self.error = None
            self.last_poll = None
            self.launch()
        return self.status()

    def clear_bindings(self):
        for table in ("invites", "accounts", "inbox", "outbox"):
            self.db.execute(f"DELETE FROM {table}")

    async def disconnect_bot(self):
        async with self.lock:
            await self.stop_worker()
            self.clear_bindings()
            self.db.execute("DELETE FROM config")
            self.db.commit()
            self.error = None
            self.last_poll = None

    def invite(self, person_id):
        config = self.config()
        if not config:
            raise ValueError("Connect your household bot first.")
        if not any(p["id"] == person_id for p in self.knowledge.get_people()):
            raise ValueError("Choose a household person first.")
        if self.db.execute("SELECT 1 FROM accounts WHERE person_id=?", (person_id,)).fetchone():
            raise ValueError("This person is already connected. Disconnect their account before connecting another.")
        token, id = secrets.token_urlsafe(24), uuid.uuid4().hex
        self.db.execute("DELETE FROM invites WHERE person_id=? OR expires<?", (person_id, time.time()))
        expires = time.time() + 900
        self.db.execute("INSERT INTO invites(id,digest,person_id,expires) VALUES(?,?,?,?)",
                        (id, hashlib.sha256(token.encode()).hexdigest(), person_id, expires))
        self.db.commit()
        return {"id": id, "url": f"https://t.me/{config['username']}?start={token}", "expires": expires}

    def queue(self, chat_id, text, first_reply=False):
        parts = list(chunks(text))
        for index, part in enumerate(parts):
            completed_reply = first_reply and index == len(parts) - 1
            self.db.execute("INSERT INTO outbox(chat_id,text,first_reply) VALUES(?,?,?)", (chat_id, part, int(completed_reply)))

    def approve(self, id):
        row = self.db.execute("SELECT * FROM invites WHERE id=?", (id,)).fetchone()
        if not row or row["state"] != "pending" or row["expires"] < time.time():
            raise ValueError("This connection request expired or is no longer pending. Create a new invitation.")
        if not any(p["id"] == row["person_id"] for p in self.knowledge.get_people()):
            raise ValueError("This household person no longer exists.")
        try:
            self.db.execute("INSERT INTO accounts(user_id,chat_id,person_id,label,conversation_id) VALUES(?,?,?,?,?)",
                            (row["user_id"], row["chat_id"], row["person_id"], row["label"], f"telegram:{uuid.uuid4().hex}"))
        except sqlite3.IntegrityError:
            raise ValueError("This Telegram account or household person is already connected.") from None
        self.db.execute("DELETE FROM invites WHERE id=?", (id,))
        self.queue(row["chat_id"], WELCOME)
        self.db.commit()

    def revoke(self, user_id):
        rows = self.db.execute("SELECT chat_id FROM accounts WHERE user_id=? UNION SELECT chat_id FROM invites WHERE user_id=?", (user_id, user_id)).fetchall()
        for row in rows:
            self.db.execute("DELETE FROM outbox WHERE chat_id=?", (row["chat_id"],))
        self.db.execute("DELETE FROM inbox WHERE state='queued' AND json_extract(payload,'$.message.from.id')=?", (user_id,))
        self.db.execute("DELETE FROM accounts WHERE user_id=?", (user_id,))
        self.db.execute("DELETE FROM invites WHERE user_id=?", (user_id,))
        self.db.commit()

    async def handle(self, update):
        message = update.get("message", {})
        chat, sender = message.get("chat", {}), message.get("from", {})
        if chat.get("type") != "private" or not sender.get("id") or sender.get("is_bot"):
            return
        user_id, chat_id = sender["id"], chat["id"]
        text = message.get("text", "").strip()
        command, _, argument = text.partition(" ")
        command = command.split("@")[0]
        if command == "/start" and argument:
            digest = hashlib.sha256(argument.encode()).hexdigest()
            row = self.db.execute("SELECT * FROM invites WHERE digest=?", (digest,)).fetchone()
            if not row or row["state"] != "waiting" or row["expires"] < time.time():
                self.queue(chat_id, "This invitation expired or has already been used. Ask the household owner for a new invitation.")
                return
            if self.db.execute("SELECT 1 FROM accounts WHERE user_id=?", (user_id,)).fetchone():
                self.queue(chat_id, "You're already connected. Send a message, or /disconnect before changing profiles.")
                return
            label = ' '.join(str(sender.get(k, '')) for k in ('first_name', 'last_name')).strip()[:160]
            if sender.get('username'):
                label += ' (@' + str(sender['username'])[:64] + ')'
            self.db.execute("UPDATE invites SET state='pending',user_id=?,chat_id=?,label=? WHERE id=?",
                            (user_id, chat_id, label or str(user_id), row["id"]))
            self.queue(chat_id, f"Connection requested. Your Telegram account ID is {user_id}. Compare this ID with the request in Plata → Connections, or share it with the household owner so they can confirm your account.\n\n" + MEMORY_NOTICE + "\nIf this is not what you intended, send /disconnect to cancel.")
            return
        account = self.db.execute("SELECT * FROM accounts WHERE user_id=? AND chat_id=?", (user_id, chat_id)).fetchone()
        if command == "/disconnect":
            self.revoke(user_id)
            self.queue(chat_id, "Your Telegram connection and any pending request have been removed. Existing household memories have not been deleted.")
            return
        if not account:
            pending = self.db.execute("SELECT 1 FROM invites WHERE user_id=? AND state='pending' AND expires>?", (user_id, time.time())).fetchone()
            self.queue(chat_id, "Waiting for the household owner to confirm your account in Plata → Connections." if pending else
                       "You need a household invitation to connect to Plata. Ask the household owner to open Connections → Telegram.")
            return
        if command in {"/start", "/help"}:
            self.queue(chat_id, WELCOME)
        elif command == "/new":
            self.db.execute("UPDATE accounts SET conversation_id=? WHERE user_id=?", (f"telegram:{uuid.uuid4().hex}", user_id))
            self.queue(chat_id, "Fresh chat started. Shared household memory is still available. What would you like to talk about?")
        elif not text:
            self.queue(chat_id, "Please type a message. Voice, photos, and attachments aren't supported yet.")
        elif len(text) > 12000:
            self.queue(chat_id, "Please send a shorter message (up to 12,000 characters).")
        else:
            response = await self.router.route(ConversationRequest(
                text=text, conversation_id=account["conversation_id"], source="telegram",
                person_id=account["person_id"], language=sender.get("language_code", "en")))
            self.queue(chat_id, response.reply_text, first_reply=True)

    def ingest(self, updates):
        import json
        offset = self.config()["offset"]
        with self.db:
            for update in updates:
                if update["update_id"] < offset:
                    continue
                self.db.execute("INSERT OR IGNORE INTO inbox(update_id,payload) VALUES(?,?)",
                                (update["update_id"], json.dumps(update)))
                self.db.execute("UPDATE config SET offset=MAX(offset,?) WHERE id=1", (update["update_id"] + 1,))

    async def process(self):
        import json
        while row := self.db.execute("SELECT * FROM inbox WHERE state='queued' ORDER BY update_id LIMIT 1").fetchone():
            self.db.execute("UPDATE inbox SET state='processing' WHERE update_id=?", (row["update_id"],))
            self.db.commit()
            update = json.loads(row["payload"])
            try:
                await self.handle(update)
            except Exception:
                # Do not log exception text: third-party errors may contain credentials or private messages.
                message = update.get("message", {})
                chat = message.get("chat", {})
                if chat.get("type") == "private" and self.db.execute("SELECT 1 FROM accounts WHERE chat_id=?", (chat.get("id"),)).fetchone():
                    self.queue(chat["id"], "I couldn't finish that message. Please try again.")
                self.error = "A Telegram message could not be completed. The sender can retry."
            self.db.execute("UPDATE inbox SET state='done',payload='{}' WHERE update_id=?", (row["update_id"],))
            self.db.commit()
        # Updates older than the durable offset will not be requested again.
        self.db.execute("DELETE FROM inbox WHERE state='done' AND update_id < (SELECT offset-1000 FROM config WHERE id=1)")
        self.db.commit()

    async def flush(self):
        config = self.config()
        for row in self.db.execute("SELECT * FROM outbox ORDER BY id").fetchall():
            try:
                await self.api.call(config["token"], "sendMessage", chat_id=row["chat_id"], text=row["text"])
            except TelegramError as error:
                if not error.permanent:
                    raise
                self.db.execute("UPDATE accounts SET delivery_error=? WHERE chat_id=?", (str(error), row["chat_id"]))
            else:
                self.db.execute("UPDATE accounts SET delivery_error=NULL,first_reply=CASE WHEN ? THEN COALESCE(first_reply,?) ELSE first_reply END WHERE chat_id=?",
                                (row["first_reply"], time.time(), row["chat_id"]))
            self.db.execute("DELETE FROM outbox WHERE id=?", (row["id"],))
            self.db.commit()

    async def run(self):
        while True:
            try:
                async with self.lock:
                    await self.process()
                    await self.flush()
                    config = dict(self.config())
                updates = await self.api.call(config["token"], "getUpdates", offset=config["offset"], timeout=20, allowed_updates=["message"])
                async with self.lock:
                    self.ingest(updates)
                    self.last_poll = time.time()
                    self.error = None
            except TelegramError as error:
                self.error = str(error)
                await asyncio.sleep(error.retry_after)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.error = "Telegram connection interrupted. Retrying automatically."
                await asyncio.sleep(5)


class TokenRequest(BaseModel):
    token: SecretStr


class InviteRequest(BaseModel):
    person_id: str = Field(min_length=1, max_length=200)
    shared_memory_acknowledged: bool


async def private_workspace(request: Request):
    """Shared UI/API CSRF boundary; the trusted workspace network grants access."""
    if request.headers.get("x-plata-workspace") != "1":
        raise HTTPException(403, "Send X-Plata-Workspace: 1 from a client on the trusted Plata workspace network.")
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and urlsplit(origin).netloc != request.headers.get("host")):
        raise HTTPException(403, "Use the same-origin private Plata workspace.")


def telegram_router(service):
    router = APIRouter(prefix="/api/telegram", dependencies=[Depends(private_workspace)])

    @router.get("")
    async def status(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.status()

    @router.put("/bot")
    async def connect(body: TokenRequest):
        try:
            return await service.configure(body.token.get_secret_value().strip())
        except (ValueError, TelegramError) as error:
            raise HTTPException(400, str(error)) from None

    @router.delete("/bot")
    async def disconnect():
        await service.disconnect_bot()
        return {"ok": True}

    @router.post("/invitations")
    async def invite(body: InviteRequest, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if not body.shared_memory_acknowledged:
            raise HTTPException(422, "Acknowledge shared household memory before inviting someone.")
        async with service.lock:
            try:
                return service.invite(body.person_id)
            except ValueError as error:
                raise HTTPException(400, str(error)) from None

    @router.post("/invitations/{id}/approve")
    async def approve(id: str):
        async with service.lock:
            try:
                service.approve(id)
            except ValueError as error:
                raise HTTPException(400, str(error)) from None
        return {"ok": True}

    @router.delete("/invitations/{id}")
    async def cancel(id: str):
        async with service.lock:
            service.db.execute("DELETE FROM invites WHERE id=?", (id,))
            service.db.commit()
        return {"ok": True}

    @router.delete("/accounts/{user_id}")
    async def revoke(user_id: int):
        async with service.lock:
            service.revoke(user_id)
        return {"ok": True}

    @router.post("/qr")
    async def qr(body: dict):
        import qrcode
        import qrcode.image.svg
        url = str(body.get("url", ""))
        config = service.config()
        prefix = f"https://t.me/{config['username']}?start=" if config else ""
        if not prefix or not url.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", url[len(prefix):]):
            raise HTTPException(422, "Use a current Plata invitation link.")
        output = io.BytesIO()
        qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage).save(output)
        return Response(output.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    return router
