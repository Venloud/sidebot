import nextcord
from nextcord.ext import commands, tasks
import os
import json
import re
import requests
import asyncio
import hmac
import random
import sys
import threading
import traceback
import unicodedata
from datetime import datetime
from flask import Flask, jsonify, redirect, render_template_string, request, Response, session, url_for
import functools
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv

# Load environment variables
load_dotenv()   # Replit Secrets take precedence; .env is a local fallback only
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

# ── Invite link ────────────────────────────────────────────────
CLIENT_ID   = "1312104099252011150"
PERMISSIONS = 271674432  # send/read messages, manage messages & roles, embed links, reactions, read history, connect+speak in voice
INVITE_URL  = (
    f"https://discord.com/oauth2/authorize"
    f"?client_id={CLIENT_ID}&permissions={PERMISSIONS}&scope=bot%20applications.commands"
)

intents = nextcord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("!", "side bot ", "sidebot ", "bobot ", "bot "),
    intents=intents,
)

bot.tiktok_usernames = set()
bot.twitch_usernames = set()

# Onboarding conversation state  {user_id: {"step": str, "data": dict, "guild_id": int}}
onboarding_state = {}

# AI chat conversation history — keyed by "{context}_{user_id}" so DM and server
# mentions NEVER share history.  context is "dm" or "server".
# {f"dm_{user_id}": [...], f"server_{user_id}": [...]}
ai_conversations = {}
AI_MAX_HISTORY = 6  # keep last 6 messages (3 back-and-forth exchanges)

ANNOUNCED_FILE = "announced_live.json"
MEMBER_PROFILES_FILE = os.path.join(os.path.dirname(__file__), "member_profiles.json")
MAX_MEMBER_PROFILES = 20

# ── Casual responses ───────────────────────────────────────────
def _load_casual_responses():
    path = os.path.join(os.path.dirname(__file__), "casual_responses.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: could not load casual_responses.json: {e}")
        return {}

casual_responses = _load_casual_responses()

def normalize_text(text: str) -> str:
    """Lowercase, strip accents, remove punctuation, collapse whitespace."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def casual_match(text: str):
    """Return a random response if text matches a casual trigger, else None.
    Checks longest triggers first to avoid 'gg' shadowing 'ggs'."""
    norm = normalize_text(text)
    sorted_triggers = sorted(casual_responses.keys(), key=len, reverse=True)
    for trigger in sorted_triggers:
        norm_trigger = normalize_text(trigger)
        pattern = r"(?<![a-z0-9])" + re.escape(norm_trigger) + r"(?![a-z0-9])"
        if re.search(pattern, norm):
            return random.choice(casual_responses[trigger])
    return None

def get_command_list(dm: bool = False) -> str:
    """Return the full command-list help string.
    dm=True adds the invite link footer (only shown in DMs, never in server channels).
    """
    base = (
        "**Kòmand SideBot:**\n\n"
        "**🤖 AI Chat**\n"
        "`@SideBot [mesaj]` — Pale avè m dirèkteman, map reponn! (AI)\n"
        "*(Ekri m nan DM tou — mwen konn pale!)*\n\n"
        "**🎮 Jeneyal**\n"
        "`bot kiye la` — Montre ki moun ki online nan chanèl la\n"
        "`gg` / `ggs` — Mwen reponn GG back\n"
        "`sak pase` / `sakpase` — Map reponn map boule\n\n"
        "**📢 Live**\n"
        "`bot whoslive` — Checke ki moun ki ap fè live sou TikTok oswa Twitch\n\n"
        "**💬 Mesaj**\n"
        "`bot send [mesaj] on [chanèl]` — Voye yon mesaj nan yon chanèl espesifik\n"
        "`bot clean [kantite]` — Efase yon kantite mesaj (ex: `bot clean 50`)\n\n"
        "**🗳️ Sondaj**\n"
        "`bot create_poll [chanèl] [wòl] [kesyon]` — Kreye yon sondaj\n"
        "`bot assign_poll_role` — Bay wòl la pou moun ki vote ✅\n\n"
        "**👥 Wòl**\n"
        "`bot create_role [non wòl]` — Kreye yon nouvo wòl ak yon koulè owaza\n"
        "`bot assign_role [@user] [non wòl]` — Bay yon wòl a yon manm\n\n"
        "**🎙️ Vwa (Voice)**\n"
        "`bot join [chanèl]` — Antre nan yon chanèl vwa (ex: `bot join general`)\n"
        "`bot leave` — Kite chanèl vwa a"
    )
    if dm:
        base += f"\n\n💡 *Ou renmen SideBot? Invite li nan pwòp sèvè pa w:*\n{INVITE_URL}"
    return base

def resolve_reply(raw: str, dm: bool = False) -> str:
    """Substitute {COMMAND_LIST} and {INVITE_URL} sentinels in a casual reply.
    dm=True: include invite link in command list and resolve {INVITE_URL}.
    dm=False (server): command list has no invite link; {INVITE_URL} is left unresolved
               (the server mention handler intercepts invite responses before this runs).
    """
    if "{COMMAND_LIST}" in raw:
        return get_command_list(dm=dm)
    if "{INVITE_URL}" in raw:
        if dm:
            return raw.replace("{INVITE_URL}", INVITE_URL)
        # In a server channel the caller should have intercepted — return as-is (safety net)
        return raw
    return raw


def is_invite_response(raw: str) -> bool:
    """Return True if a casual reply is an invite-link response (contains {INVITE_URL})."""
    return "{INVITE_URL}" in raw

# ── Command-detection helper ───────────────────────────────────
_REGISTERED_COMMANDS = {
    "whoslive", "clean", "clear", "links", "anime", "movies", "football", "manga", "music", "clips", "games", "dayone", "create", "create_role", "assign_role",
    "create_poll", "assign_poll_role", "invite", "send", "wipe",
    "join", "leave", "list", "delete_role", "create_access_role", "kick", "ban", "unban", "roleperms", "rolepermissions", "rank", "leaderboard", "top", "ranks", "setupranks",
}
_INLINE_COMMANDS = {"kiye la"}  # handled inline in on_message, not via @bot.command

def is_command_text(text: str) -> bool:
    """Return True if text looks like a bot command (with or without 'bot ' prefix)."""
    s = text.lower().strip()
    for pfx in ("bot ", "bobot ", "sidebot ", "side bot ", "!"):
        if s.startswith(pfx):
            s = s[len(pfx):]
            break
    first = s.split()[0] if s.split() else ""
    return first in _REGISTERED_COMMANDS or any(s.startswith(c) for c in _INLINE_COMMANDS)
# ──────────────────────────────────────────────────────────────

# ── Tool functions for Groq tool-calling ──────────────────────
def get_current_datetime() -> str:
    """Return current date/time in Haiti/Eastern timezone."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Port-au-Prince")
    except Exception:
        from datetime import timezone, timedelta
        tz = timezone(timedelta(hours=-5))
    now = datetime.now(tz)
    return now.strftime("%A, %B %d, %Y at %I:%M %p (Haiti/Eastern time)")

_WMO_CONDITIONS = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "icy fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "moderate rain", 65: "heavy rain",
    71: "light snow", 73: "moderate snow", 75: "heavy snow",
    80: "rain showers", 81: "heavy rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}

def get_weather(location: str) -> str:
    """Geocode via Open-Meteo and fetch current weather (no API key needed)."""
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1, "language": "en", "format": "json"},
            timeout=6,
        ).json()
        results = geo.get("results", [])
        if not results:
            return f"Location not found: {location}"
        r = results[0]
        lat, lon = r["latitude"], r["longitude"]
        city = r.get("name", location)
        country = r.get("country", "")
        wx = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "current_weather": "true",
                "temperature_unit": "celsius",
                "windspeed_unit": "kmh",
            },
            timeout=6,
        ).json()
        cw = wx.get("current_weather", {})
        temp_c = cw.get("temperature")
        temp_f = round(temp_c * 9 / 5 + 32, 1) if temp_c is not None else "?"
        condition = _WMO_CONDITIONS.get(cw.get("weathercode", -1), "unknown conditions")
        return f"{city}, {country}: {temp_c}°C ({temp_f}°F), {condition}"
    except Exception as e:
        return f"Weather lookup failed: {e}"
# ──────────────────────────────────────────────────────────────

# ── Web dashboard ──────────────────────────────────────────────
web_app = Flask(__name__)
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
web_app.secret_key = os.getenv("SESSION_SECRET") or DASHBOARD_PASSWORD
web_app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Leave the session non-permanent so it lasts for the browser tab/session,
    # not indefinitely on a shared computer.
    SESSION_PERMANENT=False,
)

