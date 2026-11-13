"""Start the agent's persistent Chrome (CDP on 127.0.0.1:9223) unless it is already running.

    uv run python scripts/start-agent0-chrome.py
"""
import fcntl
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path


def chrome_up():
    try:
        urllib.request.urlopen('http://127.0.0.1:9223/json/version', timeout=2).close()
        return True
    except OSError:
        return False


if __name__ == '__main__':
    # A lock, so two callers never start two Chromes on one profile
    lock_path = Path.home() / '.cache/agent0-chrome-start.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if chrome_up():
            raise SystemExit(0)
        # Its own systemd scope: an OOM kill of Chrome must not take the caller's scope (Paseo, tmux) down with it
        log = Path(__file__).resolve().parents[1] / 'tmp/agent0-chrome.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(['systemd-run', '--user', '--scope', '--quiet', f'--unit=agent0-chrome-{datetime.now():%Y%m%d%H%M%S}',
                          '-p', 'OOMPolicy=continue', '--', 'google-chrome', f'--user-data-dir={Path.home()}/.config/agent0/chrome',
                          '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=9223', '--restore-last-session',
                          '--no-first-run', '--no-default-browser-check'],
                         stdin=subprocess.DEVNULL, stdout=log.open('a'), stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(150):
            if chrome_up():
                raise SystemExit(0)
            time.sleep(0.2)
        raise SystemExit(f"The agent's Chrome did not come up within 30 s; see {log}")
