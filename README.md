# Kdrama Heaven

Telegram file-store bot for a private database channel. Admins drop files in the bot, it stores them in the channel, and anyone with a `t.me` start link can receive a copy. Built for a single long-running container on Koyeb.

This is a new bot inspired by the usual Pyrogram file-store layout (single links, batch links, force-subscribe, auto-delete, broadcast, stats). It is not a copy of that code. Every secret and id comes from the environment. If a required variable is missing, the process prints the missing names and exits. Nothing in the repository is a usable token, API hash, or MongoDB URI.

## Features

- Store documents, video, audio, photos, and albums in a database channel, and index the file name for search.
- Share links for one post, a message range (`/batch`), a hand-picked set (`/cbatch`), or a protected batch (`/rbatch`).
- Optional expiry (`24h`, `7d`, `30m`) and a maximum number of opens on each link. `/delink` revokes a token.
- Paste a database-channel `t.me` link, or forward the post, and the bot returns a link without re-uploading.
- Several force-subscribe channels at once. Channels listed in `FORCE_SUB_REQUEST_CHANNELS` accept a pending join request as access, with optional auto-approve.
- Optional ad-link verification. A shortener URL and API key make non-premium users pass a timed check before files are sent.
- Premium users. Each bypass (force-subscribe, verification, auto-delete) can be turned off.
- Delivered files can delete themselves. A **Get again** button resends them while the link is still valid.
- Custom caption template, welcome text, welcome photo, force-subscribe text, and auto-reply, including overrides saved in MongoDB with `/settings`.
- Admin panel on inline buttons: stats, ban and unban, premium, settings, requests, broadcast, restart.
- Broadcast a replied-to message, with a live progress edit and an option to pin it.
- `/search` and `/anime` browse the index. `/request` files a drama request that admins can mark done or rejected.
- Per-user rate limit, Telegram flood-wait retries, uptime, and a restart command for the owner.
- HTTP health check on `$PORT` (default 8000) at `/` and `/health`, bound before the bot logs in, so a platform probe can pass while Telegram connects.

Run one instance only. A second replica would poll the same bot token and race the same updates.

## Commands

Paste this into BotFather (`/setcommands`). Admin commands are also registered on each admin's private chat with the bot the first time the process starts. The handlers still check permissions if someone types an admin command by hand.

```
start - Start the bot or open a file link
help - How to use this bot
id - Show your Telegram user id
me - Show your access status
search - Search stored files by name
anime - Browse the stored catalog
request - Request a drama or series
myrequests - Check your requests
uptime - Show uptime and response time
genlink - Create a link for one post
batch - Create a link for a range of posts
cbatch - Create a link for selected posts
rbatch - Create a protected batch link
delink - Revoke a file link
broadcast - Send a message to all users
stats - Show library and user statistics
users - Show how many users the bot has
ban - Ban a user
unban - Unban a user
premium - Grant or revoke premium
settings - Edit messages and toggles
panel - Open the admin panel
admins - List bot admins
requests - List open drama requests
restart - Restart the bot
cancel - Cancel the current action
```

`/ping` is the same as `/uptime`. `/catalog` is the same as `/anime`. `/admin` opens the same panel as `/panel`.

### Link limits

After `/genlink`, `/batch`, `/cbatch`, or `/rbatch` picks the posts, send `expiry uses`:

| Reply | Result |
| --- | --- |
| `0 0` or `/skip` | No expiry, unlimited opens |
| `24h 10` | Expires in 24 hours, 10 opens |
| `7d 0` | Expires in 7 days, unlimited opens |
| `30m 1` | Expires in 30 minutes, one open |

Durations use `m`, `h`, or `d`.

### Message placeholders

Welcome, force-subscribe, and auto-reply text:

`{first}` `{last}` `{id}` `{mention}` `{username}` `{uptime}`

Caption template:

`{filename}` `{previouscaption}` `{filesize}` `{filetype}`