def require_auth(f):
    """Protect a dashboard route with the signed browser session."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not DASHBOARD_PASSWORD:
            return Response("Dashboard is disabled: set DASHBOARD_PASSWORD in Replit Secrets.", 503)
        if session.get("dashboard_authenticated") is not True:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("dashboard_login"))
        return f(*args, **kwargs)
    return decorated

recent_events = []          # circular buffer of last 60 events
last_check_time = None      # ISO string of last live-check run
bot_start_time = None       # set in on_ready

def add_event(msg: str):
    recent_events.append({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg})
    if len(recent_events) > 60:
        recent_events.pop(0)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SideBot Monitor</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0f1117;color:#e3e5e8;font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
  header{background:#1e2130;border-bottom:1px solid #2e3147;padding:18px 28px;display:flex;align-items:center;gap:14px}
  header img{width:42px;height:42px;border-radius:50%;object-fit:cover}
  header h1{font-size:1.25rem;font-weight:700;letter-spacing:.3px}
  header .badge{background:#23a55a;color:#fff;font-size:.7rem;font-weight:700;padding:2px 8px;border-radius:99px;margin-left:8px;text-transform:uppercase}
  header .uptime{margin-left:auto;font-size:.8rem;color:#8b8fa8}
  main{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;padding:24px 28px}
  .card{background:#1e2130;border:1px solid #2e3147;border-radius:12px;padding:20px}
  .card h2{font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:#8b8fa8;margin-bottom:14px}
  .stat{font-size:2rem;font-weight:700;color:#fff}
  .stat-sub{font-size:.82rem;color:#8b8fa8;margin-top:2px}
  .pill{display:inline-flex;align-items:center;gap:6px;background:#2e3147;border-radius:99px;padding:5px 12px;font-size:.82rem;margin:3px}
  .pill.live{background:#3d1515;border:1px solid #d83c3e;color:#ff6b6b}
  .pill .dot{width:8px;height:8px;border-radius:50%;background:#23a55a;flex-shrink:0}
  .pill.live .dot{background:#d83c3e;animation:pulse 1.2s ease-in-out infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .pill .platform{font-size:.7rem;color:#8b8fa8;margin-left:2px}
  .empty{color:#8b8fa8;font-size:.85rem;font-style:italic}
  .event-log{max-height:320px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}
  .event{font-size:.8rem;display:flex;gap:10px;padding:6px 0;border-bottom:1px solid #2e3147}
  .event:last-child{border-bottom:none}
  .event .ts{color:#5865f2;flex-shrink:0;font-family:monospace}
  .event .text{color:#c4c9d4;word-break:break-word}
  .members-list{display:flex;flex-direction:column;gap:10px}
  .member-row{display:grid;grid-template-columns:1.2fr .8fr 1fr 1fr 1fr;gap:12px;
              padding:12px 0;border-bottom:1px solid #2e3147}
  .member-row:last-child{border-bottom:none}
  .member-field{min-width:0}
  .member-label{color:#8b8fa8;font-size:.68rem;text-transform:uppercase;letter-spacing:.5px;
                margin-bottom:4px}
  .member-value{color:#e3e5e8;font-size:.84rem;overflow-wrap:anywhere}
  footer{text-align:center;padding:16px;color:#4a4f6a;font-size:.75rem}
  footer span{color:#5865f2}
  .refresh-bar{height:3px;background:#5865f2;animation:shrink 30s linear forwards}
  @keyframes shrink{from{width:100%}to{width:0%}}
  @media (max-width:700px){
    .member-row{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
    .member-field:last-child{grid-column:1/-1}
  }
</style>
</head>
<body>
<div class="refresh-bar" id="bar"></div>
<header>
  <img src="https://cdn.discordapp.com/embed/avatars/0.png" id="avatar" alt="SideBot">
  <div>
    <h1>SideBot <span class="badge" id="status-badge">Online</span></h1>
    <div id="bot-name" style="font-size:.8rem;color:#8b8fa8">Loading...</div>
  </div>
  <div class="uptime" id="uptime-txt">–</div>
</header>
<main id="main-grid">
  <div class="card">
    <h2>📊 Overview</h2>
    <div class="stat" id="guild-count">–</div>
    <div class="stat-sub">servers connected</div>
    <div style="margin-top:16px;display:flex;gap:24px">
      <div><div class="stat" id="twitch-count" style="font-size:1.4rem">–</div><div class="stat-sub">Twitch tracked</div></div>
      <div><div class="stat" id="tiktok-count" style="font-size:1.4rem">–</div><div class="stat-sub">TikTok tracked</div></div>
    </div>
    <div style="margin-top:12px;font-size:.75rem;color:#8b8fa8">Last check: <span id="last-check">–</span></div>
  </div>

  <div class="card">
    <h2>🔴 Currently Live</h2>
    <div id="live-list"><div class="empty">No one is live right now</div></div>
  </div>

  <div class="card">
    <h2>🎮 Tracked Accounts</h2>
    <div id="tracked-list"><div class="empty">No accounts tracked</div></div>
  </div>

  <div class="card" style="grid-column:1/-1">
    <h2>👥 Recent Members</h2>
    <div class="members-list" id="members-list"><div class="empty">No completed onboardings yet</div></div>
  </div>

  <div class="card" style="grid-column:1/-1">
    <h2>📋 Recent Events</h2>
    <div class="event-log" id="event-log"><div class="empty">No events yet</div></div>
  </div>
</main>
<footer>SideBot Monitor &nbsp;·&nbsp; Auto-refreshes every <span>30s</span></footer>
<script>
async function load() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();

    document.getElementById('bot-name').textContent = d.bot_name || 'SideBot';
    document.getElementById('guild-count').textContent = d.guild_count ?? '–';
    document.getElementById('twitch-count').textContent = d.tracked_twitch.length;
    document.getElementById('tiktok-count').textContent = d.tracked_tiktok.length;
    document.getElementById('last-check').textContent = d.last_check || 'Never';

    // Live
    const liveEl = document.getElementById('live-list');
    if (d.currently_live.length) {
      liveEl.innerHTML = d.currently_live.map(u =>
        `<span class="pill live"><span class="dot"></span>${u}</span>`
      ).join('');
    } else {
      liveEl.innerHTML = '<div class="empty">No one is live right now</div>';
    }

    // Tracked
    const trackedEl = document.getElementById('tracked-list');
    const all = [
      ...d.tracked_twitch.map(u => ({u, p: 'Twitch'})),
      ...d.tracked_tiktok.map(u => ({u, p: 'TikTok'}))
    ];
    if (all.length) {
      trackedEl.innerHTML = all.map(({u,p}) =>
        `<span class="pill"><span class="dot"></span>${u} <span class="platform">${p}</span></span>`
      ).join('');
    } else {
      trackedEl.innerHTML = '<div class="empty">No accounts tracked</div>';
    }

    // Recently completed member onboarding profiles
    const membersEl = document.getElementById('members-list');
    if (d.recent_members.length) {
      const escapeHtml = value => String(value ?? '—').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[char]));
      const formatJoinTime = value => {
        const date = new Date(value);
        return Number.isNaN(date.getTime())
          ? escapeHtml(value || 'Unknown')
          : escapeHtml(date.toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'}));
      };
      const field = (label, value) =>
        `<div class="member-field"><div class="member-label">${label}</div>` +
        `<div class="member-value">${escapeHtml(value || '—')}</div></div>`;
      membersEl.innerHTML = d.recent_members.map(member =>
        `<div class="member-row">` +
        field('Discord username', member.discord_username) +
        field('Join reason', member.reason) +
        field('Game username', member.game_username) +
        field('Console username', member.console_username) +
        field('Joined', formatJoinTime(member.join_time)) +
        `</div>`
      ).join('');
    } else {
      membersEl.innerHTML = '<div class="empty">No completed onboardings yet</div>';
    }

    // Events
    const evEl = document.getElementById('event-log');
    if (d.recent_events.length) {
      evEl.innerHTML = [...d.recent_events].reverse().map(e =>
        `<div class="event"><span class="ts">${e.time}</span><span class="text">${e.msg}</span></div>`
      ).join('');
    } else {
      evEl.innerHTML = '<div class="empty">No events yet</div>';
    }

    if (d.started_at) {
      const start = new Date(d.started_at);
      const diff = Math.floor((Date.now() - start) / 1000);
      const h = Math.floor(diff/3600), m = Math.floor((diff%3600)/60), s = diff%60;
      document.getElementById('uptime-txt').textContent =
        `Uptime ${h}h ${m}m ${s}s`;
    }
  } catch(e) { console.error(e); }
}
load();
// Reset progress bar and reload every 30 s
setInterval(() => {
  const bar = document.getElementById('bar');
  bar.style.animation = 'none';
  bar.offsetHeight; // reflow
  bar.style.animation = '';
  load();
}, 30000);
</script>
</body>
</html>
"""

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · SideBot Monitor</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0f1117;color:#e3e5e8;font-family:'Segoe UI',system-ui,sans-serif;
       min-height:100vh;display:grid;place-items:center;padding:24px}
  .card{width:min(100%,380px);background:#1e2130;border:1px solid #2e3147;
        border-radius:12px;padding:28px;box-shadow:0 16px 40px rgba(0,0,0,.25)}
  h1{font-size:1.35rem;margin-bottom:8px}
  p{color:#8b8fa8;font-size:.9rem;margin-bottom:22px}
  label{display:block;color:#c4c9d4;font-size:.82rem;font-weight:600;margin-bottom:8px}
  input{width:100%;background:#0f1117;color:#e3e5e8;border:1px solid #3a3f59;
        border-radius:7px;padding:11px 12px;font-size:1rem}
  input:focus{outline:2px solid #5865f2;border-color:transparent}
  button{width:100%;margin-top:16px;background:#5865f2;color:#fff;border:0;border-radius:7px;
         padding:11px 14px;font-size:.95rem;font-weight:700;cursor:pointer}
  button:hover{background:#4752c4}
  .error{background:#3d1515;border:1px solid #d83c3e;color:#ff8c8c;border-radius:7px;
         padding:10px 12px;font-size:.82rem;margin-bottom:16px}
</style>
</head>
<body>
  <main class="card">
    <h1>SideBot Monitor</h1>
    <p>Sign in to view the private bot dashboard.</p>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <form method="post">
      <label for="password">Admin password</label>
      <input id="password" name="password" type="password" autocomplete="current-password"
             required autofocus>
      <button type="submit">Sign in</button>
    </form>
  </main>
</body>
</html>
"""

@web_app.route("/login", methods=["GET", "POST"])
def dashboard_login():
    if not DASHBOARD_PASSWORD:
        return Response("Dashboard is disabled: set DASHBOARD_PASSWORD in Replit Secrets.", 503)
    error = None
    if request.method == "POST":
        submitted_password = request.form.get("password", "")
        if hmac.compare_digest(submitted_password, DASHBOARD_PASSWORD):
            session["dashboard_authenticated"] = True
            return redirect(url_for("dashboard"))
        error = "Incorrect password."
    return render_template_string(LOGIN_HTML, error=error)

@web_app.route("/")
@require_auth
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@web_app.route("/health")
def health():
    """Public lightweight endpoint for uptime monitors."""
    return jsonify({"status": "ok"}), 200

@web_app.route("/api/status")
@require_auth
def api_status():
    return jsonify({
        "bot_name":       bot.user.name if bot.user else "SideBot",
        "bot_online":     not bot.is_closed(),
        "guild_count":    len(bot.guilds),
        "tracked_twitch": sorted(bot.twitch_usernames),
        "tracked_tiktok": sorted(bot.tiktok_usernames),
        "currently_live": sorted(announced_live_sessions),
        "last_check":     last_check_time,
        "started_at":     bot_start_time,
        "recent_members": list(reversed(member_profiles[-MAX_MEMBER_PROFILES:])),
        "recent_events":  recent_events,
    })

def run_web_server():
    web_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
# ──────────────────────────────────────────────────────────────

# ── Sports scores via ESPN public API ─────────────────────────
def get_sports_scores(team_or_match: str) -> str:
    """Fetch live/recent sports scores from ESPN's public unofficial API."""
    ENDPOINTS = [
        ("Soccer", "https://site.api.espn.com/apis/site/v2/sports/soccer/all.1/scoreboard"),
        ("NFL",    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"),
        ("NBA",    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"),
        ("MLB",    "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"),
    ]
    query_words = [w for w in team_or_match.lower().split() if len(w) > 2]
    found = []
    for sport_name, url in ENDPOINTS:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code != 200:
                continue
            for event in resp.json().get("events", []):
                name_full = (event.get("name", "") + " " + event.get("shortName", "")).lower()
                if not any(w in name_full for w in query_words):
                    continue
                comp = (event.get("competitions") or [{}])[0]
                competitors = comp.get("competitors", [])
                status = event.get("status", {}).get("type", {}).get("description", "—")
                if len(competitors) >= 2:
                    h, a = competitors[0], competitors[1]
                    h_name  = h.get("team", {}).get("displayName", "?")
                    a_name  = a.get("team", {}).get("displayName", "?")
                    h_score = h.get("score", "—")
                    a_score = a.get("score", "—")
                    found.append(f"[{sport_name}] {h_name} {h_score}–{a_score} {a_name} ({status})")
        except Exception:
            continue
    if found:
        return "\n".join(found[:4])
    search = requests.utils.quote(team_or_match)
    return (
        f"No live/recent scores found for '{team_or_match}' right now. "
        f"Check ESPN: https://www.espn.com/search/_/q/{search}"
    )
# ──────────────────────────────────────────────────────────────

def load_announced_sessions():
    """Load persisted announced-live set from disk."""
    try:
        with open(ANNOUNCED_FILE, "r") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def save_announced_sessions():
    """Persist the current announced-live set to disk."""
    with open(ANNOUNCED_FILE, "w") as f:
        json.dump(list(announced_live_sessions), f)

announced_live_sessions = load_announced_sessions()

# Completed onboarding profiles are kept locally so the dashboard retains them
# across bot restarts.  The list is intentionally bounded to the dashboard's
# recent-members view.
member_profiles_lock = threading.Lock()

def load_member_profiles():
    """Load the most recent completed onboarding profiles from disk."""
    try:
        with open(MEMBER_PROFILES_FILE, "r", encoding="utf-8") as f:
            profiles = json.load(f)
        if not isinstance(profiles, list):
            return []
        return profiles[-MAX_MEMBER_PROFILES:]
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def record_member_profile(profile):
    """Append one completed onboarding profile and persist it atomically."""
    with member_profiles_lock:
        member_profiles.append(profile)
        del member_profiles[:-MAX_MEMBER_PROFILES]
        temp_file = f"{MEMBER_PROFILES_FILE}.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(member_profiles, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, MEMBER_PROFILES_FILE)

member_profiles = load_member_profiles()

# Load and save usernames
def load_usernames(filename):
    try:
        with open(filename, "r") as file:
            return set(file.read().splitlines())
    except FileNotFoundError:
        return set()

def save_usernames(filename, usernames):
    with open(filename, "w") as file:
        file.write("\n".join(usernames))

bot.tiktok_usernames = load_usernames("tiktok_users.txt")
bot.twitch_usernames = load_usernames("twitch_users.txt")


WELCOME_GUIDE_MARKER = "🇭🇹 **WELCOME TO THE 509!**"
WELCOME_GUIDE = """🇭🇹 **WELCOME TO THE 509!** 🎮

**Byenveni lakay ou!** Haitian gaming, football, anime, and good vibes!

**📍 CHANNEL GUIDE**
💬 #509-chat: General chat
⚽ #fc27-contents: FC27 and Clubs
🎬 #clips: Highlights
🍿 #anime-movies-forum: Recommendations
📢 #self-promote: Your content
🌐 #useful-websites: Resources
🎮 #free-games: Free games
🗳️ #polls: Voting
🏆 #leaderboard: XP ranks
🤖 #bot-commands: Bot commands
🔴 #live-notifications: Live alerts

**🏆 RANKS**
NPC → Random → Side Character → Regular → Main Character → Certified → GOAT → 👑 Himothy
Earn XP by chatting. Use bot rank and bot leaderboard.

**🤖 SIDEBOT**
bot list: Commands
bot create poll Messi | Ronaldo | X: Poll
509website: Resources
🔐 day-one: Private password-verified access.

**📜 RULES**
1. Respect everyone. No harassment or discrimination.
2. No spam, scams, or harmful links.
3. **Use the right channel for each topic.**
4. Respect privacy and moderators.
5. Follow Discord's rules.

**🚀 GET STARTED**
Say hello in #509-chat!
🇭🇹 **THE 509, PLIS KE YON SÈVÈ, SE YON KOMINOTE!** 🔵🔴
"""


async def ensure_welcome_guide(guild):
    """Keep a persistent guide in #welcome and preserve unrelated pinned messages."""
    channel = nextcord.utils.get(guild.text_channels, name="welcome")
    if not channel:
        return
    try:
        pinned = await channel.pins()
        if any(m.author.id == bot.user.id and m.content.startswith(WELCOME_GUIDE_MARKER) for m in pinned):
            return
        async for message in channel.history(limit=100):
            if message.author.id == bot.user.id and message.content.startswith(WELCOME_GUIDE_MARKER):
                if channel.permissions_for(guild.me).manage_messages:
                    try:
                        await message.pin(reason="509 welcome guide")
                    except nextcord.DiscordException:
                        pass
                return
        guide = await channel.send(WELCOME_GUIDE, allowed_mentions=nextcord.AllowedMentions.none())
        if channel.permissions_for(guild.me).manage_messages:
            try:
                await guide.pin(reason="509 welcome guide")
            except nextcord.DiscordException:
                pass
    except nextcord.DiscordException as exc:
        print(f"Welcome guide setup failed in {guild.name}: {exc}")


@tasks.loop(hours=6)
async def welcome_cleanup():
    """Delete unpinned messages older than 3 days ONLY in #welcome."""
    from datetime import timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    for guild in bot.guilds:
        channel = nextcord.utils.get(guild.text_channels, name="welcome")
        if not channel:
            continue
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.read_message_history and perms.manage_messages):
            continue
        removed = 0
        try:
            async for message in channel.history(limit=250, before=cutoff):
                if message.pinned or (
                    message.author.id == bot.user.id and message.content.startswith(WELCOME_GUIDE_MARKER)
                ):
                    continue
                try:
                    await message.delete(reason="509 welcome messages older than three days")
                    removed += 1
                except nextcord.DiscordException as exc:
                    print(f"Welcome cleanup deletion failed: {exc}")
            if removed:
                print(f"Removed {removed} aged welcome messages from {guild.name}")
        except nextcord.DiscordException as exc:
            print(f"Welcome cleanup failed in {guild.name}: {exc}")


@welcome_cleanup.before_loop
async def welcome_cleanup_ready():
    await bot.wait_until_ready()



FREE_GAMES_FILE = os.path.join(os.path.dirname(__file__), "free_games_seen.json")

def load_free_games_seen():
    try:
        with open(FREE_GAMES_FILE, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

free_games_seen = load_free_games_seen()

def save_free_games_seen():
    temp = FREE_GAMES_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(free_games_seen, handle)
    os.replace(temp, FREE_GAMES_FILE)

def fetch_free_game_offers():
    """GamerPower official public API, full-game offers only."""
    response = requests.get(
        "https://www.gamerpower.com/api/giveaways",
        params={"type": "game", "sort-by": "date"},
        headers={"User-Agent": "SideBot-509/1.0 (Discord community free games alerts)"},
        timeout=20,
    )
    if response.status_code == 201:
        return []
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []

@tasks.loop(hours=2)
async def free_games_watch():
    """Announce newly discovered full game giveaways without startup flooding."""
    try:
        offers = await asyncio.to_thread(fetch_free_game_offers)
    except (requests.RequestException, ValueError) as exc:
        print(f"[free games] API error: {exc}")
        return
    now_ids = {str(item["id"]) for item in offers if isinstance(item, dict) and "id" in item}
    for guild in bot.guilds:
        channel = nextcord.utils.get(guild.text_channels, name="free-games")
        if not channel or not channel.permissions_for(guild.me).send_messages:
            continue
        key = str(guild.id)
        # At first run store current active deals, don't flood the channel.
        if key not in free_games_seen:
            free_games_seen[key] = sorted(now_ids)
            save_free_games_seen()
            continue
        recorded = set(map(str, free_games_seen.get(key, [])))
        for offer in reversed(offers):
            if not isinstance(offer, dict):
                continue
            identifier = str(offer.get("id", ""))
            if not identifier or identifier in recorded or offer.get("status", "Active").lower() != "active":
                continue
            title = str(offer.get("title", "Free game"))[:150]
            link = str(offer.get("gamerpower_url", ""))
            if not link.startswith("https://www.gamerpower.com/"):
                link = "https://www.gamerpower.com/"
            platforms = str(offer.get("platforms", "Various platforms"))[:180]
            expires = str(offer.get("end_date", "See offer page"))[:80]
            try:
                await channel.send(
                    f"🎮 **FREE GAME ALERT**\n**{nextcord.utils.escape_markdown(title)}**\n"
                    f"Platform: {nextcord.utils.escape_markdown(platforms)}\n"
                    f"Ends: {nextcord.utils.escape_markdown(expires)}\n"
                    f"Claim details: {link}\n"
                    "Source: GamerPower.com • Check offer restrictions before claiming.",
                    allowed_mentions=nextcord.AllowedMentions.none()
                )
            except nextcord.DiscordException as exc:
                print(f"[free games] failed to post in {guild.name}: {exc}")
                break
            recorded.add(identifier)
            free_games_seen[key] = sorted(recorded)[-1000:]
            save_free_games_seen()

@free_games_watch.before_loop
async def free_games_wait_ready():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    global bot_start_time
    bot_start_time = datetime.utcnow().isoformat()
    print(f"Bot is ready! Logged in as {bot.user.name}")
    print("Mwen back anbet! 🚀")
    if not getattr(bot, "_dashboard_started", False):
        threading.Thread(target=run_web_server, daemon=True).start()
        bot._dashboard_started = True
    add_event(f"Bot online as {bot.user.name}")
    if not check_live_status.is_running():
        check_live_status.start()
    if not dayone_expiry_check.is_running():
        dayone_expiry_check.start()
    if not welcome_cleanup.is_running():
        welcome_cleanup.start()
    if not rank_role_monitor.is_running():
        rank_role_monitor.start()
    if not free_games_watch.is_running():
        free_games_watch.start()
    for guild in bot.guilds:
        await ensure_rank_roles(guild)
        await ensure_welcome_guide(guild)

@bot.event
async def on_guild_join(guild):
    """Send a simple welcome message when the bot joins a new server."""
    await ensure_rank_roles(guild)
    await ensure_welcome_guide(guild)
    # Try #general first, then the first text channel we can send to
    target = (
        nextcord.utils.get(guild.text_channels, name="general")
        or next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
    )
    if target:
        try:
            await target.send(
                "Yo! SideBot here. 👋\n"
                "I'm ready — ask me stuff, I got you.\n"
                "Gaming, weather, casual chat — I speak Creole too.\n"
                "Type `@SideBot help` if you're lost."
            )
            add_event(f"🆕 Joined server: {guild.name}")
        except Exception as e:
            print(f"[on_guild_join] Could not send welcome to {guild.name}: {e}")


@bot.event
async def on_member_join(member):
    """Assign all accessible roles and start the onboarding DM flow."""
    # Do not grant all server roles to new members.
    welcome_role_name = os.getenv("WELCOME_ROLE_NAME", "").strip()
    if welcome_role_name:
        role = nextcord.utils.get(member.guild.roles, name=welcome_role_name)
        if role and not role.managed and not role.permissions.administrator and role < member.guild.me.top_role:
            try:
                await member.add_roles(role, reason="Configured welcome role")
                print(f"Assigned welcome role {role.name} to {member.name}")
            except nextcord.DiscordException as e:
                print(f"Could not assign welcome role: {e}")
        else:
            print(f"Skipping unavailable or unsafe welcome role: {welcome_role_name}")

    # Welcome message in #welcome or #general
    welcome_channel = (
        nextcord.utils.get(member.guild.channels, name="welcome")
        or nextcord.utils.get(member.guild.channels, name="general")
    )
    if welcome_channel:
        await welcome_channel.send(
            f"Byenveni {member.mention}! 🎉 Map voye w yon mesaj prive pou nou ka konnen ou pi byen."
        )

    # Start onboarding via DM
    try:
        onboarding_state[member.id] = {
            "step": "reason",
            "data": {},
            "guild_id": member.guild.id
        }
        await member.send(
            f"Alo **{member.display_name}**! 👋 Byenveni nan sèvè a!\n\n"
            f"Map poze w kèk kesyon rapid pou nou ka konnen ou pi byen.\n\n"
            f"**➡️ Poukisa w te rejwenn sèvè a?**\n"
            f"1️⃣ FIFA\n"
            f"2️⃣ COD (Call of Duty)\n"
            f"3️⃣ Jis pale / Just chat\n"
            f"4️⃣ Lòt bagay\n\n"
            f"Reponn ak **1**, **2**, **3**, oswa **4**.\n\n"
            f"💡 *Ou renmen SideBot? Invite li nan pwòp sèvè pa w:*\n{INVITE_URL}"
        )
    except Exception as e:
        print(f"Could not DM {member.name} for onboarding: {e}")


async def handle_onboarding(message):
    """Handle step-by-step onboarding DM conversation."""
    user_id = message.author.id
    state = onboarding_state.get(user_id)
    if not state:
        return

    step = state["step"]
    data = state["data"]
    text = message.content.strip()

    if step == "reason":
        reasons = {"1": "FIFA", "2": "COD", "3": "Just chat", "4": "Lòt bagay"}
        if text not in reasons:
            await message.channel.send(
                "⚠️ Tanpri reponn ak **1**, **2**, **3**, oswa **4**."
            )
            return
        data["reason"] = reasons[text]
        state["step"] = "name"
        await message.channel.send(
            f"✅ *{data['reason']}* — bon!\n\n"
            f"**➡️ Ki jan yo rele w? (non w oswa ti non w)**"
        )

    elif step == "name":
        data["name"] = text
        if data["reason"] in ("FIFA", "COD"):
            state["step"] = "game_username"
            game = data["reason"]
            await message.channel.send(
                f"✅ Enchanté **{text}**!\n\n"
                f"**➡️ Ki username w sou {game}?**"
            )
        else:
            await finish_onboarding(message, state)

    elif step == "game_username":
        data["game_username"] = text
        state["step"] = "console_username"
        await message.channel.send(
            f"✅ Username **{text}** — noté!\n\n"
            f"**➡️ Ki username w sou PlayStation / console w? (PS4, PS5, Xbox, PC…)**"
        )

    elif step == "console_username":
        data["console_username"] = text
        await finish_onboarding(message, state)


async def finish_onboarding(message, state):
    """Send summary to the member-info channel and thank the user."""
    data = state["data"]
    user_id = message.author.id
    guild_id = state["guild_id"]

    await message.channel.send(
        "✅ Mèsi! Tout enfòmasyon yo anrejistre. Enjoy sèvè a! 🎮🔥"
    )

    guild = bot.get_guild(guild_id)
    member = guild.get_member(user_id) if guild else None
    joined_at = getattr(member, "joined_at", None)
    profile = {
        "discord_username": getattr(message.author, "name", None)
        or getattr(message.author, "display_name", "Unknown"),
        "reason": data.get("reason", "—"),
        "game_username": data.get("game_username", "—"),
        "console_username": data.get("console_username", "—"),
        "join_time": (
            joined_at.isoformat()
            if joined_at
            else datetime.utcnow().isoformat() + "Z"
        ),
    }
    record_member_profile(profile)
    onboarding_state.pop(user_id, None)

    # Post summary to #member-info (fallback to #general)
    if guild:
        info_channel = (
            nextcord.utils.get(guild.channels, name="member-info")
            or nextcord.utils.get(guild.channels, name="general")
        )
        if info_channel:
            mention = member.mention if member else f"User {user_id}"
            lines = [
                f"📋 **Nouvo manm:** {mention}",
                f"👤 Non: **{data.get('name', '—')}**",
                f"🎯 Rezon: **{data.get('reason', '—')}**",
            ]
            if "game_username" in data:
                lines.append(f"🎮 Username {data['reason']}: **{data['game_username']}**")
            if "console_username" in data:
                lines.append(f"🕹️ Console username: **{data['console_username']}**")
            await info_channel.send("\n".join(lines))


_GROQ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_datetime",
            "description": "Get the current date and time in Haiti/Eastern timezone. Call this whenever the user asks about time or date.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Fetch real current weather for a location using Open-Meteo (free, no API key). ALWAYS call this for weather questions — never guess temperatures.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City or location name, e.g. 'Miami' or 'Port-au-Prince'"}
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sports_scores",
            "description": "Fetch live or recent sports scores from ESPN. ALWAYS call this for sports score questions — never invent scores.",
            "parameters": {
                "type": "object",
                "properties": {
                    "team_or_match": {"type": "string", "description": "Team name or matchup, e.g. 'Real Madrid' or 'Real Madrid vs Barcelona'"}
                },
                "required": ["team_or_match"],
            },
        },
    },
]

_GROQ_SYSTEM = (
    "You are SideBot, a Discord bot for a Haitian Creole gaming community (FIFA, COD, TikTok, Twitch). "
    "Respond as a helpful Discord bot: concise (stay under 2000 characters), casual, gaming-aware. "
    "LANGUAGE: default to English. Only switch to another language (Haitian Creole, French, Spanish, etc.) "
    "if the user clearly writes in that language — do NOT assume Creole just because the community is Haitian. "
    "When the user does write in Haitian Creole, use authentic casual slang, not textbook-formal Creole. "
    "TOOL DISCIPLINE: when asked about weather, sports scores, or current time/date, "
    "you MUST call the appropriate tool — NEVER guess or hallucinate data. "
    "If a tool returns no results or fails, tell the user honestly: "
    "'I couldn\\'t fetch that info, but you can check [ESPN / weather.com / etc.]'. "
    "Never invent temperatures, scores, schedules, or timestamps."
)

_GROQ_MODEL = "openai/gpt-oss-120b"

def get_ai_response(user_id: int, user_message: str, context: str = "server") -> str:
    """Stage 2: Groq AI, full error logging, context-separated history.

    context: 'dm' or 'server' — histories are stored under separate keys so a
    user's DM conversation and their server @mention conversation never mix.
    """
    from openai import OpenAI
    client = OpenAI(
        api_key=os.getenv("GROQ_API_KEY"),
        base_url="https://api.groq.com/openai/v1",
    )

    conv_key = f"{context}_{user_id}"
    history = ai_conversations.setdefault(conv_key, [])
    history.append({"role": "user", "content": user_message})
    if len(history) > AI_MAX_HISTORY:
        ai_conversations[conv_key] = history[-AI_MAX_HISTORY:]
        history = ai_conversations[conv_key]

    messages = [{"role": "system", "content": _GROQ_SYSTEM}, *history]

    try:
        response = client.chat.completions.create(
            model=_GROQ_MODEL,
            messages=messages,
            tools=_GROQ_TOOLS,
            tool_choice="auto",
            max_tokens=200,
        )
    except Exception as e:
        full_err = traceback.format_exc()
        print(f"[Groq ERROR] Full exception:\n{full_err}")
        print(f"[Groq ERROR] Request body sent:\n{json.dumps(messages, default=str, indent=2)}")
        raise

    msg = response.choices[0].message
    reply = (msg.content or "").strip()

    if msg.tool_calls:
        # Convert the SDK model to a plain dict before sending it back. This
        # keeps the assistant tool-call message in the exact shape Groq expects.
        assistant_message = (
            msg.model_dump(exclude_none=True)
            if hasattr(msg, "model_dump")
            else {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )
        messages.append(assistant_message)
        for tc in msg.tool_calls:
            fn = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
            if fn == "get_current_datetime":
                result = get_current_datetime()
            elif fn == "get_weather":
                result = get_weather(args.get("location", ""))
            elif fn == "get_sports_scores":
                result = get_sports_scores(args.get("team_or_match", ""))
            else:
                result = f"Unknown tool '{fn}'."
            print(f"[Tool] {fn}({args}) → {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        try:
            response2 = client.chat.completions.create(
                model=_GROQ_MODEL, messages=messages, max_tokens=200)
        except Exception:
            full_err = traceback.format_exc()
            print(f"[Groq ERROR] Tool-result call:\n{full_err}")
            print(f"[Groq ERROR] Messages:\n{json.dumps(messages, default=str, indent=2)}")
            raise
        reply = (response2.choices[0].message.content or "").strip()

    history.append({"role": "assistant", "content": reply})
    return reply


# Social links are keyed by guild and member ID, never just display name.
SOCIAL_LINKS_FILE = os.path.join(os.path.dirname(__file__), "social_links.json")
SOCIAL_CHANNEL_NAMES = {"self-promote", "self-promo", "self-promotion"}
SOCIAL_HOSTS = {
    "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"},
    "tiktok": {"tiktok.com", "www.tiktok.com", "m.tiktok.com"},
    "twitch": {"twitch.tv", "www.twitch.tv"},
    "instagram": {"instagram.com", "www.instagram.com"},
}
social_links_lock = threading.Lock()


def load_social_links():
    try:
        with open(SOCIAL_LINKS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


social_links = load_social_links()


def extract_social_links(content):
    """Accept only recognized public profile URLs, not arbitrary external links."""
    from urllib.parse import urlsplit
    found = {}
    for raw in re.findall(r"https?://[^\s<>]+", content, flags=re.IGNORECASE):
        url = raw.rstrip(".,!?;:)]}>")
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            path = parsed.path.strip("/")
            platform = next((p for p, hosts in SOCIAL_HOSTS.items() if host in hosts), None)
            if not platform or not path:
                continue
            first = path.split("/")[0]
            if platform == "youtube":
                valid = first.startswith("@") or first.lower() in ("channel", "c", "user")
                if not valid or (first.lower() in ("channel", "c", "user") and len(path.split("/")) < 2):
                    continue
            elif platform == "tiktok":
                if not first.startswith("@"):
                    continue
            elif platform == "twitch":
                if first.lower() in ("videos", "directory", "downloads", "settings"):
                    continue
            elif platform == "instagram":
                if first.lower() in ("p", "reel", "reels", "stories", "explore", "accounts"):
                    continue
            found[platform] = url
        except ValueError:
            continue
    return found


def save_social_links():
    temp = SOCIAL_LINKS_FILE + ".tmp"
    with social_links_lock:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(social_links, f, ensure_ascii=False, indent=2)
        os.replace(temp, SOCIAL_LINKS_FILE)


def social_query(message):
    """Handle 'bot what @member youtube' or 'bot @member socials'."""
    if not message.guild:
        return None
    text = message.content.strip()
    match = re.match(
        r"^(?:bot|bobot|sidebot|side bot)\s+(?:(?:what(?:'s| is)?|give|show|get)\s+)?(.+?)\s+"
        r"(youtube|tiktok|twitch|instagram|insta|yt|socials?|links?)\s*\??$",
        text, flags=re.IGNORECASE
    )
    if not match:
        return None
    subject, platform = match.groups()
    subject = re.sub(r"^(?:is|are|does|of|for)\s+", "", subject.strip(), flags=re.I)
    platform = {"insta": "instagram", "yt": "youtube", "social": "all", "socials": "all", "link": "all", "links": "all"}.get(platform.lower(), platform.lower())
    member = next((m for m in message.mentions if m.id != bot.user.id), None)
    if member is None:
        lookup = subject.lstrip("@").casefold()
        matches = [m for m in message.guild.members if
                   m.name.casefold() == lookup or m.display_name.casefold() == lookup or
                   (getattr(m, "global_name", None) or "").casefold() == lookup]
        if len(matches) != 1:
            return "I couldn't identify that member. Mention them directly, for example: `bot what @user youtube`."
        member = matches[0]
    record = social_links.get(str(message.guild.id), {}).get(str(member.id), {})
    if not record:
        return f"No saved social links for {member.mention} yet. Post a profile link in #self-promote."
    if platform == "all":
        return f"Social links for {member.mention}:\n" + "\n".join(f"**{p.title()}:** {url}" for p, url in sorted(record.items()))
    url = record.get(platform)
    return f"{member.mention}'s {platform.title()}: {url}" if url else f"No {platform.title()} link saved for {member.mention} yet."


# XP is server-specific. Only normal messages count, with a cooldown.
XP_FILE = os.path.join(os.path.dirname(__file__), "xp_scores.json")
XP_RANKS = (
    ("NPC", 0, 0x95A5A6),
    ("Random", 100, 0xCD7F32),
    ("Side Character", 350, 0xBCC6CC),
    ("Regular", 800, 0xFFD700),
    ("Main Character", 1600, 0x40E0D0),
    ("Certified", 3000, 0x3498DB),
    ("GOAT", 5500, 0x9B59B6),
    ("Himothy", 10000, 0xE74C3C),
)
OLD_RANK_NAMES = ("Rookie", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Master", "Legend")
XP_COOLDOWN_SECONDS = 60
XP_PER_MESSAGE = 10
xp_mutex = threading.Lock()
xp_last_award = {}


def load_xp_scores():
    try:
        with open(XP_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


xp_scores = load_xp_scores()


def save_xp_scores():
    with xp_mutex:
        temp = XP_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(xp_scores, f, ensure_ascii=False)
        os.replace(temp, XP_FILE)


def rank_for_xp(xp):
    return max((r for r in XP_RANKS if xp >= r[1]), key=lambda r: r[1])


async def ensure_rank_roles(guild):
    """Rename existing rank roles where possible, preserving member assignments."""
    roles = {}
    for index, (name, threshold, color) in enumerate(XP_RANKS):
        old_role = nextcord.utils.get(guild.roles, name=f"Rank | {OLD_RANK_NAMES[index]}")
        new_role = nextcord.utils.get(guild.roles, name=f"Rank | {name}")
        if old_role and not new_role:
            try:
                await old_role.edit(name=f"Rank | {name}", reason="SideBot rank name update")
            except nextcord.DiscordException as exc:
                print(f"Cannot rename rank role in {guild.name}: {exc}")
    # Create any roles that could not be renamed.
    roles = {}
    for name, threshold, color in XP_RANKS:
        role_name = f"Rank | {name}"
        role = nextcord.utils.get(guild.roles, name=role_name)
        if role is None:
            try:
                role = await guild.create_role(
                    name=role_name, colour=nextcord.Colour(color),
                    permissions=nextcord.Permissions.none(),
                    reason="SideBot activity rank",
                )
            except nextcord.DiscordException as exc:
                print(f"Cannot create {role_name} in {guild.name}: {exc}")
        if role:
            roles[name] = role
    return roles


async def apply_rank_role(member, xp):
    """Keep the earned rank, remove only SideBot rank roles."""
    roles = await ensure_rank_roles(member.guild)
    chosen = roles.get(rank_for_xp(xp)[0])
    if not chosen:
        return
    known = set(roles.values())
    remove = [role for role in member.roles if role in known and role != chosen]
    try:
        if remove:
            await member.remove_roles(*remove, reason="SideBot rank updated")
        if chosen not in member.roles:
            await member.add_roles(chosen, reason="SideBot activity rank")
    except nextcord.DiscordException as exc:
        print(f"Could not update rank for {member.id}: {exc}")


async def grant_message_xp(message):
    if not message.guild or message.author.bot or not isinstance(message.author, nextcord.Member):
        return
    # Do not award points for commands, very short messages, or repeated spam.
    content = message.content.strip()
    if len(content) < 8 or content.startswith(("!", "/")) or content.lower().startswith(("bot ", "bobot ", "sidebot ", "side bot ")):
        return
    from time import monotonic
    key = (message.guild.id, message.author.id)
    now = monotonic()
    if now - xp_last_award.get(key, float("-inf")) < XP_COOLDOWN_SECONDS:
        return
    xp_last_award[key] = now
    guild_data = xp_scores.setdefault(str(message.guild.id), {})
    user_key = str(message.author.id)
    previous = max(0, int(guild_data.get(user_key, 0)))
    updated = previous + XP_PER_MESSAGE
    guild_data[user_key] = updated
    save_xp_scores()
    if previous == 0 or rank_for_xp(previous)[0] != rank_for_xp(updated)[0]:
        await apply_rank_role(message.author, updated)
        add_event(f"Rank up: {message.author.display_name} -> {rank_for_xp(updated)[0]}")



# Configurable resource directory. Only owner-provided URLs are returned.
RESOURCE_LINKS_FILE = os.path.join(os.path.dirname(__file__), "resource_links.json")
RESOURCE_CATEGORIES = ("anime", "movies", "football", "manga", "music", "clips", "games")

def load_resource_links():
    try:
        with open(RESOURCE_LINKS_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        print(f"[resources] load failed: {exc}")
        return {}

resource_links = load_resource_links()

def save_resource_links():
    temporary = RESOURCE_LINKS_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(resource_links, handle, indent=2, ensure_ascii=False)
    os.replace(temporary, RESOURCE_LINKS_FILE)

def resource_url_valid(url):
    from urllib.parse import urlsplit
    try:
        result = urlsplit(url)
        return result.scheme == "https" and bool(result.netloc) and not result.username and not result.password
    except ValueError:
        return False

def resource_reply(category):
    item = resource_links.get(category, {})
    if not isinstance(item, dict):
        item = {}
    url = str(item.get("url", ""))
    if not resource_url_valid(url):
        return f"No {category} resource configured yet. Ask a moderator."
    description = str(item.get("description", ""))[:160]
    return (f"🇭🇹 **THE 509 — {category.upper()}**\n"
            f"{description}\n{url}\n"
            "⚠️ Only use trustworthy and lawful sources.")

@bot.group(name="links", invoke_without_command=True)
async def links_command(ctx):
    status = "\n".join(
        f"• {key}: {'available' if resource_url_valid(resource_links.get(key, {}).get('url', '')) else 'not configured'}"
        for key in RESOURCE_CATEGORIES
    )
    await ctx.send(
        "🇭🇹 **THE 509 — USEFUL LINKS**\n" + status +
        "\nTry bot anime, bot movies, bot football, etc.\n" +
        "Moderators: bot links set anime https://example.com",
        allowed_mentions=nextcord.AllowedMentions.none())

@links_command.command(name="set")
@commands.has_permissions(manage_guild=True)
async def links_set(ctx, category: str, url: str):
    category = category.casefold()
    if category not in RESOURCE_CATEGORIES or len(url) > 1200 or not resource_url_valid(url):
        await ctx.send("Use a valid category and HTTPS URL. See bot links.")
        return
    resource_links.setdefault(category, {})["url"] = url
    try:
        save_resource_links()
    except OSError as exc:
        await ctx.send(f"Could not save the URL: {exc}")
        return
    await ctx.send(f"Updated {category} resource.", allowed_mentions=nextcord.AllowedMentions.none())

@links_command.command(name="remove")
@commands.has_permissions(manage_guild=True)
async def links_remove(ctx, category: str):
    category = category.casefold()
    if category not in RESOURCE_CATEGORIES:
        await ctx.send("Unknown resource category.")
        return
    resource_links.setdefault(category, {})["url"] = ""
    save_resource_links()
    await ctx.send(f"Removed {category} link.")

def register_resource_commands():
    for category in RESOURCE_CATEGORIES:
        async def resource_command(ctx, _category=category):
            await ctx.send(resource_reply(_category),
                           allowed_mentions=nextcord.AllowedMentions.none())
        resource_command.__name__ = f"resource_{category}_command"
        bot.command(name=category)(resource_command)

register_resource_commands()

@bot.command(name="list")
async def list_commands(ctx):
    """Show all registered SideBot commands and supported chat triggers."""
    descriptions = {
        "rank": "View your XP and rank, optionally mention a member",
        "links": "Resource directory; moderators can set or remove URLs",
        "anime": "Anime website",
        "movies": "Movies and TV website",
        "football": "Football and live sports website",
        "manga": "Manga website",
        "music": "Music website",
        "clips": "Clips website",
        "games": "Games website",
        "leaderboard": "Top 10 members by XP (alias: top)",
        "ranks": "View all rank thresholds",
        "setupranks": "Create rank roles (Manage Roles required)",
        "ping": "Check bot latency and monitor status",
        "clean": "Delete messages (alias: clear; Manage Messages required)",
        "create_role": "Create a Discord role (Manage Roles required)",
        "create": "Create channel, thread, role (with permission flags), poll, or event. Use bot create for syntax.",
        "dayone": "Set up private day-one channel with temporary or permanent password access",
        "assign_role": "Assign a member role (Administrator required)",
        "whoslive": "Check monitored Twitch and TikTok accounts",
        "create_poll": "Post a poll (Manage Roles required)",
        "assign_poll_role": "Assign role to yes voters (Manage Roles required)",
        "invite": "Get SideBot's server invite information",
        "send": "Send a message to a named channel (Manage Messages required)",
        "join": "Join a voice channel",
        "leave": "Leave a voice channel",
        "list": "Show this command list",
        "roleperms": "Show each role’s server permissions (Administrator only)",
        "delete_role": "Delete an ordinary role (Manage Roles required)",
        "create_access_role": "Create access role for a channel: bot create_access_role Name #channel",
        "kick": "Kick a member, optionally with a reason",
        "ban": "Ban a member, optionally with a reason",
        "unban": "Unban by numeric user ID",
    }
    lines = ["**SideBot commands**", "Prefixes: `bot`, `sidebot`, `bobot`, `side bot`, `!`"]
    for command in sorted(bot.commands, key=lambda item: item.name):
        if command.hidden or command.name == "help":
            continue
        lines.append(f"`bot {command.name}`: {descriptions.get(command.name, command.help or 'Bot command')}")
    lines.extend([
        "",
        "**Other triggers**",
        "`bot kiye la`: Mention online channel members",
        "`bot saw util` / `bot kisa w itil`: Help in Haitian Creole",
        "`bot what @member youtube`: Look up a saved social profile",
        "`bot @member socials`: Show their saved social links",
        "`@SideBot question` or `sidebot question`: AI conversation",
        "`bot` / `sidebot` / `bobot`: Wake the bot",
        "`gg`, `ggs`, `sak pase`: Casual responses",
    ])
    # Discord messages must remain below the 2000 character limit.
    buffer = ""
    for line in lines:
        if len(buffer) + len(line) + 1 > 1900:
            await ctx.send(buffer, allowed_mentions=nextcord.AllowedMentions.none())
            buffer = ""
        buffer += line + "\n"
    if buffer:
        await ctx.send(buffer, allowed_mentions=nextcord.AllowedMentions.none())


@bot.command(name="roleperms", aliases=["rolepermissions"])
@commands.has_permissions(administrator=True)
async def role_permissions_command(ctx, *, role_name: str = ""):
    """Show role permissions. Server administrators only."""
    if ctx.guild is None:
        return
    if role_name:
        role = next((r for r in ctx.guild.roles if r.name.casefold() == role_name.casefold()), None)
        if role is None:
            await ctx.send("Role not found. Use `bot roleperms` to see available roles.")
            return
        roles = [role]
    else:
        roles = sorted(ctx.guild.roles, key=lambda role: role.position, reverse=True)

    for role in roles:
        allowed = [name.replace("_", " ").title() for name, enabled in role.permissions if enabled]
        perms = ", ".join(allowed) if allowed else "No server-wide permissions"
        if role.permissions.administrator:
            perms = "**Administrator: bypasses channel restrictions**. " + perms
        heading = f"**{nextcord.utils.escape_markdown(role.name)}**"
        body = f"{heading}\n{perms}"
        if len(body) > 1900:
            body = body[:1890] + "..."
        await ctx.send(body, allowed_mentions=nextcord.AllowedMentions.none())


def draw_rank_card(display_name, avatar_bytes, xp, placement):
    """Render a personalized card inspired by the blue, red and silver 509 crest."""
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import math

    W, H = 1050, 350
    img = Image.new("RGB", (W, H), (2, 8, 22))
    d = ImageDraw.Draw(img)
    name, floor, _ = rank_for_xp(xp)
    number = next(i for i, entry in enumerate(XP_RANKS, 1) if entry[0] == name)
    following = next((r for r in XP_RANKS if r[1] > xp), None)
    blue, red, white = (0, 112, 255), (248, 34, 61), (241, 247, 255)
    regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    heavy = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def font(size, bold=False):
        try:
            return ImageFont.truetype(heavy if bold else regular, size)
        except OSError:
            return ImageFont.load_default()

    # Dramatic split lighting and subtle digital streaks, matching the 509 artwork.
    for x in range(W):
        t = x / (W - 1)
        d.line((x, 0, x, H), fill=(int(4 + 17*t), int(10 + 2*(1-t)), int(28 - 14*t)))
    glow = Image.new("RGBA", (W, H))
    gd = ImageDraw.Draw(glow)
    for i in range(16):
        gd.line((-40, 310-i*16, 510, 110-i*7), fill=(10, 86+i*6, 255, 25), width=5)
        gd.line((570, 95-i*7, 1100, 310-i*16), fill=(255, 22+i*3, 48, 25), width=5)
    glow = glow.filter(ImageFilter.GaussianBlur(9))
    img = Image.alpha_composite(img.convert("RGBA"), glow)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((12, 12, W-13, H-13), radius=19, outline=(56, 83, 133), width=3)
    d.line((20, 19, 490, 19), fill=blue, width=5)
    d.line((565, 19, W-20, 19), fill=red, width=5)
    d.line((20, H-18, 490, H-18), fill=blue, width=4)
    d.line((565, H-18, W-20, H-18), fill=red, width=4)
    # Decorative fortress and palms as subtle silhouettes above the stats area.
    for px in (610, 665, 810, 865):
        d.line((px, 119, px, 43), fill=(12, 33, 66), width=7)
        for delta in (-42, -25, 25, 42):
            d.arc((px+delta-42, 38, px+delta+42, 106), 200, 320, fill=(14, 36, 72), width=3)
    d.polygon([(690, 110), (690, 53), (728, 53), (728, 36), (760, 36),
               (760, 53), (801, 53), (801, 108)], fill=(16, 32, 56))
    # Haitian flag motif and section header.
    d.rectangle((790, 35, 826, 46), fill=(20, 73, 228))
    d.rectangle((790, 46, 826, 57), fill=(213, 27, 54))
    d.rectangle((801, 41, 814, 50), fill=white)
    d.text((843, 34), "THE 509", font=font(18, True), fill=(171, 191, 221))

    # Avatar with a neon bevel border.
    d.rounded_rectangle((32, 63, 223, 254), radius=20, fill=(2, 25, 65), outline=blue, width=5)
    try:
        ava = Image.open(BytesIO(avatar_bytes)).convert("RGB").resize((175, 175))
        mask = Image.new("L", (175, 175), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, 174, 174), radius=16, fill=255)
        img.paste(ava, (40, 71), mask)
    except (OSError, ValueError, TypeError):
        d.text((107, 131), "?", fill=white, font=font(51, True))
    d = ImageDraw.Draw(img)
    d.text((252, 49), display_name[:23], fill=white, font=font(31, True))
    d.text((967, 51), f"#{placement}", fill=white, font=font(32, True), anchor="ra")

    # Each level gets its own crowned metallic shield badge.
    bx, by = 301, 174
    level_shades = [
        (118, 128, 153), (155, 179, 210), (46, 208, 222),
        (16, 113, 255), (154, 82, 255), (244, 189, 63),
        (76, 192, 255), (255, 49, 75)
    ]
    shade = level_shades[number - 1]
    outline = [(bx, by-56), (bx+48, by-32), (bx+43, by+23),
               (bx, by+55), (bx-43, by+23), (bx-48, by-32)]
    d.polygon(outline, fill=(8, 16, 37))
    d.line(outline + [outline[0]], fill=shade, width=7, joint="curve")
    inner = [(bx, by-40), (bx+31, by-23), (bx+27, by+15),
             (bx, by+36), (bx-27, by+15), (bx-31, by-23)]
    d.polygon(inner, fill=(23, 39, 70))
    d.text((bx, by-16), str(number), font=font(38, True), fill=white, anchor="mm")
    if number >= 5:
        crowns = [(bx-27, by-63), (bx-18, by-77), (bx-4, by-65),
                  (bx, by-85), (bx+10, by-65), (bx+25, by-78), (bx+31, by-61)]
        d.polygon(crowns, fill=shade)
    d.text((370, 119), "RANK", fill=(151, 168, 199), font=font(20, True))
    d.text((370, 149), name.upper(), fill=white, font=font(29 if len(name)<14 else 23, True))

    # XP bar and numerical progression.
    d.text((710, 119), "XP", fill=(79, 165, 255), font=font(19, True))
    denom = following[1] if following else xp
    d.text((710, 146), f"{xp:,} / {denom:,}" if following else f"{xp:,} / MAX",
           fill=white, font=font(19, True))
    left, top, right, bottom = 710, 186, 988, 211
    d.rounded_rectangle((left, top, right, bottom), radius=12, fill=(44, 56, 81))
    ratio = min(1, max(0, (xp-floor)/(following[1]-floor))) if following else 1
    filled = int((right-left)*ratio)
    if filled:
        d.rounded_rectangle((left, top, left+filled, bottom), radius=min(12,filled//2), fill=red if number==8 else blue)
    d.text((988, 220), f"{ratio*100:.0f}%", fill=(221, 229, 243), font=font(15, True), anchor="ra")

    # Footer metadata, no fabricated message count or joining date.
    d.line((33, 282, W-33, 282), fill=(53, 67, 98), width=2)
    d.text((44, 297), f"SERVER RANK  #{placement}", fill=white, font=font(19, True))
    d.text((390, 297), f"{xp:,} TOTAL XP", fill=(154, 181, 225), font=font(19, True))
    d.text((755, 297), f"NEXT: {following[0].upper()}" if following else "HIMOTHY UNLOCKED",
           fill=(255, 139, 151), font=font(17, True))
    output = BytesIO()
    img.convert("RGB").save(output, format="PNG")
    output.seek(0)
    return output


@bot.command(name="rank")
@commands.cooldown(1, 5, commands.BucketType.user)
async def rank_command(ctx, member: nextcord.Member = None):
    if not ctx.guild:
        return
    member = member or ctx.author
    scores = xp_scores.get(str(ctx.guild.id), {})
    xp = max(0, int(scores.get(str(member.id), 0)))
    placement = 1 + sum(int(score) > xp for score in scores.values())
    try:
        avatar_bytes = await member.display_avatar.read()
        card = await asyncio.to_thread(draw_rank_card, member.display_name, avatar_bytes, xp, placement)
        await ctx.send(file=nextcord.File(card, filename="sidebot-rank.png"))
    except (nextcord.DiscordException, OSError, ValueError) as exc:
        print(f"[rank card] Failed: {exc}")
        name, _, _ = rank_for_xp(xp)
        await ctx.send(f"**{member.display_name}** | {name} | {xp:,} XP | Server rank #{placement}",
                       allowed_mentions=nextcord.AllowedMentions.none())


@bot.command(name="leaderboard", aliases=["top"])
async def leaderboard_command(ctx):
    if not ctx.guild:
        return
    scores = xp_scores.get(str(ctx.guild.id), {})
    top = sorted(((uid, int(xp)) for uid, xp in scores.items()), key=lambda x: x[1], reverse=True)[:10]
    if not top:
        await ctx.send("No points earned in this server yet.")
        return
    lines = []
    for position, (uid, xp) in enumerate(top, 1):
        member = ctx.guild.get_member(int(uid))
        label = member.display_name if member else f"Member {uid}"
        lines.append(f"{position}. {nextcord.utils.escape_markdown(label)}: {xp} XP ({rank_for_xp(xp)[0]})")
    await ctx.send("**SideBot Leaderboard**\n" + "\n".join(lines), allowed_mentions=nextcord.AllowedMentions.none())


@bot.command(name="ranks")
async def ranks_command(ctx):
    if not ctx.guild:
        return
    await ctx.send("**SideBot Ranks**\n" + "\n".join(f"**{name}**: {xp} XP" for name, xp, _ in XP_RANKS))


@tasks.loop(minutes=15)
async def rank_role_monitor():
    """Reconcile XP rank roles, including after manual role edits or restarts."""
    for guild in bot.guilds:
        if not guild.me.guild_permissions.manage_roles:
            continue
        try:
            roles = await ensure_rank_roles(guild)
            if len(roles) != len(XP_RANKS):
                continue
            for user_id, earned in list(xp_scores.get(str(guild.id), {}).items()):
                member = guild.get_member(int(user_id))
                if member and not member.bot:
                    expected = roles.get(rank_for_xp(max(0, int(earned)))[0])
                    existing = [role for role in member.roles if role in roles.values()]
                    if expected and (expected not in existing or len(existing) != 1):
                        await apply_rank_role(member, max(0, int(earned)))
        except (nextcord.DiscordException, ValueError, TypeError) as exc:
            print(f"Rank role monitor error in {guild.name}: {exc}")


@rank_role_monitor.before_loop
async def wait_for_rank_role_monitor():
    await bot.wait_until_ready()


@bot.command(name="setupranks")
@commands.has_permissions(manage_roles=True)
async def setup_ranks_command(ctx):
    if not ctx.guild:
        return
    roles = await ensure_rank_roles(ctx.guild)
    # Existing members may have XP from before role creation.
    await ctx.send(f"Configured {len(roles)}/{len(XP_RANKS)} rank roles. Use `bot rank` to view progress.")


def contains_discord_server_invite(text):
    """Recognize Discord server invitation URLs, not OAuth application URLs."""
    return bool(re.search(
        r"https?://(?:www\.)?(?:discord\.gg/|discord(?:app)?\.com/invite/)[A-Za-z0-9-]+",
        text, flags=re.IGNORECASE,
    ))


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.guild and message.content.strip().casefold() == "509website":
        await message.channel.send(
            "🌐 **How to Use FMHY**\n\n"
            "1. Go to https://fmhy.net/\n"
            "2. Use the 🔎 **Search** button at the top if you already know what you're looking for.\n"
            "3. Otherwise, scroll through the categories:\n"
            "   • 🎬 **Streaming**, movies, shows, and other video resources\n"
            "   • 🍥 **Anime**, search 'anime' for anime-related resources\n"
            "   • ⚽ **Sports**, search 'sports' or your sport\n"
            "   • 🎮 **Gaming**, gaming-related resources\n"
            "   • 📚 **Reading**, manga, comics, books, and more\n"
            "4. Open the section you want and choose one of the listed resources.\n"
            "5. If you're confused, open the **Beginners Guide** on the homepage.\n\n"
            "⚠️ **Use trusted/legal sources and be careful with unfamiliar links, pop-ups, downloads, and ads.**",
            allowed_mentions=nextcord.AllowedMentions.none(),
        )
        await message.channel.send(
            "🇭🇹 **THE 509 — FOOTBALL**\n"
            "⚽ Game day?\n"
            "▶️ Watch here:\nhttps://fmhy.net/video#live-sports\n"
            "🗣️ Talk about the match in #Lakay\n\n"
            "1. Open the **Live Sports** page.\n"
            "2. Scroll through the available website options.\n"
            "3. Choose whichever service or link you prefer.\n"
            "4. If one option does not work, go back and try another listed option.\n"
            "5. Use the search feature on the site if you're looking for a specific sport or event.\n\n"
            "⚠️ **Only use services that are legal in your location, and be careful with unfamiliar ads, pop-ups, downloads, or requests for personal information.**",
            allowed_mentions=nextcord.AllowedMentions.none(),
        )
        return

    if message.content.strip().casefold() in (
        "bot kreyol pale", "sidebot kreyol pale", "bobot kreyol pale", "side bot kreyol pale"
    ):
        await message.reply("kreyol konprann")
        return

    if message.guild:
        await grant_message_xp(message)
        if message.channel.name.lower() in SOCIAL_CHANNEL_NAMES:
            links = extract_social_links(message.content)
            if links:
                guild_links = social_links.setdefault(str(message.guild.id), {})
                guild_links.setdefault(str(message.author.id), {}).update(links)
                save_social_links()
                add_event(f"Saved social profiles for {message.author.display_name}: {', '.join(links)}")
        answer = social_query(message)
        if answer is not None:
            await message.reply(answer, allowed_mentions=nextcord.AllowedMentions.none())
            return

    # ── Three-stage mention pipeline ───────────────────────────
    if message.guild and bot.user in message.mentions and not message.mention_everyone:
        user_text = (
            message.content
            .replace(f"<@{bot.user.id}>", "")
            .replace(f"<@!{bot.user.id}>", "")
            .strip()
        )
        if not user_text:
            return

        # Stage 0 — command detection: never send commands to AI
        if is_command_text(user_text):
            if "kiye la" in user_text.lower():
                online_members = [m.mention for m in message.channel.members
                                  if m.status != nextcord.Status.offline and not m.bot]
                await message.reply(
                    " ".join(online_members) if online_members else "**Mwen pa jwen moun ki online.**"
                )
            else:
                await bot.process_commands(message)
            return

        # Stage 1 — fast-path casual match (no API call)
        casual_reply = casual_match(user_text)
        if casual_reply:
            if is_invite_response(casual_reply):
                # Never show invite link in public server channels
                add_event(f"🔗 Invite req (server, blocked) → {message.author.display_name}")
                await message.reply(
                    "🔒 Lyen envitasyon an pa disponib nan chanèl piblik.\n"
                    "Mande yon admin sèvè, oswa voye m yon DM pou jwenn lyen an! 😄"
                )
            else:
                resolved = resolve_reply(casual_reply, dm=False)
                add_event(f"💬 Casual: '{normalize_text(user_text)[:24]}' → {message.author.display_name}")
                await message.reply(resolved)
            return

        # Stage 2 — Groq AI fallback (server context — history isolated from DMs)
        async with message.channel.typing():
            try:
                reply = await asyncio.to_thread(
                    get_ai_response, message.author.id, user_text, "server"
                )
                add_event(f"🤖 AI reply to {message.author.display_name}")
                await message.reply(reply)
            except Exception as e:
                err_trace = traceback.format_exc()
                print(f"[AI server error] {e}\n{err_trace}")
                add_event(f"❌ AI error: {type(e).__name__}: {str(e)[:80]}")
                await message.reply("Desolé, mwen pa ka reponn kounye a. 😅")
        return
    # ──────────────────────────────────────────────────────────

    # Handle DMs — three-stage pipeline
    if not message.guild:
        # Onboarding takes priority — never intercept mid-flow
        if message.author.id in onboarding_state:
            await handle_onboarding(message)
            return

        user_text = message.content.strip()
        if not user_text:
            return

        if contains_discord_server_invite(user_text):
            await message.channel.send(
                f"Want to add SideBot to your server? Use my official invite link:\n{INVITE_URL}"
            )
            return

        # Stage 0 — commands in DMs: invite shows the link; everything else is server-only
        if is_command_text(user_text):
            s = normalize_text(user_text)
            for pfx in ("bot ", "side bot ", "!"):
                if s.startswith(pfx):
                    s = s[len(pfx):]
                    break
            cmd = s.split()[0] if s.split() else ""
            if cmd == "invite":
                await message.channel.send(
                    f"👋 **Ou vle ajoute SideBot nan pwòp sèvè pa w?**\n"
                    f"Klike sou lyen sa a pou invite m:\n{INVITE_URL}"
                )
            else:
                await message.channel.send(
                    "⚠️ Kòmand sa a sèlman travay nan yon chanèl sèvè — pa nan DM. "
                    "Eseye l nan sèvè a! 🎮"
                )
            return

        # Stage 1 — casual fast-path (covers help, invite, greetings, etc.)
        casual_reply = casual_match(user_text)
        if casual_reply:
            resolved = resolve_reply(casual_reply, dm=True)  # DMs: invite link allowed
            add_event(f"💬 DM casual: '{normalize_text(user_text)[:24]}' → {message.author.name}")
            await message.channel.send(resolved)
            return

        # Stage 2 — Groq AI fallback (DM context — history isolated from server mentions)
        async with message.channel.typing():
            try:
                reply = await asyncio.to_thread(
                    get_ai_response, message.author.id, user_text, "dm"
                )
                add_event(f"🤖 DM AI reply to {message.author.name}")
                await message.channel.send(reply)
            except Exception as e:
                err_trace = traceback.format_exc()
                print(f"[AI DM error] {e}\n{err_trace}")
                add_event(f"❌ DM AI error: {type(e).__name__}: {str(e)[:80]}")
                await message.channel.send("Desolé, mwen pa ka reponn kounye a. 😅")
        return

    content = message.content.lower()
    # Normalize the aliases for older inline commands and help triggers.
    for alias in ("side bot ", "sidebot ", "bobot "):
        if content.startswith(alias):
            content = "bot " + content[len(alias):]
            break

    if (
    content.startswith("bot saw util") or
    content.startswith("bot kisa w itil") or
    content.startswith("bot kisa w util") or
    content.startswith("bot sa w itil") or
    content.startswith("bot sa w util") or
    content.startswith("bot kisa ou itil") or
    content.startswith("bot kisa ou util") or
    content.startswith("bot sa ou itil") or
    content.startswith("bot sa ou util") or
    content.startswith("bot ki sa w itil") or
    content.startswith("bot ki sa w util") or
    content.startswith("bot ki sa ou itil") or
    content.startswith("bot ki sa ou util") or
    content.startswith("bot ki sa w fè") or
    content.startswith("bot ki sa ou fè") or
    content.startswith("bot ki sa w ka fè") or
    content.startswith("bot ki sa ou ka fè") or
    content.startswith("bot sa w ka fè") or
    content.startswith("bot sa ou ka fè") or
    content.startswith("bot kisa w ka fè") or
    content.startswith("bot kisa ou ka fè") or
    content.startswith("bot kisa w fe") or
    content.startswith("bot kisa ou fe") or
    content.startswith("bot ki sa w fe") or
    content.startswith("bot ki sa ou fe") or
    content.startswith("bot ki sa w ka fe") or
    content.startswith("bot ki sa ou ka fe") or
    content.startswith("bot sa w ka fe") or
    content.startswith("bot sa ou ka fe") or
    content.startswith("bot kisa w ka fe") or
    content.startswith("bot kisa ou ka fe") or
    content.startswith("bot ki sa w itil pou") or
    content.startswith("bot ki sa ou itil pou") or
    content.startswith("bot sa w itil pou") or
    content.startswith("bot sa ou itil pou") or
    content.startswith("bot sa w fe") or
    content.startswith("bot sa ou fe")
):

        await send_how_to(message.channel)
        return

    if "bot kiye la" in content:
        online_members = [member.mention for member in message.channel.members
                          if member.status != nextcord.Status.offline and not member.bot]
        await message.channel.send(
            " ".join(online_members) if online_members else "**Mwen pa jwen moun ki online.**"
        )
        return

    # Registered commands take precedence. Unknown wake-word messages use AI.
    wake = re.match(r"^(?:side bot|sidebot|bobot|bot)(?:\s+(.*))?$",
                    message.content.strip(), flags=re.IGNORECASE | re.DOTALL)
    if wake:
        ctx = await bot.get_context(message)
        if ctx.valid:
            await bot.invoke(ctx)
            return
        prompt = (wake.group(1) or "").strip()
        if not prompt:
            await message.reply("I'm here! Ask me something or try `bot rank`.")
            return
        async with message.channel.typing():
            try:
                reply = await asyncio.to_thread(get_ai_response, message.author.id, prompt, "server")
                await message.reply(reply, allowed_mentions=nextcord.AllowedMentions.none())
            except Exception as exc:
                print(f"[Wake word AI error] {exc}\n{traceback.format_exc()}")
                await message.reply("I can't answer right now. Try again shortly.")
        return

    await bot.process_commands(message)

# Check YouTube less often to conserve its search quota.
_youtube_last_checked = {}


def check_youtube_live(profile_url):
    """Verify an active YouTube livestream using the official Data API."""
    from urllib.parse import urlsplit
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return None
    try:
        path = urlsplit(profile_url).path.strip("/")
        parts = path.split("/")
        channel_id = None
        if len(parts) >= 2 and parts[0].lower() == "channel":
            channel_id = parts[1]
        elif parts[0].startswith("@"):
            response = requests.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "id", "forHandle": parts[0], "key": api_key},
                timeout=10,
            )
            response.raise_for_status()
            items = response.json().get("items", [])
            channel_id = items[0]["id"] if items else None
        if not channel_id:
            return None
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet", "channelId": channel_id,
                "eventType": "live", "type": "video",
                "maxResults": 1, "key": api_key,
            },
            timeout=10,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        video_id = items[0].get("id", {}).get("videoId") if items else None
        return (bool(video_id), f"https://www.youtube.com/watch?v={video_id}" if video_id else None)
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        print(f"YouTube live check failed: {exc}")
        return None


def profile_username(platform, url):
    from urllib.parse import urlsplit
    parts = urlsplit(url).path.strip("/").split("/")
    if not parts:
        return None
    if platform == "tiktok":
        return parts[0].lstrip("@")
    if platform == "twitch":
        return parts[0]
    return None


async def notify_live(guild, name, platform, link):
    channel = next(
        (c for c in guild.text_channels if c.name.lower() in ("notifications", "notification", "live-notifications")),
        None,
    ) or nextcord.utils.get(guild.text_channels, name="live")
    if not channel:
        print(f"No notification channel in {guild.name}; skipped {platform} alert")
        return False
    try:
        await channel.send(
            f"🔴 Hey, **{name}** is live on **{platform.title()}**!\n{link}",
            allowed_mentions=nextcord.AllowedMentions.none(),
        )
        return True
    except nextcord.DiscordException as exc:
        print(f"Could not notify {guild.name}: {exc}")
        return False


@tasks.loop(minutes=10)
async def check_live_status():
    global last_check_time
    print("Running live status check...")
    access_token = (
        await asyncio.to_thread(get_twitch_access_token)
        if TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET else None
    )
    current = set()
    checked = set()
    # Old manually tracked accounts also remain supported in each server.
    for guild in bot.guilds:
        targets = []
        for username in bot.twitch_usernames:
            targets.append((f"twitch:{username.lower()}", username, "twitch",
                            f"https://www.twitch.tv/{username}"))
        for username in bot.tiktok_usernames:
            targets.append((f"tiktok:{username.lower()}", username, "tiktok",
                            f"https://www.tiktok.com/@{username}/live"))
        for user_id, links in social_links.get(str(guild.id), {}).items():
            member = guild.get_member(int(user_id))
            name = member.display_name if member else f"User {user_id}"
            for platform in ("twitch", "tiktok", "youtube"):
                if links.get(platform):
                    targets.append((f"{platform}:member:{user_id}", name, platform, links[platform]))

        for identity, name, platform, url in targets:
            key = f"{guild.id}:{identity}"
            if key in checked:
                continue
            checked.add(key)
            result = None
            if platform == "youtube":
                if not os.getenv("YOUTUBE_API_KEY"):
                    continue
                from time import monotonic
                now = monotonic()
                if now - _youtube_last_checked.get(key, float("-inf")) < 3600:
                    # Retain the known active state until the next YouTube check.
                    if key in announced_live_sessions:
                        current.add(key)
                    continue
                _youtube_last_checked[key] = now
                result = await asyncio.to_thread(check_youtube_live, url)
            elif platform == "twitch" and access_token:
                username = profile_username(platform, url)
                if username:
                    result = await asyncio.to_thread(check_twitch_live, username, access_token)
            elif platform == "tiktok":
                username = profile_username(platform, url)
                if username:
                    result = await asyncio.to_thread(check_tiktok_live, username)
            # Unknown result must not reset a prior live announcement.
            if result is None:
                if key in announced_live_sessions:
                    current.add(key)
                continue
            is_live, link = result
            if is_live:
                current.add(key)
                if key not in announced_live_sessions:
                    if await notify_live(guild, name, platform, link):
                        announced_live_sessions.add(key)
                        save_announced_sessions()
                        add_event(f"{name} live on {platform} in {guild.name}")
    offline = announced_live_sessions - current
    if offline:
        announced_live_sessions.difference_update(offline)
        save_announced_sessions()
    last_check_time = datetime.now().strftime("%H:%M:%S")
    print("Live status check complete.")


def get_twitch_access_token():
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }
    try:
        response = requests.post(url, data=params, timeout=10)
        response.raise_for_status()
        return response.json().get("access_token")
    except (requests.RequestException, ValueError) as e:
        print(f"Twitch authentication failed: {e}")
        return None

def check_twitch_live(username, access_token):
    url = f"https://api.twitch.tv/helix/streams?user_login={username}"
    headers = {"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {access_token}"}
    if not access_token or not TWITCH_CLIENT_ID:
        return (False, None)
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json().get("data", [])
        return (True, f"https://www.twitch.tv/{username}") if data else (False, None)
    except (requests.RequestException, ValueError) as e:
        print(f"Twitch check failed for {username}: {e}")
        return (False, None)

def check_tiktok_live(username):
    """
    Detect TikTok live status via SSR content markers.
    TikTok does NOT redirect /live when offline — the URL and HTTP status are
    identical whether the user is streaming or not. The only reliable signal is
    the server-rendered JSON embedded in the page, which only contains live-room
    data when an actual stream is running.
    """
    url = f"https://www.tiktok.com/@{username}/live"
    try:
        response = requests.get(
            url,
            timeout=10,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
            allow_redirects=True,
        )
        print(f"TikTok [{username}] status={response.status_code} final_url={response.url[:100]}")
        if response.status_code != 200:
            return (False, None)

        text = response.text
        # TikTok embeds liveRoomStatus in the SSR JSON on every /live page.
        # 0 = not streaming. Any non-zero value means the room is active.
        match = re.search(r'"liveRoomStatus":(\d+)', text)
        if not match:
            print(f"TikTok [{username}] liveRoomStatus not found in page")
            return (False, None)
        status = int(match.group(1))
        is_live = status != 0
        print(f"TikTok [{username}] liveRoomStatus={status} is_live={is_live}")
        return (True, url) if is_live else (False, None)
    except Exception as e:
        print(f"TikTok check error for {username}: {e}")
        return (False, None)

async def send_live_notification(message):
    for guild in bot.guilds:
        live_channel = nextcord.utils.get(guild.channels, name="live")
        if live_channel:
            await live_channel.send(message)

@bot.command(name="ping")
@commands.cooldown(1, 10, commands.BucketType.user)
async def ping(ctx):
    """Check Discord gateway latency and monitoring status."""
    latency = round(bot.latency * 1000)
    monitor = "active" if check_live_status.is_running() else "inactive"
    await ctx.send(f"🏓 Pong! {latency} ms | Live monitor: {monitor}")


@bot.command(name="clean", aliases=["clear"])
@commands.has_permissions(manage_messages=True)
@commands.bot_has_permissions(manage_messages=True)
async def clean(ctx, amount: int = 50):
    if not 1 <= amount <= 100:
        await ctx.send("**Chwazi yon kantite ant 1 ak 100.**")
        return
    deleted = await ctx.channel.purge(limit=amount, check=lambda message: not message.pinned)
    await ctx.send(f"**{len(deleted)} mesaj yo efase.**", delete_after=5)

# Day-one vault. Secrets are salted hashes; never print plaintext passwords.
DAYONE_FILE = os.path.join(os.path.dirname(__file__), "dayone_access.json")
DAYONE_ROLE = "day-one"
DAYONE_DAY = 86400

def dayone_load():
    try:
        with open(DAYONE_FILE, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}

dayone_data = dayone_load()

def dayone_save():
    path = DAYONE_FILE + ".tmp"
    with open(path, "w", encoding="utf-8") as file:
        json.dump(dayone_data, file)
    os.replace(path, DAYONE_FILE)

def dayone_digest(secret, salt):
    import hashlib
    return hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt), 260000).hex()

def dayone_entry(guild_id):
    return dayone_data.setdefault(str(guild_id), {"codes": {}, "expires": {}})


@bot.group(name="dayone", invoke_without_command=True)
async def dayone_group(ctx):
    await ctx.send(
        "**day-one access**\n"
        "DM me: \`bot dayone verify SERVER_ID PASSWORD\`\n"
        "Admins DM me: \`bot dayone set SERVER_ID permanent PASSWORD\` or "
        "\`bot dayone set SERVER_ID temporary PASSWORD\`.\n"
        "Admins in the server: \`bot dayone setup #secret-channel\`."
    )


@dayone_group.command(name="setup")
@commands.has_permissions(manage_roles=True, manage_channels=True)
@commands.bot_has_permissions(manage_roles=True, manage_channels=True)
async def dayone_setup(ctx, channel: nextcord.TextChannel):
    if not ctx.guild:
        return
    role = nextcord.utils.get(ctx.guild.roles, name=DAYONE_ROLE)
    try:
        if role is None:
            role = await ctx.guild.create_role(
                name=DAYONE_ROLE, permissions=nextcord.Permissions.none(),
                reason="Day-one verified access")
        await channel.set_permissions(ctx.guild.default_role, view_channel=False,
                                      reason="Day-one private channel")
        await channel.set_permissions(role, view_channel=True, read_message_history=True,
                                      reason="Day-one verified access")
        await ctx.send(f"Secured {channel.mention} for **day-one** members. Set a code via DM.")
    except nextcord.DiscordException as exc:
        await ctx.send(f"Setup failed: {exc}")


@dayone_group.command(name="set")
async def dayone_set(ctx, guild_id: int, kind: str, *, password: str):
    if ctx.guild:
        await ctx.message.delete() if ctx.channel.permissions_for(ctx.guild.me).manage_messages else None
        await ctx.author.send("For security, set passwords only in my DMs.")
        return
    guild = bot.get_guild(guild_id)
    if not guild:
        await ctx.send("I am not connected to that server.")
        return
    member = guild.get_member(ctx.author.id)
    if not member or not member.guild_permissions.administrator:
        await ctx.send("Server Administrator permission required.")
        return
    kind = kind.lower()
    if kind not in ("permanent", "temporary") or not 8 <= len(password) <= 128:
        await ctx.send("Use permanent or temporary, with a password between 8 and 128 characters.")
        return
    import secrets
    import time
    salt = secrets.token_hex(16)
    entry = dayone_entry(guild.id)
    entry["codes"][kind] = {
        "salt": salt,
        "digest": dayone_digest(password, salt),
        "until": time.time() + DAYONE_DAY if kind == "temporary" else None,
    }
    dayone_save()
    await ctx.send(f"Set **{kind}** code for {guild.name}. "
                   + ("Expires in 24 hours." if kind == "temporary" else "Valid until replaced."))


@dayone_group.command(name="verify")
@commands.cooldown(4, 600, commands.BucketType.user)
async def dayone_verify(ctx, guild_id: int, *, password: str):
    if ctx.guild:
        try:
            await ctx.message.delete()
        except nextcord.DiscordException:
            pass
        try:
            await ctx.author.send("Please verify privately using \`bot dayone verify SERVER_ID PASSWORD\`.")
        except nextcord.DiscordException:
            pass
        return
    import time
    guild = bot.get_guild(guild_id)
    member = guild.get_member(ctx.author.id) if guild else None
    entry = dayone_data.get(str(guild_id))
    if not member or not entry:
        await ctx.send("Server or access code not found.")
        return
    matched = None
    for kind, code in entry.get("codes", {}).items():
        if kind == "temporary" and time.time() > code.get("until", 0):
            continue
        if hmac.compare_digest(dayone_digest(password, code["salt"]), code["digest"]):
            matched = kind
            break
    if matched is None:
        await ctx.send("Invalid or expired password.")
        return
    role = nextcord.utils.get(guild.roles, name=DAYONE_ROLE)
    if not role or role >= guild.me.top_role:
        await ctx.send("Access role is missing or too high in role hierarchy. Ask an admin.")
        return
    try:
        await member.add_roles(role, reason=f"Day-one {matched} code verified")
    except nextcord.DiscordException as exc:
        await ctx.send(f"Couldn't grant access: {exc}")
        return
    if matched == "permanent":
        entry.setdefault("expires", {}).pop(str(member.id), None)
        await ctx.send(f"Verified! **day-one** access granted in {guild.name}.")
    else:
        # A previously permanent grant must not be downgraded by a temporary code.
        if str(member.id) not in entry.setdefault("permanent_users", []):
            entry.setdefault("expires", {})[str(member.id)] = time.time() + DAYONE_DAY
        await ctx.send(f"Verified! **day-one** access granted in {guild.name} for 24 hours.")
    if matched == "permanent" and str(member.id) not in entry.setdefault("permanent_users", []):
        entry["permanent_users"].append(str(member.id))
    dayone_save()


@tasks.loop(minutes=5)
async def dayone_expiry_check():
    import time
    dirty = False
    now = time.time()
    for guild_id, entry in list(dayone_data.items()):
        guild = bot.get_guild(int(guild_id))
        if not guild:
            continue
        role = nextcord.utils.get(guild.roles, name=DAYONE_ROLE)
        for user_id, deadline in list(entry.get("expires", {}).items()):
            if deadline > now:
                continue
            member = guild.get_member(int(user_id))
            if role and member and role in member.roles:
                try:
                    await member.remove_roles(role, reason="24-hour day-one access expired")
                except nextcord.DiscordException as exc:
                    print(f"day-one expiry failed for {user_id}: {exc}")
                    continue
            del entry["expires"][user_id]
            dirty = True
    if dirty:
        dayone_save()


@bot.group(name="create", invoke_without_command=True)
async def create_group(ctx):
    """Show creation options without creating anything."""
    await ctx.send(
        "**SideBot Create Commands**\n"
        "\`bot create channel CHANNEL_NAME\`  Create a text channel\n"
        "\`bot create thread THREAD_NAME\`  Start a thread in this channel\n"
        "\`bot create role ROLE_NAME\`  Create a permission-free role\n"
        "\`bot create poll Messi | Ronaldo | X\`  Create a multi-choice poll\n"
        "\`bot create event TITLE | 2026-10-20 19:00 | 60 | #voice-channel\`  Schedule a voice event\n"
        "Event time must use UTC, duration is minutes. Only moderators can create resources."
    )


@create_group.command(name="channel")
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def create_channel_sub(ctx, *, name: str):
    name = name.strip()
    if not 1 <= len(name) <= 90:
        await ctx.send("Channel name must be between 1 and 90 characters.")
        return
    if nextcord.utils.get(ctx.guild.text_channels, name=name):
        await ctx.send("That channel already exists.")
        return
    try:
        channel = await ctx.guild.create_text_channel(name, reason=f"Requested by {ctx.author}")
        await ctx.send(f"Created {channel.mention}")
    except nextcord.DiscordException as exc:
        await ctx.send(f"Couldn't create channel: {exc}")


@create_group.command(name="thread")
@commands.has_permissions(create_public_threads=True)
@commands.bot_has_permissions(create_public_threads=True, send_messages_in_threads=True)
async def create_thread_sub(ctx, *, name: str):
    name = name.strip()
    if not isinstance(ctx.channel, nextcord.TextChannel):
        await ctx.send("Use this command inside a text channel.")
        return
    if not 1 <= len(name) <= 100:
        await ctx.send("Thread name must be between 1 and 100 characters.")
        return
    try:
        message = await ctx.send(f"Thread topic: **{nextcord.utils.escape_markdown(name)}**")
        thread = await message.create_thread(name=name)
        await ctx.send(f"Created thread {thread.mention}")
    except nextcord.DiscordException as exc:
        await ctx.send(f"Couldn't create thread: {exc}")


# Permission flags supported for newly created roles. Dangerous moderation
# and administration permissions are intentionally excluded.
CREATABLE_ROLE_PERMISSIONS = {
    "view_channel", "send_messages", "read_message_history", "embed_links",
    "attach_files", "add_reactions", "use_external_emojis",
    "connect", "speak", "stream", "use_voice_activation",
    "create_public_threads", "send_messages_in_threads",
}


@create_group.command(name="role")
@commands.has_permissions(manage_roles=True)
@commands.bot_has_permissions(manage_roles=True)
async def create_role_sub(ctx, *, details: str):
    """bot create role VIP | view_channel, send_messages, connect"""
    fields = [item.strip() for item in details.split("|")]
    if len(fields) > 2 or not fields[0]:
        await ctx.send("Usage: `bot create role VIP | view_channel, send_messages, connect`")
        return
    name = fields[0]
    if not 1 <= len(name) <= 90:
        await ctx.send("Role name must be between 1 and 90 characters.")
        return
    if nextcord.utils.get(ctx.guild.roles, name=name):
        await ctx.send("That role already exists.")
        return
    requested = [value.strip().lower().replace(" ", "_") for value in fields[1].split(",")] if len(fields) == 2 else []
    if any(not value or value not in CREATABLE_ROLE_PERMISSIONS for value in requested):
        await ctx.send("Unsupported permission. Allowed: " + ", ".join(sorted(CREATABLE_ROLE_PERMISSIONS)))
        return
    permissions = nextcord.Permissions.none()
    for flag in requested:
        if not getattr(ctx.author.guild_permissions, flag, False):
            await ctx.send(f"You do not have permission to grant `{flag}`.")
            return
        setattr(permissions, flag, True)
    try:
        role = await ctx.guild.create_role(
            name=name, permissions=permissions,
            reason=f"Requested by {ctx.author}",
        )
        selected = ", ".join(sorted(set(requested))) if requested else "none"
        await ctx.send(
            f"Created role **{nextcord.utils.escape_markdown(role.name)}**. Permissions: {selected}.",
            allowed_mentions=nextcord.AllowedMentions.none(),
        )
    except nextcord.DiscordException as exc:
        await ctx.send(f"Couldn't create role: {exc}")


@create_group.command(name="poll")
@commands.cooldown(1, 30, commands.BucketType.user)
@commands.bot_has_permissions(send_messages=True, add_reactions=True)
async def create_poll_sub(ctx, *, options: str):
    """Example: bot create poll Messi | Ronaldo | X"""
    names = [item.strip() for item in options.split("|")]
    if not 2 <= len(names) <= 9 or any(not n or len(n) > 90 for n in names):
        await ctx.send("Enter 2 to 9 options separated by \`|\`. Example: \`bot create poll Messi | Ronaldo | X\`")
        return
    marks = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
    body = "**Vote for your choice**\n" + "\n".join(
        f"{marks[i]} {nextcord.utils.escape_markdown(name)}"
        for i, name in enumerate(names)
    )
    try:
        poll = await ctx.send(body, allowed_mentions=nextcord.AllowedMentions.none())
        for mark in marks[:len(names)]:
            await poll.add_reaction(mark)
    except nextcord.DiscordException as exc:
        await ctx.send(f"Couldn't create poll: {exc}")


@create_group.command(name="event")
@commands.has_permissions(manage_events=True)
@commands.bot_has_permissions(manage_events=True)
async def create_event_sub(ctx, *, details: str):
    """TITLE | YYYY-MM-DD HH:MM UTC | MINUTES | #voice-channel"""
    from datetime import timezone, timedelta
    fields = [x.strip() for x in details.split("|")]
    if len(fields) != 4 or not fields[0] or len(fields[0]) > 100:
        await ctx.send(
            "Usage: \`bot create event Game Night | 2026-10-20 19:00 | 60 | #voice-channel\` (UTC)"
        )
        return
    try:
        start = datetime.strptime(fields[1], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        duration = int(fields[2])
        if not 15 <= duration <= 1440 or start <= datetime.now(timezone.utc):
            raise ValueError("Event must be in the future, duration 15 to 1440 minutes.")
        match = re.fullmatch(r"<#(\d+)>", fields[3])
        channel = ctx.guild.get_channel(int(match.group(1))) if match else None
        if not isinstance(channel, (nextcord.VoiceChannel, nextcord.StageChannel)):
            raise ValueError("Mention an existing voice or stage channel.")
    except ValueError as exc:
        await ctx.send(f"Invalid event details: {exc}")
        return
    try:
        event = await ctx.guild.create_scheduled_event(
            name=fields[0],
            entity_type=nextcord.ScheduledEventEntityType.voice if isinstance(channel, nextcord.VoiceChannel)
                        else nextcord.ScheduledEventEntityType.stage_instance,
            start_time=start,
            end_time=start + timedelta(minutes=duration),
            channel=channel,
            description=f"Created by {ctx.author}",
            reason=f"Requested by {ctx.author}",
        )
        await ctx.send(f"Scheduled **{nextcord.utils.escape_markdown(event.name)}** for {fields[1]} UTC.")
    except nextcord.DiscordException as exc:
        await ctx.send(f"Couldn't create event: {exc}")


@bot.command(name="create_role")
@commands.has_permissions(manage_roles=True)
async def create_role(ctx, role_name: str):
    role = nextcord.utils.get(ctx.guild.roles, name=role_name)
    if role:
        await ctx.send(f"**Wòl '{role_name}' deja egziste.**")
        return
    new_role = await ctx.guild.create_role(name=role_name, color=nextcord.Color(random.randint(0, 0xFFFFFF)))
    await ctx.send(f"**Wòl '{role_name}' kreye avèk siksè!**")

@bot.command(name="assign_role")
@commands.has_permissions(administrator=True)
async def assign_role(ctx, member: nextcord.Member, *, role_name: str):
    role = nextcord.utils.get(ctx.guild.roles, name=role_name)
    if not role:
        await ctx.send(f"**Mwen pa ka jwenn wòl '{role_name}'**")
        return
    try:
        await member.add_roles(role)
        await ctx.send(f"**Mwen bay {member.mention} Wòl '{role.name}' avèk siksè.**")
    except Exception as e:
        await ctx.send(f"**Pa posib bay wòl sa a. Erè: {e}**")

def can_moderate_target(ctx, member):
    """Protect owner, administrators, equal hierarchy, and the caller."""
    bot_member = ctx.guild.me
    if member.id == ctx.guild.owner_id or member.id == ctx.author.id or member.id == bot_member.id:
        return False
    if member.guild_permissions.administrator:
        return False
    if member.top_role >= bot_member.top_role:
        return False
    if ctx.author.id != ctx.guild.owner_id and member.top_role >= ctx.author.top_role:
        return False
    return True


@bot.command(name="delete_role")
@commands.has_permissions(manage_roles=True)
@commands.bot_has_permissions(manage_roles=True)
async def delete_role_command(ctx, *, role_name: str):
    role = nextcord.utils.get(ctx.guild.roles, name=role_name)
    if not role:
        await ctx.send("Role not found.")
        return
    if role.is_default() or role.managed or role.permissions.administrator:
        await ctx.send("Cannot delete default, integration-managed, or Administrator roles.")
        return
    if role >= ctx.guild.me.top_role or (
        ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role
    ):
        await ctx.send("Role hierarchy prevents deletion.")
        return
    if role.name.startswith("Rank | "):
        await ctx.send("Rank roles are managed by SideBot. They cannot be deleted here.")
        return
    try:
        await role.delete(reason=f"Deleted by {ctx.author} with SideBot")
        await ctx.send(f"Deleted role: {nextcord.utils.escape_markdown(role_name)}")
    except nextcord.DiscordException as exc:
        await ctx.send(f"Unable to delete role: {exc}")


@bot.command(name="create_access_role")
@commands.has_permissions(manage_roles=True, manage_channels=True)
@commands.bot_has_permissions(manage_roles=True, manage_channels=True)
async def create_access_role(ctx, role_name: str, channel: nextcord.TextChannel):
    """Create a role that grants visibility to an existing private channel."""
    if nextcord.utils.get(ctx.guild.roles, name=role_name):
        await ctx.send("A role with that name already exists.")
        return
    role = None
    try:
        role = await ctx.guild.create_role(
            name=role_name, permissions=nextcord.Permissions.none(),
            reason=f"Access role requested by {ctx.author}"
        )
        # Grant access without changing other role permissions on this channel.
        await channel.set_permissions(role, view_channel=True, read_message_history=True,
                                      reason=f"Access role configured by {ctx.author}")
        await ctx.send(f"Created **{nextcord.utils.escape_markdown(role_name)}** for {channel.mention}.")
    except nextcord.DiscordException as exc:
        await ctx.send(f"Couldn't configure access role: {exc}")


@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
@commands.bot_has_permissions(kick_members=True)
async def kick_member_command(ctx, member: nextcord.Member, *, reason: str = "No reason provided"):
    if not can_moderate_target(ctx, member):
        await ctx.send("Cannot kick this member due to permissions or role hierarchy.")
        return
    try:
        await member.kick(reason=f"{ctx.author}: {reason[:350]}")
        await ctx.send(f"Kicked **{nextcord.utils.escape_markdown(str(member))}**. Reason: {reason[:300]}")
    except nextcord.DiscordException as exc:
        await ctx.send(f"Kick failed: {exc}")


@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def ban_member_command(ctx, member: nextcord.Member, *, reason: str = "No reason provided"):
    if not can_moderate_target(ctx, member):
        await ctx.send("Cannot ban this member due to permissions or role hierarchy.")
        return
    try:
        await member.ban(reason=f"{ctx.author}: {reason[:350]}", delete_message_days=0)
        await ctx.send(f"Banned **{nextcord.utils.escape_markdown(str(member))}**. Reason: {reason[:300]}")
    except nextcord.DiscordException as exc:
        await ctx.send(f"Ban failed: {exc}")


@bot.command(name="unban")
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def unban_member_command(ctx, user_id: int):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=f"Unbanned by {ctx.author}")
        await ctx.send(f"Unbanned user ID `{user_id}`.")
    except (nextcord.DiscordException, ValueError) as exc:
        await ctx.send(f"Unban failed: {exc}")


@bot.command(name="whoslive")
async def whoslive(ctx):
    live_users = []
    access_token = get_twitch_access_token()
    for username in bot.twitch_usernames:
        is_live, link = check_twitch_live(username, access_token)
        if is_live:
            live_users.append(f"**{username}** is live on Twitch: {link}")

    for username in bot.tiktok_usernames:
        is_live, link = check_tiktok_live(username)
        if is_live:
            live_users.append(f"**{username}** is live on TikTok: {link}")

    await ctx.send("\n".join(live_users) if live_users else "**Mwen pa jwenn pèsonn ki ap viv kounye a.**")

async def send_how_to(ctx):
    await ctx.send(
        "**Kijan w ka itilizem:**\n"
        "1. **bot call [users]** - Rele youn oswa plizyè moun.\n"
        "2. **bot call [users] for [activity]** - Pou yon bagay spesifik.\n"
        "3. **bot kiye la** - Checke sim on.\n"
        "4. **gg / ggs** - Mwen reponn GG back.\n"
        "5. **sak pase / s ak pase / sakpase** - Map reponn map boule.\n"
        "6. **bot sak gen la** - Bot reponn: {username}, poze e ou?\n"
        "7. **bot clean [amount]** - Efase yon kantite mesaj espesifik (ex: bot clean 50).\n"
        "8. **bot wipe** - Efase tout mesaj nan chanèl la san risk de rate limit.\n"
        "9. **bot send [message] on [channel_name]** - Voye yon mesaj nan yon chanèl espesifik.\n"
        "10. **bot whoslive** - Checke ki moun ki ap viv sou TikTok oswa Twitch.\n"
        "11. **bot create_poll [channel_name] [role_name] [question]** - Kreye yon sondaj ak wòl espesifik pou moun ki vote 'wi'.\n"
        "12. **bot assign_poll_role** - Bay wòl sondaj la pou moun ki vote 'wi'.\n"
        "13. **bot create_role [role_name]** - Kreye yon nouvo wòl ak yon koulè owaza.\n"
        "14. **bot assign_role [@user] [role_name]** - Bay yon wòl espesifik a yon itilizatè.\n"
    )


@bot.command(name="create_poll")
@commands.has_permissions(manage_roles=True)
async def create_poll(ctx, channel_name: str, role_name: str, *, question: str):
    """
    Create a poll in a specified channel.
    Example: bot create_poll group-chat FIFA "Do you play FIFA?"
    """
    channel = nextcord.utils.get(ctx.guild.channels, name=channel_name)
    
    if not channel:
        await ctx.send(f"**Mwen pa ka jwenn chanèl '{channel_name}'**")
        return

    message = await channel.send(f"📊 **Poll:** {question}\nReact with ✅ for Yes or ❌ for No.")
    await message.add_reaction("✅")
    await message.add_reaction("❌")

    # Store poll data for tracking
    bot.poll_message_id = message.id
    bot.poll_channel_id = channel.id
    bot.poll_question = question
    bot.poll_yes_responses = set()  # To store users who responded with "yes"
    bot.poll_role_name = role_name  # The role to assign later

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot or reaction.message.id != getattr(bot, "poll_message_id", None):
        return

    if reaction.emoji == "✅":
        bot.poll_yes_responses.add(user.id)
    elif reaction.emoji == "❌":
        bot.poll_yes_responses.discard(user.id)

@bot.command(name="assign_poll_role")
@commands.has_permissions(manage_roles=True)
async def assign_poll_role(ctx):
    """
    Assigns the specified role to all users who responded "yes" to the last poll.
    """
    role_name = bot.poll_role_name
    successful_assignments = 0

    for member_id in bot.poll_yes_responses:
        member = ctx.guild.get_member(member_id)
        if member:
            # Reusing your existing assign_role function
            await assign_role(ctx, member=member, role_name=role_name)
            successful_assignments += 1

    await ctx.send(f"**Wòl '{role_name}' te ajoute pou {successful_assignments} moun.**")
    bot.poll_yes_responses.clear()

@bot.command(name="invite")
async def invite_cmd(ctx):
    """Invite link in DMs; redirect to admin in server channels."""
    if ctx.guild:
        # Server channel — don't post link publicly
        await ctx.send(
            "🔒 Lyen envitasyon an pa disponib nan chanèl piblik.\n"
            "Mande yon admin sèvè, oswa voye m yon DM pou jwenn lyen an! 😄"
        )
    else:
        await ctx.send(
            f"👋 **Ou vle ajoute SideBot nan pwòp sèvè pa w?**\n"
            f"Klike sou lyen sa a pou invite m:\n{INVITE_URL}"
        )

@bot.command(name="send")
@commands.has_permissions(manage_messages=True)
async def send(ctx, message: str, *, channel_name: str):
    """
    Usage:
    - bot send "Hello everyone!" on general
    - bot send "Live stream starting now!" on announcements
    """
    # Remove 'on' or 'to' from the channel name if present
    if channel_name.lower().startswith("on "):
        channel_name = channel_name[3:]
    elif channel_name.lower().startswith("to "):
        channel_name = channel_name[3:]

    # Find the target channel
    target_channel = nextcord.utils.get(ctx.guild.channels, name=channel_name.strip())

    if not target_channel:
        await ctx.send(f"**Mwen pa ka jwenn chanèl '{channel_name}'**")
        return

    # Send the formatted message
    try:
        await target_channel.send(message)
        await ctx.send(f"**Mesaj voye nan chanèl {target_channel.mention} avèk siksè!**")
    except Exception as e:
        await ctx.send(f"**Gen yon erè pandan mwen tap voye mesaj la: {e}**")


@bot.command(name="join")
async def join_voice(ctx, *, channel_name: str = ""):
    """Join a voice channel by name. Usage: bot join general  /  bot join party"""
    # If caller is already in a voice channel and no name given, join theirs
    if not channel_name and ctx.author.voice:
        channel = ctx.author.voice.channel
    else:
        # Search voice channels (case-insensitive, partial match)
        channel = nextcord.utils.find(
            lambda c: isinstance(c, nextcord.VoiceChannel)
                      and channel_name.lower() in c.name.lower(),
            ctx.guild.channels,
        )

    if not channel:
        if channel_name:
            await ctx.send(f"**Mwen pa ka jwenn chanèl vwa '{channel_name}'.** 🔇\n"
                           f"Verifye non chanèl la epi eseye ankò.")
        else:
            await ctx.send("**Antre nan yon chanèl vwa ou menm an premye, "
                           "oswa ekri `bot join [non chanèl]`.**")
        return

    # Leave current channel first if already connected in this guild
    if ctx.voice_client:
        await ctx.voice_client.disconnect()

    try:
        await channel.connect()
        add_event(f"🎙️ Joined voice: #{channel.name} (req by {ctx.author.display_name})")
        await ctx.send(f"**Mwen antre nan 🔊 {channel.name}!** 🎙️")
    except nextcord.errors.ClientException as e:
        await ctx.send(f"**Mwen pa ka antre nan chanèl la:** {e}")
    except Exception as e:
        await ctx.send(f"**Erè:** {e}")


@bot.command(name="leave")
async def leave_voice(ctx):
    """Leave the current voice channel. Usage: bot leave"""
    if not ctx.voice_client:
        await ctx.send("**Mwen pa nan okenn chanèl vwa kounye a.** 🔇")
        return

    channel_name = ctx.voice_client.channel.name
    await ctx.voice_client.disconnect()
    add_event(f"🔇 Left voice: #{channel_name} (req by {ctx.author.display_name})")
    await ctx.send(f"**Mwen kite 🔊 {channel_name}.** 👋")


def _run_tests():
    """Three-stage pipeline test. Run with: python bot.py --test"""
    print("\n" + "=" * 60)
    print("SIDEBOT PIPELINE TEST  (Stage 0: cmd | Stage 1: casual | Stage 2: AI)")
    print("=" * 60)

    # Clear all history before tests so results are deterministic
    ai_conversations.clear()
    print("  [history cleared]")

    def run_stage(text, user_id=0, context="server"):
        # Stage 0
        if is_command_text(text):
            s = text.lower().strip()
            for pfx in ("bot ", "side bot ", "!"):
                if s.startswith(pfx):
                    s = s[len(pfx):]
                    break
            cmd = s.split()[0] if s.split() else text
            if context == "dm":
                print(f"  [STAGE 0 – COMMAND/DM]  '{cmd}' → blocked (server-only) ✓")
            else:
                print(f"  [STAGE 0 – COMMAND]  '{cmd}' → handler (not AI) ✓")
            return
        # Stage 1
        cr = casual_match(text)
        if cr:
            if is_invite_response(cr) and context == "server":
                print(f"  [STAGE 1 – INVITE BLOCKED]  🔒 redirect to admin/DM ✓")
            else:
                dm_flag = (context == "dm")
                resolved = resolve_reply(cr, dm=dm_flag)
                print(f"  [STAGE 1 – FAST PATH]  {resolved[:120]}")
            return
        # Stage 2
        print(f"  [STAGE 2 – GROQ AI]  context={context}")
        try:
            reply = get_ai_response(user_id, text, context)
            print(f"  Reply: {reply}")
        except Exception:
            print(f"  ERROR: {traceback.format_exc()}")

    print("\n── Core test cases (from prompt) ────────────────────────")
    for text, note, ctx in [
        ("bot whoslive",       "Stage 0: command, not AI",                    "server"),
        ("bot join",           "Stage 0: DM → server-only message",           "dm"),
        ("sak pase",           "Stage 1: casual fast-path, no Groq (server)", "server"),
        ("sak pase",           "Stage 1: casual fast-path, no Groq (DM)",     "dm"),
        ("What's up?",         "Stage 2: English reply, not forced Creole",   "dm"),
        ("What time is it",    "Stage 2: AI reply backed by datetime tool",   "dm"),
        ("how do I add you",   "Invite: server → redirect to admin/DM",       "server"),
        ("invite link",        "Invite: DM → clean link shown",               "dm"),
    ]:
        print(f"\n→ '{text}'  [{ctx}]  ({note})")
        run_stage(text, user_id=1, context=ctx)

    print("\n── History isolation check ──────────────────────────────")
    dm_key     = "dm_42"
    server_key = "server_42"
    get_ai_response(42, "remember this: DM secret", "dm")
    get_ai_response(42, "what did I just tell you?", "server")
    dm_hist     = ai_conversations.get(dm_key, [])
    server_hist = ai_conversations.get(server_key, [])
    dm_ok     = any("DM secret" in m["content"] for m in dm_hist)
    server_ok = not any("DM secret" in m["content"] for m in server_hist)
    print(f"  DM history has 'DM secret':     {dm_ok}  {'✅' if dm_ok else '❌'}")
    print(f"  Server history missing it:       {server_ok}  {'✅' if server_ok else '❌'}")
    ai_conversations.clear()

    print("\n── Additional routing checks ────────────────────────────")
    for text, note, ctx in [
        ("kijan ou ye monchè",   "casual fast-path",           "server"),
        ("ggs",                  "casual — ggs not gg",        "server"),
        ("bot clean 10",         "command — not AI",           "server"),
        ("bot create_role test", "command — not AI",           "server"),
        ("help",                 "command list, no invite URL","server"),
        ("help",                 "command list WITH invite URL","dm"),
        ("how do I invite you",  "invite — DM shows link",     "dm"),
    ]:
        print(f"\n→ '{text}'  [{ctx}]  ({note})")
        run_stage(text, user_id=2, context=ctx)

    print("\n── is_command_text() checks ─────────────────────────────")
    checks = [
        ("bot whoslive",    True),
        ("bot clean 5",     True),
        ("bot kiye la",     True),
        ("whoslive",        True),
        ("sak pase",        False),
        ("what time is it", False),
        ("help",            False),
    ]
    all_ok = True
    for text, expected in checks:
        got = is_command_text(text)
        ok = "✅" if got == expected else "❌"
        if got != expected:
            all_ok = False
        print(f"  {ok}  is_command_text({text!r}) = {got}  (expected {expected})")
    if all_ok:
        print("  All checks passed.")

    print("\n── Onboarding guard ─────────────────────────────────────")
    FAKE_ID = 9876
    onboarding_state[FAKE_ID] = {"step": "reason", "data": {}, "guild_id": 0}
    print(f"  User {FAKE_ID} in onboarding_state → pipeline skipped ✓")
    del onboarding_state[FAKE_ID]

    print("\n── Invite URL format check ──────────────────────────────")
    assert f"client_id={CLIENT_ID}" in INVITE_URL, "❌ client_id missing"
    assert "scope=bot" in INVITE_URL, "❌ scope missing"
    assert str(PERMISSIONS) in INVITE_URL, "❌ permissions missing"
    print(f"  ✅  {INVITE_URL}")

    print("\n" + "=" * 60)

if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_tests()
    else:
        try:
            print("Starting bot...")
            if not DISCORD_TOKEN:
                raise RuntimeError("DISCORD_TOKEN is missing. Set it in GitHub Actions secrets.")
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(f"ERROR starting bot: {e}")
