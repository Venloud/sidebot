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


## Self promotion link memory
Members can post their YouTube channel, TikTok profile, Twitch channel, or Instagram profile URL in **#self-promote**, **#self-promo**, or **#self-promotion**. SideBot stores the links for that server and Discord user. Ask `bot what @member youtube`, `bot @member instagram`, or `bot @member socials`. Mention the member for best matching. Links saved after deployment are cached between GitHub Actions runs, but caches are not a durable database, and older posts are not imported automatically.


## Live notifications for community accounts

Create a text channel named `notifications`, `notification`, or `live-notifications` (falls back to `live`). When members share supported YouTube, Twitch, or TikTok profile URLs in `self-promote`, SideBot remembers their profile per server and checks whether they are streaming. Existing manually tracked Twitch and TikTok names still work. Alerts say `Hey, NAME is live on PLATFORM!` with the stream link. A notification is sent once per observed live session. Live checks occur every 10 minutes, with YouTube limited to hourly checks per saved channel.

**YouTube setup:** Enable YouTube Data API v3 in Google Cloud, then add the `YOUTUBE_API_KEY` repository secret under GitHub Actions. The bot uses official `channels.list` handle resolution and `search.list?eventType=live`; YouTube search calls have a daily quota, so many tracked channels can exceed it. If the API key is missing, YouTube notifications are skipped, without affecting Twitch or TikTok. Channel URLs using `/@handle` and `/channel/UC...` are supported.

**Caveats:** Live notifications start after new links are posted, historical channel messages are not automatically imported. GitHub Actions runners are intermittent; checks and notifications do not run when the runner is stopped, and cache eviction can lose saved profiles and alert state. TikTok web detection is unofficial and may be affected by site changes.
