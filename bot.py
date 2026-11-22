"""
Dumb Discord bot with provider failover.

Setup:
  pip install discord.py openai python-dotenv
  fill in .env (next to this file)
  python bot.py

Also enable "Message Content Intent" in the Discord developer portal.
Providers without a key set are skipped automatically.
Model names change often, so edit PROVIDERS if one starts failing.
"""
import asyncio
import os
import random
import re
import sqlite3
import time
from collections import defaultdict, deque

import discord
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()


def _env(name, default=""):
    return os.getenv(name, default).strip()


MEMORY_LIMIT = int(_env("MEMORY_LIMIT", "21"))
TYPING_RANGE = (float(_env("TYPING_MIN", "6")), float(_env("TYPING_MAX", "9")))
COOLDOWN_SECONDS = int(_env("COOLDOWN_SECONDS", "120"))  # skip a provider this long after it fails
REQUEST_TIMEOUT = int(_env("REQUEST_TIMEOUT", "20"))

# ---------- limits (set any to 0 to disable) ----------
USER_LIMIT = int(_env("USER_LIMIT", "10"))          # pings per user per window
USER_WINDOW = int(_env("USER_WINDOW", "3600"))      # window in seconds
USER_COOLDOWN = float(_env("USER_COOLDOWN", "15"))  # min seconds between one user's pings
SERVER_LIMIT = int(_env("SERVER_LIMIT", "60"))      # pings per server per window
SERVER_WINDOW = int(_env("SERVER_WINDOW", "3600"))
# 3 slots for user IDs that bypass every limit
UNLIMITED_IDS = {
    int(v) for v in (_env(f"UNLIMITED_ID_{i}") for i in (1, 2, 3)) if v.isdigit()
}

user_hits = defaultdict(deque)
server_hits = defaultdict(deque)
last_ping = {}


def allowed(user_id: int, scope: str) -> bool:
    if user_id in UNLIMITED_IDS:
        return True
    now = time.time()
    uh, sh = user_hits[user_id], server_hits[scope]
    while uh and now - uh[0] > USER_WINDOW:
        uh.popleft()
    while sh and now - sh[0] > SERVER_WINDOW:
        sh.popleft()
    if USER_COOLDOWN and now - last_ping.get(user_id, 0) < USER_COOLDOWN:
        return False
    if USER_LIMIT and len(uh) >= USER_LIMIT:
        return False
    if SERVER_LIMIT and len(sh) >= SERVER_LIMIT:
        return False
    uh.append(now)
    sh.append(now)
    last_ping[user_id] = now
    return True


SYSTEM_PROMPT = (
    "You are a dumb, stupid, idiotic bot. You answer questions badly and "
    "confidently. You barely care what you answer, and whether it's true or "
    "false doesn't matter to you. Keep replies short (1-3 sentences), "
    "lowercase vibes, no helpful disclaimers, never break character."
)

# Providers come from .env: PROVIDER_ORDER lists names in priority order, and each
# name needs NAME_API_KEY, NAME_BASE_URL, NAME_MODEL. Incomplete ones are skipped.
clients = []  # (name, AsyncOpenAI, model)
for name in _env("PROVIDER_ORDER").split(","):
    name = name.strip()
    if not name:
        continue
    p = re.sub(r"\W", "_", name).upper()
    key, url, model = _env(f"{p}_API_KEY"), _env(f"{p}_BASE_URL"), _env(f"{p}_MODEL")
    if not (key and url and model) or "YOUR_" in url:
        continue
    clients.append((name, AsyncOpenAI(base_url=url, api_key=key, timeout=REQUEST_TIMEOUT), model))
cooldown_until = {}  # provider name -> unix time

# ---------- memory (sqlite) ----------
db = sqlite3.connect("memory.db")
db.execute(
    "CREATE TABLE IF NOT EXISTS mem ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, role TEXT, content TEXT)"
)
db.commit()


def remember(scope: str, role: str, content: str):
    db.execute("INSERT INTO mem (scope, role, content) VALUES (?,?,?)", (scope, role, content))
    # keep only the newest MEMORY_LIMIT rows per server (oldest gets deleted)
    db.execute(
        "DELETE FROM mem WHERE scope=? AND id NOT IN "
        "(SELECT id FROM mem WHERE scope=? ORDER BY id DESC LIMIT ?)",
        (scope, scope, MEMORY_LIMIT),
    )
    db.commit()


def recall(scope: str):
    rows = db.execute("SELECT role, content FROM mem WHERE scope=? ORDER BY id", (scope,)).fetchall()
    msgs = []
    for role, content in rows:
        if msgs and msgs[-1]["role"] == role:  # some providers need alternating roles
            msgs[-1]["content"] += "\n" + content
        else:
            msgs.append({"role": role, "content": content})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


# ---------- generation with failover ----------
async def generate(history):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    now = time.time()
    for name, client, model in clients:
        if cooldown_until.get(name, 0) > now:
            continue
        try:
            r = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=200, temperature=1.1
            )
            text = (r.choices[0].message.content or "").strip()
            if text:
                return text[:1900]
        except Exception as e:  # 429s, outages, bad keys, etc.
            print(f"[{name}] failed: {e!r}")
            cooldown_until[name] = time.time() + COOLDOWN_SECONDS
    return "my brain is broken rn. try again later"


# ---------- discord ----------
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


@bot.event
async def on_ready():
    print(f"logged in as {bot.user} with providers: {[c[0] for c in clients]}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or bot.user not in message.mentions:
        return

    text = re.sub(rf"<@!?{bot.user.id}>", "", message.content).strip() or "hi"
    scope = str(message.guild.id if message.guild else f"dm-{message.channel.id}")

    if not allowed(message.author.id, scope):
        try:  # react instead of replying so limited users can't spam the channel
            await message.add_reaction("\u23f3")
        except discord.HTTPException:
            pass
        return

    # memory = only the pinger's message and the bot's reply, nothing else
    remember(scope, "user", f"{message.author.display_name}: {text}")
    history = recall(scope)

    async with message.channel.typing():
        delay = random.uniform(*TYPING_RANGE)
        # generate while "typing" so the total wait is 6-9s, not 6-9s + API time
        reply, _ = await asyncio.gather(generate(history), asyncio.sleep(delay))

    remember(scope, "assistant", reply)
    await message.reply(reply, allowed_mentions=discord.AllowedMentions.none())


bot.run(_env("DISCORD_TOKEN"))
