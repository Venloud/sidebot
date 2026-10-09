# SideBot private GitHub setup

1. Create a **private** GitHub repository and upload the contents of this folder to its root. Do not upload the original source ZIP or its `.git` directory.
2. In the repo, open **Settings > Secrets and variables > Actions > New repository secret**. Add the required `DISCORD_TOKEN`. Also add `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `GROQ_API_KEY`, `DASHBOARD_PASSWORD`, and `SESSION_SECRET` if you use those features.
3. Open **Actions > SideBot Discord > Run workflow** to launch immediately. Scheduled runs use UTC at 00:00, 06:00, 12:00, and 18:00.
4. Make sure **Message Content**, **Server Members**, and **Presence** privileged intents are enabled in Discord Developer Portal for this bot.

**Limitations**: GitHub-hosted Actions are temporary, not a supported always-on bot host. Each run lasts up to about 5 hours 45 minutes, leaving gaps. Scheduled runs can be delayed or disabled by GitHub. State is restored/saved via Actions cache, which is not durable storage and can be evicted. An external database or an always-on VPS is required for reliable 24/7 hosting and persistent state. Flask's dashboard is not publicly accessible on GitHub-hosted runners. This workflow is suitable for testing, not dependable permanent hosting.

Never commit bot tokens or the original `.env`; if the uploaded archive contained an actual token, rotate it through the Discord Developer Portal.


## October 2026 maintenance update

- Use `bot ping` to check gateway latency and whether the live monitor is running.
- New members no longer automatically receive every assignable role. To assign a safe welcome role, set repository **Settings > Secrets and variables > Actions > Variables > New repository variable** named `WELCOME_ROLE_NAME` to the exact name of the role. Leave unset for onboarding without automatic roles.
- Live checks now occur every 5 minutes, with HTTP timeouts for Twitch calls.
- Discord reconnects no longer restart an already active monitoring task or Flask server.
- The workflow checks Python syntax before installing dependencies and starting the bot.

**Note:** This setup has not been validated by a successful live GitHub Actions run. GitHub hosted runners are not always-on hosting; a server or persistent service is required for reliable 24/7 operation.
