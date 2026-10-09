# AGENTS.md — SideBot contributor instructions

This repository powers **SideBot** for **The 509**, a Haitian gaming/hangout Discord community.

## Product priorities

SideBot is intended to replace overlapping third-party bots with one coherent 509-branded bot.

Primary responsibilities:
1. Resource commands (/anime, /movies, /football, etc.)
2. XP, ranks, leaderboard and visual rank cards
3. 509 member badge and special accolades
4. Free-game notifications
5. One-time secret voice access
6. Self-promotion/social profile memory and live notifications
7. Moderation/server utilities actually used by the server

Do not rebuild features that already exist without checking SideBot/bot.py and README.md first.

## Rank system

Current rank order and thresholds are:

- NPC — 0 XP
- Random — 100 XP
- Side Character — 350 XP
- Regular — 800 XP
- Main Character — 1600 XP
- Certified — 3000 XP
- GOAT — 5500 XP
- Himothy — 10000 XP

Do not rename or rebalance these without explicit owner approval.

XP is server-specific. Current award rule is 10 XP per eligible message with a 60-second cooldown.

## Badge philosophy

Every member automatically displays the universal **🇭🇹 The 509** badge in SideBot badge/profile output.

Special badges are intentionally rare and separate from rank progression. Do not create a long automatic badge ladder.

## Secret Vault contract

Expected workflow:

/vault code
→ grant Secret Access
→ hidden voice channel becomes visible
→ user joins
→ user leaves/switches/disconnects
→ role is removed
→ code is required again

Access that is granted but unused must expire after 5 minutes.

Never log or store plaintext Vault passwords. Only salted hashes may be persisted.

## Resource links

Do not invent links.

Owner-approved resources are stored in SideBot/resource_links.json and editable by moderators through SideBot commands. Only HTTPS URLs are accepted.

When adding a new resource category, update RESOURCE_CATEGORIES, prefix and slash registration, help/list output, welcome guide if relevant, and README.

## Commands and AI routing

The bot has both prefix/chat routing and slash commands.

When adding a new prefix command, also update _REGISTERED_COMMANDS. Otherwise a message mentioning SideBot with the new command may fall through to the AI pipeline.

Prefer slash commands for member-facing features. Keep legacy prefix commands for compatibility unless explicitly removing them.

## Discord permissions

Do not use Administrator permission.

SideBot may need specific permissions such as View/Send Messages, Embed Links, Attach Files, Read Message History, Manage Messages, Manage Roles, Manage Channels, Kick/Ban Members, Connect/Speak, Create Public Threads, Send in Threads, and Manage Events.

The SideBot Discord role must remain above any role it creates, assigns, removes or repairs.

## Privileged intents

The bot currently expects these Developer Portal intents:
- Message Content
- Server Members
- Presence

If code no longer needs an intent, remove it deliberately rather than disabling it only in the portal.

## Persistent state

Current JSON state includes announced live sessions, member profiles, social links, XP, day-one access, free-game seen IDs, resource links, message counts, badges, and Vault configuration.

GitHub Actions cache is **not durable storage**.

For production, migrate runtime state to SQLite/PostgreSQL or another persistent store before treating GitHub Actions as replaceable infrastructure.

## Hosting

GitHub Actions is for testing, not dependable 24/7 hosting.

Production should use an always-on host/container/VPS with persistent storage.

The Flask monitor respects PORT when set.

## Security

Never commit Discord bot tokens, API secrets, dashboard passwords, session secrets, or Vault passwords. Use environment variables/secrets.

Avoid logging entire sensitive request payloads. Be particularly careful around DMs and password/access commands.

## Git workflow

Prefer a feature branch + PR for meaningful changes.

Before merging:
1. Compile Python.
2. Verify command registration.
3. Confirm no duplicate event handlers accidentally replace each other.
4. Check role hierarchy assumptions.
5. Check Discord message lengths.
6. Verify runtime state files are persisted/ignored correctly.
7. Test the affected flow in a real test server when possible.
