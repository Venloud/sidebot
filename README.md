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


## XP ranks and commands

SideBot awards 10 XP for an eligible server message, at most once per member each 60 seconds. Commands, bots, and very short messages do not earn points. Rank roles are automatically created on bot startup if SideBot has Manage Roles permission: Rank | NPC (0), Random (100), Side Character (350), Regular (800), Main Character (1600), Certified (3000), GOAT (5500), Himothy (10000). The bot changes only SideBot rank roles on promotions.

Use `bot rank`, `bot rank @member`, `bot leaderboard`, `bot ranks`, or `bot setupranks` (Manage Roles permission). Prefix aliases `bobot` and `sidebot` work for command invocations as well as `bot` and `side bot`, for example `bobot rank` or `sidebot leaderboard`.

XP data is stored locally in `SideBot/xp_scores.json` and saved through GitHub Actions cache. This is not durable storage: cache eviction, cancelled workflows, and interrupted jobs can lose scores. A database should replace the cache for production hosting.


## Automatic code updates during a GitHub Actions run

The workflow now launches `scripts/live_runner.py`. It checks the private repository's `main` branch every 60 seconds. When a new commit changes SideBot code, the supervisor validates the new Python syntax, stops the bot process, updates its checkout, and starts the bot again within the **same** Actions job. Non-code changes do not restart the bot. Changes to `requirements.txt` install dependencies before switching.

This is **not zero downtime**: Python must restart to load new definitions and Discord briefly disconnects. The GitHub Actions job itself remains active. The existing running job will not gain this capability until the next job starts with the updated workflow. The standard six-hour GitHub runner limit, scheduled gaps, and limitations of Actions cache remain. In-memory values reset on bot restart, while saved JSON state on disk remains for the new process.


## Visual rank cards

Use `bot rank` or `bot rank @member` (also `sidebot rank` and `bobot rank`). SideBot renders a PNG card featuring the member's Discord avatar, current rank, XP, server placement, and progress to the next tier. Pillow is installed via `requirements.txt`. On image-generation errors, it sends a readable text fallback.


## Moderation and access roles

SideBot moderation is permission-gated and respects Discord's role hierarchy. Available commands:

- `bot create_access_role Members #private-channel` creates a role with no global powers and grants it View Channel and Read Message History for that channel. An administrator must separately hide the channel from `@everyone` to make it private.
- `bot delete_role Role Name` deletes a non-administrative non-managed ordinary role, excluding SideBot rank roles.
- `bot kick @member reason` kicks a member. Caller and bot require Kick Members permission.
- `bot ban @member reason` bans a member without deleting message history. Caller and bot require Ban Members permission.
- `bot unban USER_ID` unbans a user by Discord numeric ID.

Moderation commands will not act on the server owner, administrators, members at or above the bot's highest role, or equal/higher-ranked members relative to a non-owner moderator. Assign the bot Manage Roles, Manage Channels, Kick Members, and Ban Members only when those features are needed. Avoid Administrator permission. Audit results in Discord's audit log. 


## Grouped create commands

Use `bot create` to see its available subcommands, also works with `sidebot create` and `bobot create`. Examples:

- `bot create channel gaming-talk` creates a public text channel (Manage Channels).
- `bot create thread Match Discussion` creates a public thread in the current text channel (Create Public Threads).
- `bot create role VIP` creates a role with no server-wide permissions (Manage Roles).
- `bot create poll Messi | Ronaldo | X` posts a reaction poll with 2 to 9 choices (Manage Messages).
- `bot create event Game Night | 2026-10-20 19:00 | 60 | #voice-channel` schedules a voice event at 19:00 UTC lasting 60 minutes (Manage Events). Mention an existing voice or stage channel.

The bot also needs the corresponding channel, role, thread, event, message, and reaction permissions. Existing `bot create_role` and `bot create_poll` commands remain available for compatibility. Restart the bot to apply the update.


## Flexible role permissions and community polls

Any server member can use `bot create poll Messi | Ronaldo | X` in a channel where SideBot can send messages and add reactions. A 30-second per-user cooldown limits spam.

