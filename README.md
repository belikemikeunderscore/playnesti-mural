# PlayNESTI Discord Bot

PlayNESTI is a Discord bot for LAN party event management, media wall display, and VR expo notifications.

It includes:

- a media wall that collects image/video attachments from a specific Discord channel and publishes them to a local web slideshow
- a moderation dashboard for removing items from the wall
- a VR expo HTTP bridge that sends Discord DMs to waitlisted players
- a PlayNESTI LAN Party role manager with CSV import, role creation, and dashboard support

---

## Features

- `cogs.media_wall`: monitors a configured Discord channel, accepts only supported image/video attachments, stores media locally, and broadcasts updates to a live slideshow
- `web/server.py`: serves the media wall and moderation UI on `http://<host>:<port>`
- `cogs.vr_expo`: launches a local HTTP bridge at `http://127.0.0.1:6001` for external VR dashboard notifications
- `cogs.server_role_manager`: imports team data from CSV, creates Discord roles, assigns roles to members, and exposes a Flask dashboard

---

## Requirements

- Python 3.11+ (recommended)
- `discord.py`
- `aiohttp`

Optional for `server_role_manager`:

- `flask`
- `flask-cors`

---

## Installation

1. Clone the repository or download the project files.
2. Create and activate a Python virtual environment.

```powershell
cd C:\Users\mykee\Desktop\playnesti-discord-bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

3. Install dependencies.

```powershell
pip install -r requirements.txt
```

4. If you want to use the PlayNESTI dashboard cog, install Flask and Flask-CORS as well:

```powershell
pip install flask flask-cors
```

---

## Configuration

Create a `.env` file in the repository root and add your environment-specific values.

```dotenv
DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN
MEDIA_CHANNEL_ID=123456789012345678
LFT_ANNOUNCEMENT_CHANNEL_ID=123456789012345678
WEB_HOST=localhost
WEB_PORT=8080
MODERATION_PASSWORD=your_password_here
VR_BRIDGE_TOKEN=playnesti-vr-bridge
VR_BRIDGE_PORT=6001
```

Important:

- Replace `DISCORD_TOKEN` with your bot token.
- Set `MEDIA_CHANNEL_ID` to the channel ID where media submissions are accepted.
- Set `LFT_ANNOUNCEMENT_CHANNEL_ID` to the channel ID where LFT announcements should be posted.
- Change `MODERATION_PASSWORD` to secure the moderation interface.
- `VR_BRIDGE_TOKEN` and `VR_BRIDGE_PORT` are used by the VR Expo bridge.

> Tip: keep `.env` out of version control. A `.gitignore` file has been added to exclude `.env`, `.venv/`, and generated Python cache files.

---

## Running the Bot

Start the bot with:

```powershell
python main.py
```

The bot will:

- load the media wall, VR expo bridge, and server role manager cogs
- start the web app at `http://<WEB_HOST>:<WEB_PORT>`
- begin listening for Discord events

---

## Web Interfaces

### Media Wall

Open:

```text
http://<WEB_HOST>:<WEB_PORT>
```

This page shows the current media queue and plays submissions as a slideshow.

### Moderation Dashboard

Open:

```text
http://<WEB_HOST>:<WEB_PORT>/moderation
```

Log in using the password from `config.MODERATION_PASSWORD`, then remove unwanted media items from the wall.

---

## Media Wall Behavior

- Only attachments with supported extensions are accepted:
  - Images: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`
  - Videos: `.mp4`, `.mov`, `.webm`, `.avi`, `.mkv`
- Non-media messages in the configured channel are deleted automatically.
- Submitted media is stored in the repository `media/` folder and served through `/media`.
- Deleted Discord messages also remove the corresponding media item and file.

---

## VR Expo Bridge

The VR Expo bridge starts automatically with the bot and listens on `127.0.0.1:6001`.

Supported endpoints:

- `GET /health` — returns a simple status JSON
- `POST /notify` — sends a DM to a Discord user

Required request header:

```http
X-Bridge-Token: <VR_BRIDGE_TOKEN>
```

Request body example:

```json
{
  "discord": "Player#1234",
  "name": "Player",
  "minutes": 5
}
```

The bridge will resolve the Discord user and send a DM with the waitlist notification.

---

## PlayNESTI LAN Party Commands

All PlayNESTI commands are slash commands and require admin permission (`manage_roles`).

Available commands (use `/playnesti <command>`):

- `/playnesti carregar` — import team data from a CSV file (attach CSV to the message with the command)
- `/playnesti status` — show which participants are present or absent on the server
- `/playnesti criarcargos` — create team roles and automatically assign members based on imported data
- `/playnesti dashboard` — display the local web dashboard URL for managing roles and viewing team data
- `/playnesti limpar` — remove all roles created by the bot for this guild

> Note: These are modern Discord slash commands that only visible to and usable by users with the `manage_roles` permission (typically admins).

### CSV Support

The bot accepts flexible CSV formats including:

- one row per participant
- one row per team with semicolon-separated participants and Discord handles

Accepted columns (case-insensitive):

- `equipa`, `team`, `group`
- `jogo`, `game`
- `chefe`, `captain`
- `participante`, `nome`, `player`
- `discord`, `discord_handle`, `discord_tag`
- `is_chefe`, `chefe?`

---

## File Structure

- `main.py` — bot entry point and web server startup
- `config.py` — bot configuration values
- `requirements.txt` — Python dependencies
- `state.py` — shared application state for WebSocket clients and media items
- `cogs/`
  - `media_wall.py` — media channel listener and slideshow backend
  - `vr_expo.py` — local HTTP bridge for VR expo notifications
  - `server_role_manager.py` — PlayNESTI team/role manager and optional Flask dashboard
- `web/` — web application package
  - `server.py` — aiohttp app for media wall and moderation
  - `static/` — front-end files for the wall and moderation UI
- `media/` — saved media attachments

---

## Notes

- Keep `DISCORD_TOKEN` private and do not commit it to source control.
- If Flask is not installed, the PlayNESTI dashboard cog still works for role import and management, but the browser dashboard is disabled.
- The moderation page uses cookies and a password-based session token for access control.

---

## License

This repository does not include a license file by default. Add your own license if you plan to share or distribute the bot.