HTML is on. A bad template falls back to the original caption or the raw welcome text.

## Environment variables

Set these in Koyeb (or a local `.env` loaded by your shell). `.env.example` lists the same names. Blank required variables are a startup error. Optional variables have the defaults below.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `API_ID` | yes | | Integer app id from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | yes | | App hash from my.telegram.org |
| `BOT_TOKEN` | yes | | BotFather token (`123456:ABC...`) |
| `DATABASE_URL` | yes | | `mongodb://` or `mongodb+srv://` URI. `DB_URL` and `MONGO_URL` also work |
| `OWNER_ID` | yes | | Your Telegram user id. Always an admin. Only this id can restart |
| `CHANNEL_ID` | yes | | Database channel id, like `-100...`. Bot must be admin and allowed to post |
| `DATABASE_NAME` | no | `kdrama` | Database name inside the cluster. `DB_NAME` also works |
| `PORT` | no | `8000` | Health-check port. Koyeb injects this |
| `WEB_HOST` | no | `0.0.0.0` | Health-check bind address |
| `WORKERS` | no | `8` | Pyrogram workers. `TG_BOT_WORKERS` also works |
| `LOG_CHANNEL` | no | `OWNER_ID` | Where startup and new-user notes go |
| `ADMINS` | no | owner only | Extra admin user ids, comma or space separated |
| `FORCE_SUB_CHANNELS` | no | empty | Comma-separated chat ids. Optional label: `-100123\|Updates`. `FORCE_SUB_CHANNEL` also works |
| `FORCE_SUB_REQUEST_CHANNELS` | no | empty | Ids that use join requests. A pending request counts as joined |
| `AUTO_APPROVE_JOIN` | no | `false` | Approve those join requests automatically |
| `FILE_AUTO_DELETE` | no | `600` | Seconds before delivered files disappear. `0` disables. Max 7 days |
| `PROTECT_CONTENT` | no | `false` | Block forward and save on delivered files |
| `DISABLE_CHANNEL_BUTTON` | no | `false` | Do not attach the Share button on database-channel posts |
| `START_MESSAGE` | no | built-in hello | Welcome HTML |
| `START_PIC` | no | empty | Image URL or Telegram file id for the welcome |
| `FORCE_SUB_MESSAGE` | no | built-in | Text shown with the join buttons |
| `CUSTOM_CAPTION` | no | empty | Caption template. Empty keeps the original caption |
| `AUTO_DELETE_MESSAGE` | no | built-in | Text under the Get again button |
| `USER_REPLY_TEXT` | no | empty | Auto-reply for ordinary private text. Empty disables it |
| `MAX_BATCH` | no | `50` | Largest batch (1–200) |
| `SHORTENER_URL` | no | empty | Shortener endpoint, or a template with `{api}` and `{url}` |
| `SHORTENER_API` | no | empty | Shortener API key |
| `SHORTENER_ENABLED` | no | on if both are set | Force the verification gate on or off |
| `VERIFY_TUTORIAL` | no | empty | URL for the "How verification works" button |
| `TOKEN_HOURS` | no | `12` | How long a successful verification lasts (1–168) |
| `PREMIUM_BYPASS_FORCESUB` | no | `true` | Premium skips force-subscribe |
| `PREMIUM_BYPASS_SHORTENER` | no | `true` | Premium skips link verification |
| `PREMIUM_BYPASS_AUTODELETE` | no | `true` | Premium files are not auto-deleted |
| `RATE_LIMIT` | no | `8` | File opens per user per window. Admins are exempt |
| `RATE_WINDOW` | no | `30` | Rate-limit window in seconds |
| `KEEP_ALIVE_URL` | no | empty | Optional URL to GET every 4 minutes. Leave empty on Koyeb |
| `NEW_USER_LOG` | no | `true` | Log the first `/start` from each user |

`/settings` writes message text and the toggles above into MongoDB and those values win over the environment until you change them again. Secrets are never edited from chat.