Members with **Manage Roles** permission can create roles and specify a safe subset of permissions using `bot create role VIP | view_channel, send_messages, connect`. Without the `|` section the role has no server-wide permissions. Supported flags: `view_channel`, `send_messages`, `read_message_history`, `embed_links`, `attach_files`, `add_reactions`, `use_external_emojis`, `connect`, `speak`, `stream`, `use_voice_activation`, `create_public_threads`, and `send_messages_in_threads`. The command blocks Administrator and moderation permissions and disallows granting permissions the caller does not possess. Role permissions at the server level do not override explicit channel restrictions.


## Private day-one password access

- In the server, moderator: \`bot dayone setup #your-secret-channel\` creates the \`day-one\` role and restricts that text channel to it. SideBot requires Manage Channels and Manage Roles, with its role above \`day-one\`.
- In **SideBot DMs**, server administrator: \`bot dayone set SERVER_ID permanent YOUR_PASSWORD\` sets a permanent password. It remains valid until replaced.
- In **SideBot DMs**, server administrator: \`bot dayone set SERVER_ID temporary YOUR_PASSWORD\` sets a password that expires in 24 hours.
- In **SideBot DMs**, member: \`bot dayone verify SERVER_ID YOUR_PASSWORD\` verifies and receives the \`day-one\` role. Temporary passwords grant access for 24 hours, after which the bot removes the role at its next five-minute check. Permanent passwords grant access without expiry.
- \`bot dayone\` explains the commands. Passwords must be 8–128 characters and are stored only as salted PBKDF2 hashes in \`SideBot/dayone_access.json\`. Passwords entered in server channels are rejected, and the bot attempts to delete accidental public submissions. Do not post passwords publicly.
- The GitHub Actions runner restores and saves the day-one state via its \`.state\` cache. This cache is not a guaranteed persistent database; on cache loss, active temporary grants may require manual cleanup. The timer also only runs while the bot is online.


## Welcome channel automation

On startup, SideBot posts and pins one server guide in `#welcome` containing the 509 channel directory, rules, rank names, bot commands and day-one access information. It checks for an existing guide to avoid reposting on most restarts. Every six hours it deletes up to 250 unpinned messages older than three days from `#welcome` only, preserving other pinned posts and the bot's guide. Requires View Channel, Read Message History, Send Messages, and Manage Messages in `#welcome`. The normal new-member welcome post and onboarding DM still run.


## Clearing messages and rank-role monitoring

`bot clear 20` or `bot clean 20` deletes up to 20 recent unpinned messages in the invoking channel. Both require Manage Messages for the caller and SideBot; the bot reports the actual number removed. Allowed range is 1 to 100, default 50. SideBot also checks saved XP scores every 15 minutes and repairs members' earned ranking roles (NPC through Himothy) when missing or inconsistent; it does not automatically modify unrelated roles. Requires Manage Roles and appropriate role hierarchy.

## The 509 Control Center dashboard

The branded dashboard lives in `dashboard/index.html`, themed with green, black, white and brown inspired by the SideBot icon and banner. Workflow `.github/workflows/dashboard-pages.yml` deploys this static site to GitHub Pages when Pages is enabled under **Repository Settings → Pages → Build and deployment → GitHub Actions**. Availability and public access depend on the private repository's GitHub Pages plan/settings. Do not put API tokens in this website. Its **Restart controls** link opens the authenticated GitHub Actions page, where an admin can cancel an existing run and manually dispatch a replacement. It is **not** yet an API-powered one-click restart, and it does not show real-time private bot metrics. A secure backend with authentication is required for those capabilities.

## Free game announcements

SideBot checks the GamerPower API every two hours for active full-game giveaways and posts newly identified items to `#free-games`, with an attribution link to GamerPower.com. The first pass records active offers but does not post them, to avoid flooding the channel when first enabled. Already-announced IDs are stored in `SideBot/free_games_seen.json` and persisted with the GitHub Actions state cache. The source is a third-party tracker; announcements should be checked for conditions and availability. If the cache disappears or the bot is offline, notices may be missed or repeated. No API key required.
