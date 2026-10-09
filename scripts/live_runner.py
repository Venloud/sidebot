"""Run SideBot inside one GitHub Actions job and reload committed code safely."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_DIR = ROOT / "SideBot"
CHECK_SECONDS = 60
MAX_SECONDS = 5 * 3600 + 40 * 60
GRACE_SECONDS = 25
stop_requested = False


def request_stop(_signum, _frame):
    global stop_requested
    stop_requested = True


signal.signal(signal.SIGINT, request_stop)
signal.signal(signal.SIGTERM, request_stop)


def git(*args, check=True):
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=check,
        capture_output=True, text=True,
    )


def stop_bot(process):
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def launch_bot():
    print("[supervisor] Launching SideBot", flush=True)
    return subprocess.Popen([sys.executable, "-u", "bot.py"], cwd=BOT_DIR)


def check_for_updates(process):
    """Keep running old code if a new commit cannot pass basic validation."""
    try:
        git("fetch", "--quiet", "--no-tags", "origin", "main")
        current = git("rev-parse", "HEAD").stdout.strip()
        latest = git("rev-parse", "origin/main").stdout.strip()
        if current == latest:
            return process

        changed = git("diff", "--name-only", current, latest).stdout.splitlines()
        print(f"[supervisor] New commit {latest[:12]}; changed: {', '.join(changed)}", flush=True)

        # Validate new Python sources before touching the running process.
        source = git("show", "origin/main:SideBot/bot.py").stdout
        compile(source, "SideBot/bot.py", "exec")
        if "requirements.txt" in changed:
            requirements = git("show", "origin/main:requirements.txt").stdout
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", "/dev/stdin"],
                input=requirements, text=True, check=True, cwd=ROOT,
            )

        code_changed = any(
            p.startswith("SideBot/") or p == "requirements.txt"
            for p in changed
        )
        if code_changed:
            print("[supervisor] Restarting bot to load new code", flush=True)
            stop_bot(process)
        git("reset", "--hard", "origin/main")
        return launch_bot() if code_changed else process
    except (subprocess.CalledProcessError, SyntaxError) as exc:
        print(f"[supervisor] Update rejected, keeping prior code: {exc}", flush=True)
        return process


def main():
    if not os.getenv("DISCORD_TOKEN"):
        print("Missing DISCORD_TOKEN GitHub secret", file=sys.stderr)
        return 1
    deadline = time.monotonic() + MAX_SECONDS
    process = launch_bot()
    try:
        while not stop_requested and time.monotonic() < deadline:
            for _ in range(CHECK_SECONDS):
                if stop_requested or time.monotonic() >= deadline:
                    break
                status = process.poll()
                if status is not None:
                    print(f"[supervisor] Bot stopped unexpectedly with exit code {status}", flush=True)
                    return status if status else 1
                time.sleep(1)
            if not stop_requested and time.monotonic() < deadline:
                process = check_for_updates(process)
        print("[supervisor] Run ending, stopping bot and saving state", flush=True)
        return 0
    finally:
        stop_bot(process)


if __name__ == "__main__":
    sys.exit(main())