Boolean values accept `true`, `false`, `1`, `0`, `yes`, `no`, `on`, `off`.

## Deploy on Koyeb

1. Create a MongoDB database (Atlas is fine). In Network Access, allow Koyeb to connect. Atlas does not publish a stable IP list for this, so a cluster used by a small bot is usually opened to `0.0.0.0/0` with a strong user password. Copy the URI. It should start with `mongodb+srv://` or `mongodb://`.
2. In [@BotFather](https://t.me/BotFather), send `/newbot` and copy the token. Create an API id and hash at [my.telegram.org](https://my.telegram.org).
3. Create a private channel for storage. Add the bot as an administrator with **Post messages**. Copy the channel id (`-100...`). Tools such as [@userinfobot](https://t.me/userinfobot) show it when you forward a post from that channel.
4. For each force-subscribe channel, add the bot as an administrator with **Invite users via link**. Join-request channels also need permission to manage join requests. Put those ids in `FORCE_SUB_REQUEST_CHANNELS`.
5. This repository is the app. Koyeb builds whatever is on `main`.
6. In the Koyeb dashboard, choose **Create App**, then **Create Service**. Connect GitHub and select this repository, branch `main`.
7. Builder: **Dockerfile**. The image is `python:3.11-slim` and the start command is `python -m bot`. You do not need a separate start command unless you override it.
8. Instance size: the smallest paid or eco size is enough. Set the scale to **1 instance**. Do not turn on horizontal autoscaling.
9. Networking: expose port **8000**, protocol **HTTP**. Health check: protocol **HTTP**, path **`/health`**, port **8000**. A healthy process returns `200` and `{"status":"ok","bot":"starting"}` or `"bot":"ready"`. Both `/` and `/healthz` answer too.
10. Environment: add every required variable from the table, plus the optional ones you want. Use the same names as `.env.example`. Do not wrap values in quotes unless the quote marks are part of the value.
11. Deploy. Open **Runtime logs** and wait for `bot @YourBot is ready`. If the logs say `bot configuration is incomplete`, the health check will fail because the process exits before it can listen. Fix the named variables and redeploy.
12. Open the bot, send `/start`, then as the owner send a video or document. The reply is the public link. Opening that link in another account copies the file into the private chat.

`PORT` must match the exposed service port. The container reads `PORT` and binds `0.0.0.0`.

If the bot cannot see the database channel, the logs name `CHANNEL_ID` and exit after the health server has already started. Fix the admin rights and redeploy. Force-subscribe channels that fail are skipped and logged; the rest of the bot still runs.

## Run locally

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export API_ID=... API_HASH=... BOT_TOKEN=... DATABASE_URL=... OWNER_ID=... CHANNEL_ID=...
python -m bot
```

Then `curl -s localhost:8000/health`.

Checks that do not need Telegram:

```bash
python -m compileall -q bot tests
python tests/test_logic.py
```

## Notes for operators

- The database channel is the archive. Users never join it. Links only copy messages out of it.
- Forwards must keep the forward tag. A copy, or a forward with the sender hidden, has no channel message id. Pasting `https://t.me/c/<channel>/<message>` still works.
- `/broadcast` has to be a reply. The bot asks you to confirm, then edits that prompt with progress. **Send and pin** pins the copy in each private chat; pin failures are counted and do not stop the broadcast.
- `/premium add USER_ID` does not expire. `/premium add USER_ID 30` lasts 30 days. `/premium del USER_ID` removes it. `/premium list` shows the current list.
- `/ban USER_ID reason` blocks the private chat. Admins cannot be banned. `/unban USER_ID` reverses it.
- Restart is owner-only. Koyeb also restarts the container if the process exits, which is what `/restart` does after replacing the process.
- Kurigram (imported as `pyrogram`) is the Telegram library. `kuricrypt` is its crypto extension. The older `TgCrypto` package does not build against this fork, so it is not installed.
