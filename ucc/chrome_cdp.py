"""
ucc/chrome_cdp.py

Shared helpers for driving a real, already-running Chrome over CDP
(connect_over_cdp) instead of a Playwright-launched browser -- confirmed
2026-09-15 that this is what it takes to pass NY's Cloudflare Turnstile
challenge, and Ohio's newly-observed Cloudflare bot-management challenge
(escalated from a plain app-level 429 earlier the same day) looks like it
needs the same fix. A Playwright-launched Chromium -- even non-headless,
even with anti-detection args like OH's existing
--disable-blink-features=AutomationControlled -- carries automation
signals a genuine, independently-launched Chrome process doesn't.

One shared Chrome instance/profile is reused across states by default
(CDP_URL/CHROME_USER_DATA_DIR below) -- states run one at a time in
practice (see main.py --ucc-states), and different origins don't share
cookies anyway, so there's no real benefit to separate browser processes.
Pass a different cdp_url/user_data_dir if a state ever needs isolation.
"""
from __future__ import annotations
import logging
import socket
import subprocess
import time
import urllib.request
import json as _json

logger = logging.getLogger(__name__)

CDP_URL = "http://localhost:9222"
CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_USER_DATA_DIR = "/tmp/chrome-debug-ny-automation"


def cdp_port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=timeout):
            return True
    except OSError:
        return False


def cdp_has_open_page(port: int) -> bool:
    """True if Chrome's DevTools HTTP endpoint reports at least one open
    'page' target. playwright's connect_over_cdp() fails outright (not
    just context creation) if the browser has zero open tabs at connect
    time -- confirmed 2026-09-15 -- so this must be checked/fixed via
    Chrome's plain HTTP endpoint, not Playwright itself (chicken-and-egg:
    can't use Playwright to open the first tab if Playwright can't
    connect at all until a tab exists)."""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/json/list", timeout=2) as r:
            targets = _json.loads(r.read())
        return any(t.get("type") == "page" for t in targets)
    except Exception:
        return False


def cdp_open_blank_tab(port: int) -> None:
    # Newer Chrome requires PUT for /json/new (GET returns 405).
    req = urllib.request.Request(f"http://localhost:{port}/json/new", method="PUT")
    urllib.request.urlopen(req, timeout=5).read()


def ensure_chrome_cdp(
    cdp_url: str = CDP_URL,
    user_data_dir: str = CHROME_USER_DATA_DIR,
    wait_seconds: float = 20.0,
) -> None:
    """Launch a real (non-headless) Chrome with a CDP debug port if one
    isn't already listening there. Re-uses a persistent profile dir so
    cookies/clearance (e.g. Cloudflare) can carry over between runs.
    Idempotent: safe to call even if Chrome is already up on this port.

    Also guarantees at least one tab stays open -- see cdp_has_open_page.
    A prior run's workers can leave Chrome with zero tabs (each worker
    closes its own page when done), which breaks the *next* connection
    attempt entirely, not just this one -- so this keep-alive check runs
    every call, not just on fresh launch."""
    port = int(cdp_url.rsplit(":", 1)[-1])
    if not cdp_port_open(port):
        logger.info("Launching Chrome (CDP port %d)", port)
        subprocess.Popen(
            [
                CHROME_BINARY,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={user_data_dir}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if cdp_port_open(port):
                time.sleep(1.5)  # let the browser process fully settle
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"Chrome did not come up on CDP port {port} within {wait_seconds}s")
    else:
        logger.info("Chrome already listening on CDP port %d", port)

    if not cdp_has_open_page(port):
        logger.info("Chrome has zero open tabs -- opening a keep-alive blank tab")
        cdp_open_blank_tab(port)


# ── Tab workers that can't hang the run ──────────────────────────────────
# 2026-10-13: a NY run finished all 2,529 searches, then sat for 10 minutes
# because two workers' page.close() never returned: their tabs still held a
# Cloudflare Turnstile frame. Nothing commits until the UCC step returns, so
# the whole state's results were stuck. Closing the two tabs over Chrome's
# HTTP endpoint released them. run_tab_workers does that automatically.

def page_target_id(context, page) -> str | None:
    """Chrome's id for a Playwright page, so another thread can close the
    tab over HTTP (Playwright objects are tied to their own thread)."""
    try:
        cdp = context.new_cdp_session(page)
        tid = cdp.send("Target.getTargetInfo")["targetInfo"]["targetId"]
        cdp.detach()
        return tid
    except Exception as e:
        logger.warning(f"Couldn't read the tab's target id ({e}); a stuck close can't be forced")
        return None


def close_target(target_id: str, port: int = 9222) -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{port}/json/close/{target_id}", timeout=5).read()
        return True
    except Exception as e:
        logger.warning(f"Couldn't close tab {target_id}: {e}")
        return False


def run_tab_workers(work, chunks: list, label: str, port: int = 9222, close_grace: float = 60.0) -> list:
    """Run work(chunk, out, tab) for each chunk in its own daemon thread and
    return every worker's results. A worker appends results to `out` as it
    goes, sets tab["id"] (page_target_id) once its tab is open, and sets
    tab["searched"] (time.time()) when its searches are done, before it
    closes the tab. A worker still running close_grace seconds after its
    searches finished is stuck closing: its tab is closed over HTTP, and if
    that doesn't release it either, its results are kept and the thread is
    left behind (daemon threads don't block the process from exiting). A
    worker that raises loses nothing it had already appended."""
    import threading
    import time as _time
    outs = [[] for _ in chunks]
    tabs = [{} for _ in chunks]
    errors = [None] * len(chunks)

    def run(i):
        try:
            work(chunks[i], outs[i], tabs[i])
        except Exception as e:
            errors[i] = e

    threads = [threading.Thread(target=run, args=(i,), daemon=True, name=f"{label}-tab-{i}") for i in range(len(chunks))]
    for t in threads:
        t.start()
    forced = set()
    while any(t.is_alive() for t in threads):
        _time.sleep(2)
        now = _time.time()
        for i, t in enumerate(threads):
            searched = tabs[i].get("searched")
            if not t.is_alive() or not searched or now - searched < close_grace:
                continue
            if i not in forced:
                forced.add(i)
                logger.warning(f"{label}: tab {i} finished searching but didn't close in {close_grace:.0f}s; closing it over CDP")
                if tabs[i].get("id"):
                    close_target(tabs[i]["id"], port)
            elif now - searched >= 2 * close_grace:
                logger.error(f"{label}: tab {i} still stuck after a forced close; keeping its {len(outs[i])} results and moving on")
                threads[i] = None
        threads = [t for t in threads if t is not None]
    for i, e in enumerate(errors):
        if e:
            logger.error(f"{label}: tab {i} failed ({e}); keeping its {len(outs[i])} results")
    return [r for out in outs for r in out]
