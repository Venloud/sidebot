# AGENTS.md — SideBot contributor instructions

This repository powers **SideBot** for **The 509**, a Haitian gaming/hangout Discord community.

## Product priorities

SideBot replaces overlapping third-party bots with one coherent 509-branded bot.

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

Current rank order and thresholds:
- NPC — 0 XP
- Random — 100 XP
- Side Character — 350 XP
- Regular — 800 XP
- Main Character — 1600 XP
- Certified — 3000 XP
- GOAT — 5500 XP
- Himothy — 10000 XP

Do not rename or rebalance these without explicit owner approval.

Current XP award rule: 10 XP per eligible server message, at most once every 60 seconds per member.

## Badge philosophy

Every member displays the universal **🇭🇹 The 509** badge in SideBot badge/profile output.

Special badges are intentionally rare and separate from rank progression. Do not create a large automatic accolade ladder.

## Secret Vault contract

Expected workflow:

/vault code
→ grant Secret Access
→ hidden voice channel becomes visible
→ member joins
→ member leaves/switches/disconnects
→ role is removed
→ code is required again

Unused access expires after 5 minutes.

Never log or store plaintext Vault passwords. Persist only salted password hashes.

## Resource links

Do not invent URLs.

Owner-approved resources are stored in SideBot/resource_links.json and can be edited by moderators. Only HTTPS URLs are accepted.

When adding a resource category, update RESOURCE_CATEGORIES, prefix registration, slash registration, help/list output, and README.

## Custom commands and AI routing

The bot includes restricted Dyno-style custom commands and normal SideBot commands.

When adding a new prefix command, also update _REGISTERED_COMMANDS. Otherwise a mention such as @SideBot command may fall through to the AI pipeline.

Prefer slash commands for member-facing features while retaining legacy prefixes for compatibility unless explicitly removed.

Do not add eval, shell execution, arbitrary bot-command execution, or unrestricted templating to custom commands.

## Discord permissions

Do not use Administrator permission.

Use only specific permissions needed by implemented features. The SideBot role must be above every role it creates, assigns, removes, or repairs.

## Privileged intents

The bot currently expects:
- Message Content
- Server Members
- Presence

These must be enabled in the Discord Developer Portal while the code depends on them.

## Persistent state

Runtime state includes live announcements, member profiles, social links, XP, day-one access, free-game IDs, resource links, custom commands, message counts, badges, and Vault configuration.

GitHub Actions cache is **not durable storage**.

For production, migrate runtime state to persistent SQLite/PostgreSQL or equivalent.

## Hosting

GitHub Actions is suitable for testing, not dependable 24/7 Discord hosting.

Production should use an always-on host/container/VPS with persistent storage. The Flask monitor respects PORT when provided.

## Security

Never commit bot tokens, API secrets, dashboard passwords, session secrets, or Vault passwords.

Avoid logging sensitive DM/password payloads.

## Before merging

1. Compile Python.
2. Verify command and slash-command registration.
3. Check for accidental duplicate event handlers.
4. Check role hierarchy assumptions.
5. Check Discord message-size limits.
6. Verify runtime state persistence/ignore rules.
7. Test affected flows in a real test server when possible.
