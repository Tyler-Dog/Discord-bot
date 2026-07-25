# Python Discord Bot with Docker

This is a ready-to-run Discord bot built with `discord.py` and packaged for Docker.

## Features

- Slash commands
- Environment variable config
- Logging
- Dockerfile included
- Docker Compose included

## Commands

- `/ping`
- `/hello`
- `/helpme`
- `/serverinfo`
- `/userinfo`
- `/say`
- `/clear`

## 1) Create your Discord application

1. Go to the Discord Developer Portal.
2. Create a **New Application**.
3. Open the **Bot** tab and create a bot user.
4. Under **Privileged Gateway Intents**, you can leave **Message Content Intent** off for this project.
5. Copy your bot token.
6. In **OAuth2 > URL Generator**, select:
   - `bot`
   - `applications.commands`
7. In bot permissions, select at least:
   - Send Messages
   - Use Slash Commands
   - Manage Messages (only if you want `/clear` to work)
8. Open the generated invite URL and add the bot to your server.

## 2) Set your environment variables

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

Example `.env`:

```env
DISCORD_TOKEN=your_real_bot_token_here
GUILD_ID=123456789012345678
```

`GUILD_ID` is optional, but useful in development because guild command sync is faster than global sync.

## 3) Run locally without Docker

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows PowerShell
pip install -r requirements.txt
python bot.py
```

## 4) Run with Docker

Build and run:

```bash
docker compose up --build
```

Run in the background:

```bash
docker compose up -d --build
```

Stop it:

```bash
docker compose down
```

## 5) Build and run without Compose

```bash
docker build -t my-discord-bot .
docker run --env-file .env --name my-discord-bot my-discord-bot
```

## 6) Deploy idea

For a simple portfolio/demo setup, this project is enough to show:

- you can build a Discord bot in Python
- you know how to configure secrets with environment variables
- you can containerize the bot with Docker
- you can run the same app locally or inside a container

## Notes

- If `GUILD_ID` is set, commands sync to that server for quicker testing.
- If `GUILD_ID` is not set, the bot syncs commands globally, which can take longer to appear.
- Never commit your real `.env` file or token.
