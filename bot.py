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
    command_prefix=commands.when_mentioned_or("!", "side bot ", "bot "),
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
    "whoslive", "clean", "create_role", "assign_role",
    "create_poll", "assign_poll_role", "invite", "send", "wipe",
    "join", "leave",
}
_INLINE_COMMANDS = {"kiye la"}  # handled inline in on_message, not via @bot.command

def is_command_text(text: str) -> bool:
    """Return True if text looks like a bot command (with or without 'bot ' prefix)."""
    s = text.lower().strip()
    for pfx in ("bot ", "side bot ", "!"):
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

@bot.event
async def on_ready():
    global bot_start_time
    bot_start_time = datetime.utcnow().isoformat()
    print(f"Bot is ready! Logged in as {bot.user.name}")
    print("Mwen back anbet! 🚀")
    threading.Thread(target=run_web_server, daemon=True).start()
    add_event(f"Bot online as {bot.user.name}")
    check_live_status.start()

@bot.event
async def on_guild_join(guild):
    """Send a simple welcome message when the bot joins a new server."""
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
    # Give all non-admin, non-managed roles below the bot's highest role
    roles_to_give = [
        role for role in member.guild.roles
        if not role.is_default()
        and not role.managed
        and not role.permissions.administrator
        and role < member.guild.me.top_role
    ]
    if roles_to_give:
        try:
            await member.add_roles(*roles_to_give, reason="Auto all-access on join")
            print(f"Assigned {len(roles_to_give)} role(s) to {member.name}")
        except Exception as e:
            print(f"Could not assign roles to {member.name}: {e}")

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


@bot.event
async def on_message(message):
    if message.author == bot.user:
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

    if "bot kiye la" in content:
        online_members = [member.mention for member in message.channel.members
                          if member.status != nextcord.Status.offline and not member.bot]
        await message.channel.send(
            " ".join(online_members) if online_members else "**Mwen pa jwen moun ki online.**"
        )

    await bot.process_commands(message)

@tasks.loop(minutes=1)
async def check_live_status():
    global last_check_time
    print("Running live status check...")
    access_token = get_twitch_access_token()
    currently_live = set()

    for username in bot.twitch_usernames:
        is_live, link = check_twitch_live(username, access_token)
        if is_live:
            currently_live.add(username)
            if username not in announced_live_sessions:
                announced_live_sessions.add(username)
                save_announced_sessions()
                add_event(f"🔴 {username} went live on Twitch")
                await send_live_notification(f"**{username}** ap fè live sou Twitch! 🔴 {link}")

    for username in bot.tiktok_usernames:
        is_live, link = check_tiktok_live(username)
        if is_live:
            currently_live.add(username)
            if username not in announced_live_sessions:
                announced_live_sessions.add(username)
                save_announced_sessions()
                add_event(f"🔴 {username} went live on TikTok")
                await send_live_notification(f"**{username}** ap fè live sou TikTok! 🔴 {link}")

    # Remove anyone who went offline so they get announced again next stream
    went_offline = announced_live_sessions - currently_live
    if went_offline:
        announced_live_sessions.difference_update(went_offline)
        save_announced_sessions()
        for u in went_offline:
            add_event(f"⚫ {u} went offline")
        print(f"Went offline, removed from announced: {went_offline}")

    last_check_time = datetime.now().strftime("%H:%M:%S")
    print("Live status check complete.")

def get_twitch_access_token():
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }
    response = requests.post(url, params=params)
    return response.json().get("access_token") if response.status_code == 200 else None

def check_twitch_live(username, access_token):
    url = f"https://api.twitch.tv/helix/streams?user_login={username}"
    headers = {"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)
    data = response.json().get("data", [])
    return (True, f"https://www.twitch.tv/{username}") if data else (False, None)

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

@bot.command(name="clean")
@commands.has_permissions(manage_messages=True)
async def clean(ctx, amount: int = 50):
    await ctx.channel.purge(limit=amount)
    await ctx.send(f"**{amount} mesaj yo efase.**", delete_after=5)

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
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(f"ERROR starting bot: {e}")
