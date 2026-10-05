# Hosting guide (plain English)

**What "hosting" means here:** the bot is just a program. It only answers commands while it is *running*,
so for it to be online 24/7 it has to run on a computer that never turns off. You have three options.

The **dashboard** is an optional web page the bot serves itself. You don't need to host anything extra for it.

| Option | Cost | Online | Best for |
| --- | --- | --- | --- |
| A. Your own PC with Docker | free | only while your PC is on | trying it out, demos |
| B. Railway | paid cloud plan (check current pricing) | 24/7 | easiest "set it and forget it" |
| C. Fly.io | paid cloud plan (check current pricing) | 24/7 | if you like the command line |

---

## A. Run it on your own PC (start here)

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it.
2. In the project folder, copy `.env.example` to a new file called `.env` and fill in at least `DISCORD_TOKEN`
   (and `ANTHROPIC_API_KEY` if you want the AI features).
3. Open PowerShell in the project folder and run:

   ```powershell
   docker compose up -d --build
   ```

4. Open the dashboard at **http://localhost:8787**. Check the bot with `docker compose ps` (it should say `healthy`)
   and read its log with `docker compose logs -f`.
5. Stop it with `docker compose down`.

**Port 8787 already in use?** Put `DASHBOARD_PORT=9000` (any free number) in your `.env` and run
`docker compose up -d` again, then visit that port instead.

---

## B. Host it 24/7 on Railway

1. Push this project to GitHub (already done) and create an account at [railway.com](https://railway.com).
2. **New Project → Deploy from GitHub repo →** pick `Discord-bot`. Railway finds the `Dockerfile` by itself.
3. Open the service's **Variables** tab and add (never commit these to GitHub):
   - `DISCORD_TOKEN`
   - `ANTHROPIC_API_KEY` (optional)
   - `GUILD_ID` (optional, your test server's ID)
4. **Add a Volume** to the service and set its mount path to `/app/data`. Without it, XP, warnings, reminders and
   settings are wiped on every deploy.
5. Deploy. The **Deploy logs** should show `Logged in as ...`.

### Seeing the dashboard online (optional)

The dashboard is private by default. To open it from anywhere:

1. Add these variables: `DASHBOARD_HOST=0.0.0.0` and `DASHBOARD_TOKEN=` *a long random password you invent*.
   (The bot refuses to expose the dashboard without a token.) Railway provides the port itself.
2. In the service's **Settings → Networking**, click **Generate Domain**.
3. Visit `https://<your-domain>/?token=<your-token>`.

Anyone who has the token can see usernames and the leaderboard, so treat it like a password. If you skip these
steps the bot works fine; you just won't have the web page.

---

## C. Host it on Fly.io

From the project folder, with the [flyctl](https://fly.io/docs/flyctl/install/) tool installed and logged in:

```powershell
fly launch --no-deploy -c deploy/fly.toml
fly volumes create botdata --size 1
fly secrets set DISCORD_TOKEN=your_token ANTHROPIC_API_KEY=your_key
fly deploy -c deploy/fly.toml
```

The Fly setup runs the bot without a public dashboard.

---

## Good to know

- **Never put your bot token in GitHub.** `.env` is already ignored by git. If a token ever leaks, press *Reset Token*
  in the Discord Developer Portal.
- **Backups:** everything the bot remembers lives in the `data/` folder (`bot.db` plus the config JSON files).
  Copy that folder to back it up.
- **Updating:** push to GitHub and Railway redeploys automatically; locally run `docker compose up -d --build`.
