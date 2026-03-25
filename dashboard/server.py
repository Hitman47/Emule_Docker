#!/usr/bin/env python3
"""
aMule ZimaBoard Dashboard — Lightweight API server
Wraps amulecmd to provide a modern REST API + serves the dashboard UI.
Pure stdlib Python — no pip install needed.
"""

import http.server
import json
import os
import subprocess
import shutil
import time
import re
import hashlib
import threading
import html
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from pathlib import Path

# ── Config ──
EC_HOST = os.environ.get("AMULE_EC_HOST", "localhost")
EC_PORT = os.environ.get("AMULE_EC_PORT", "4712")
EC_PASSWORD = os.environ.get("AMULE_EC_PASSWORD", "")
EC_PASSWORD_HASH = os.environ.get("AMULE_EC_PASSWORD_HASH", "")
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "4713"))
DASHBOARD_PWD = os.environ.get("DASHBOARD_PWD", "admin")
INCOMING_DIR = os.environ.get("INCOMING_DIR", "/incoming")
TEMP_DIR = os.environ.get("TEMP_DIR", "/temp")

# Try to load credentials from file (more reliable than env vars)
AMULE_HOME = os.environ.get("AMULE_HOME", "/home/amule/.aMule")
_cred_file = os.path.join(AMULE_HOME, ".ec_credentials")
try:
    with open(_cred_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if '=' in _line:
                _k, _v = _line.split('=', 1)
                if _k == 'EC_PASSWORD' and _v:
                    EC_PASSWORD = _v
                elif _k == 'EC_PASSWORD_HASH' and _v:
                    EC_PASSWORD_HASH = _v
                elif _k == 'EC_HOST' and _v:
                    EC_HOST = _v
                elif _k == 'EC_PORT' and _v:
                    EC_PORT = _v
    print(f"[DASHBOARD] Credentials loaded from {_cred_file}")
except FileNotFoundError:
    print(f"[DASHBOARD] No credential file at {_cred_file}, using env vars")

# If we still have no hash, compute it
if not EC_PASSWORD_HASH and EC_PASSWORD:
    import hashlib as _hl
    EC_PASSWORD_HASH = _hl.md5(EC_PASSWORD.encode()).hexdigest()

# Track which password mode works (auto-detected on first successful call)
_password_mode = None  # None = not yet tested, "plain" or "hash"

STATIC_DIR = Path(__file__).parent / "static"
AMULE_HOME = os.environ.get("AMULE_HOME", "/home/amule/.aMule")
SETTINGS_FILE = os.environ.get("SETTINGS_FILE", os.path.join(AMULE_HOME, "dashboard-settings.json"))

# Default server sources (also initialized in entrypoint.sh)
DEFAULT_SERVER_SOURCES = {
    "official": {
        "key": "official",
        "label": "eMule Security (officiel)",
        "kind": "serverlist",
        "url": "http://upd.emule-security.org/server.met",
        "priority": 300,
        "description": "Source officielle prioritaire.",
    },
    "peerates": {
        "key": "peerates",
        "label": "Peerates",
        "kind": "serverlist",
        "url": "http://edk.peerates.net/servers/best/server.met",
        "priority": 200,
        "description": "Bonne source secondaire.",
    },
    "flyernet": {
        "key": "flyernet",
        "label": "FlyerNet",
        "kind": "html",
        "url": "http://flyernet.fr.st.free.fr/ip_serveurs.php",
        "priority": 100,
        "description": "Page HTML d'IP/ports à parser.",
    },
}



DEFAULT_DASHBOARD_CONFIG = {
    "read_only": False,
    "debug_mode": True,
    "refresh_interval_sec": 5,
    "action_history_limit": 80,
    "write_rate_limit_per_minute": 30,
    "login_rate_limit_per_minute": 20,
}


def _default_settings():
    return {
        "server_sources": [dict(v) for v in DEFAULT_SERVER_SOURCES.values()],
        "last_scan": None,
        "dashboard": dict(DEFAULT_DASHBOARD_CONFIG),
    }


def normalize_dashboard_config(raw=None):
    raw = raw or {}
    cfg = dict(DEFAULT_DASHBOARD_CONFIG)
    if isinstance(raw, dict):
        for key in list(cfg.keys()):
            if key in raw:
                cfg[key] = raw.get(key)
    cfg["read_only"] = bool(cfg.get("read_only", False))
    cfg["debug_mode"] = bool(cfg.get("debug_mode", True))
    limits = (
        ("refresh_interval_sec", 2, 60, 5),
        ("action_history_limit", 10, 300, 80),
        ("write_rate_limit_per_minute", 5, 300, 30),
        ("login_rate_limit_per_minute", 3, 120, 20),
    )
    for key, minimum, maximum, default in limits:
        try:
            value = int(cfg.get(key, default))
        except Exception:
            value = default
        cfg[key] = max(minimum, min(maximum, value))
    return cfg


def normalize_settings(raw=None):
    raw = raw if isinstance(raw, dict) else {}
    settings = _default_settings()
    if isinstance(raw.get("last_scan"), str):
        settings["last_scan"] = raw.get("last_scan")
    sources = raw.get("server_sources")
    if isinstance(sources, list) and sources:
        settings["server_sources"] = sources
    settings["dashboard"] = normalize_dashboard_config(raw.get("dashboard"))
    return settings


def get_dashboard_config():
    return normalize_settings(load_settings()).get("dashboard", dict(DEFAULT_DASHBOARD_CONFIG))


def load_settings():
    """Load persistent settings from JSON file."""
    try:
        with open(SETTINGS_FILE, "r") as f:
            return normalize_settings(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return normalize_settings(None)


def save_settings(settings):
    """Save settings to JSON file."""
    try:
        normalized = normalize_settings(settings)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)
        sync_action_history_limit(normalized.get("dashboard", {}))
        return True
    except Exception:
        return False


def get_server_sources_from_settings():
    """Build SERVER_SOURCES dict from settings file, falling back to defaults."""
    settings = load_settings()
    if settings and "server_sources" in settings:
        sources = {}
        for src in settings["server_sources"]:
            key = src.get("key", src.get("url", ""))
            sources[key] = src
        return sources
    return dict(DEFAULT_SERVER_SOURCES)


# Active server sources (refreshed from settings)
SERVER_SOURCES = get_server_sources_from_settings()

# ── Simple session auth ──
AUTH_TOKEN = hashlib.sha256(DASHBOARD_PWD.encode()).hexdigest()[:32]

# ── Cache ──
_cache = {}
_cache_lock = threading.Lock()

def cache_get(key, max_age=5):
    with _cache_lock:
        if key in _cache:
            ts, data = _cache[key]
            if time.time() - ts < max_age:
                return data
    return None

def cache_set(key, data):
    with _cache_lock:
        _cache[key] = (time.time(), data)


def cache_clear(*keys):
    with _cache_lock:
        if not keys:
            _cache.clear()
            return
        for key in keys:
            _cache.pop(key, None)


def stable_json_dumps(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def payload_digest(data):
    try:
        blob = stable_json_dumps(data)
    except Exception:
        blob = repr(data)
    return hashlib.sha1(blob.encode("utf-8", errors="ignore")).hexdigest()[:16]


def unchanged_payload(digest, **extra):
    payload = {"ok": True, "unchanged": True, "digest": digest, "generated_at": int(time.time())}
    payload.update(extra)
    return payload


# ── Action state ──
_action_locks = {name: threading.Lock() for name in ("download", "add_ed2k", "pause", "resume", "cancel")}
_last_search_context = {"query": "", "type": "kad", "results": [], "timestamp": 0}


# Recent action history for UI / diagnostics
import collections
_action_history = collections.deque(maxlen=DEFAULT_DASHBOARD_CONFIG["action_history_limit"])
_action_history_lock = threading.Lock()

# Lightweight rate limiting
_rate_limit_lock = threading.Lock()
_rate_limit_buckets = {}


def get_client_ip(handler):
    forwarded = handler.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    try:
        return handler.client_address[0]
    except Exception:
        return "unknown"


def rate_limit_retry_after(bucket, key, limit, window=60):
    now = time.time()
    scope = f"{bucket}:{key}"
    with _rate_limit_lock:
        dq = _rate_limit_buckets.get(scope)
        if dq is None:
            dq = collections.deque()
            _rate_limit_buckets[scope] = dq
        while dq and now - dq[0] >= window:
            dq.popleft()
        if len(dq) >= max(1, int(limit)):
            return max(1, int(window - (now - dq[0])))
        dq.append(now)
    return 0


def sync_action_history_limit(config=None):
    cfg = normalize_dashboard_config(config or get_dashboard_config())
    limit = int(cfg.get("action_history_limit", DEFAULT_DASHBOARD_CONFIG["action_history_limit"]))
    global _action_history
    with _action_history_lock:
        items = list(_action_history)[:limit]
        _action_history = collections.deque(items, maxlen=limit)


def clear_action_history_store():
    global _action_history
    with _action_history_lock:
        _action_history = collections.deque([], maxlen=_action_history.maxlen)
    history = _load_history()
    history["action_history"] = []
    _save_history(history)


def set_last_search_context(query, search_type, results):
    global _last_search_context
    _last_search_context = {
        "query": query,
        "type": search_type,
        "results": results or [],
        "timestamp": time.time(),
    }


def action_response(action, ok, code, message, confirmed=False, status=None, data=None):
    payload = {
        "ok": bool(ok),
        "action": action,
        "confirmed": bool(confirmed),
        "code": code,
        "message": message,
        "data": data or {},
    }
    return payload, (status or (200 if ok else 409))


def compact_transfer_result(item):
    if not isinstance(item, dict):
        return {}
    payload = {
        "hash": item.get("hash", ""),
        "name": item.get("name", ""),
        "code": item.get("code", "UNKNOWN"),
        "message": item.get("message", ""),
        "before_status": item.get("before_status", ""),
        "after_status": item.get("after_status", ""),
        "confirmed": bool(item.get("confirmed")),
        "ok": bool(item.get("ok")),
    }
    return payload



def summarize_transfer_action_results(results):
    overview = {
        "counts_by_code": {},
        "confirmed_hashes": [],
        "failed_hashes": [],
        "missing_hashes": [],
        "status_before": {},
        "status_after": {},
        "failed_items": [],
        "already_items": [],
        "success_items": [],
    }
    for raw in results or []:
        item = compact_transfer_result(raw)
        code = str(item.get("code") or "UNKNOWN")
        overview["counts_by_code"][code] = overview["counts_by_code"].get(code, 0) + 1
        before_status = str(item.get("before_status") or "").strip()
        after_status = str(item.get("after_status") or "").strip()
        if before_status:
            overview["status_before"][before_status] = overview["status_before"].get(before_status, 0) + 1
        if after_status:
            overview["status_after"][after_status] = overview["status_after"].get(after_status, 0) + 1
        hash_value = item.get("hash") or ""
        if item.get("confirmed") and item.get("ok"):
            if hash_value:
                overview["confirmed_hashes"].append(hash_value)
        elif code == "TRANSFER_NOT_FOUND":
            if hash_value:
                overview["missing_hashes"].append(hash_value)
        else:
            if hash_value:
                overview["failed_hashes"].append(hash_value)

        if code in {"STATE_NOT_CONFIRMED", "TRANSFER_NOT_FOUND", "COMMAND_FAILED", "TIMEOUT", "SESSION_ERROR", "CORE_UNREACHABLE"}:
            overview["failed_items"].append(item)
        elif code == "ALREADY_EXISTS":
            overview["already_items"].append(item)
        elif item.get("ok"):
            overview["success_items"].append(item)

    for key in ("failed_items", "already_items", "success_items"):
        overview[key] = overview[key][:12]
    return overview



def record_action_event(payload, http_status):
    event = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": int(time.time()),
        "action": payload.get("action", "unknown"),
        "ok": bool(payload.get("ok")),
        "confirmed": bool(payload.get("confirmed")),
        "code": payload.get("code", "UNKNOWN"),
        "message": payload.get("message", ""),
        "http_status": int(http_status or 0),
    }
    data = payload.get("data") or {}
    if isinstance(data, dict):
        if data.get("download", {}).get("name"):
            event["target"] = data["download"]["name"]
        elif data.get("existing", {}).get("name"):
            event["target"] = data["existing"]["name"]
        elif data.get("link"):
            event["target"] = str(data["link"])[:140]
        elif data.get("hash"):
            event["target"] = data["hash"]
        elif data.get("query"):
            event["target"] = data["query"]
    with _action_history_lock:
        _action_history.appendleft(event)
        snapshot = list(_action_history)
    history = _load_history()
    history["action_history"] = snapshot
    _save_history(history)


def get_action_history(limit=30):
    with _action_history_lock:
        return list(_action_history)[:max(1, int(limit))]


def fetch_text_url(url, timeout=20):
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; aMuleDashboard/1.0; +https://localhost)",
        "Accept": "text/html, text/plain, */*",
    })
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset()
    for encoding in [charset, "utf-8", "latin-1", "cp1252"]:
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def build_serverlist_link(url):
    return f"ed2k://|serverlist|{url}|/"


def extract_servers_from_text(raw_text, limit=128):
    text = html.unescape(raw_text or "")
    pattern = re.compile(r'((?:\d{1,3}\.){3}\d{1,3})\s*[: ]\s*(\d{2,5})')
    servers = []
    seen = set()
    for line in text.splitlines():
        stripped = re.sub(r'<[^>]+>', ' ', line)
        stripped = re.sub(r'\s+', ' ', stripped).strip()
        if not stripped:
            continue
        for ip, port in pattern.findall(stripped):
            key = f"{ip}:{port}"
            if key in seen:
                continue
            seen.add(key)
            name = stripped.replace(key, '').strip(' -|:;,.')
            if len(name) > 80:
                name = name[:80].rstrip()
            servers.append({"ip": ip, "port": int(port), "name": name or key})
            if len(servers) >= limit:
                return servers
    return servers


def parse_servers(raw):
    servers = []
    seen = set()
    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        match = re.search(r'((?:\d{1,3}\.){3}\d{1,3}):(\d{2,5})', stripped)
        if not match:
            continue
        ip, port = match.group(1), int(match.group(2))
        key = f"{ip}:{port}"
        if key in seen:
            continue
        seen.add(key)
        name = stripped.replace(key, '').strip(' -|')
        name = re.sub(r'^\d+[).:-]?\s*', '', name)
        servers.append({
            "ip": ip,
            "port": port,
            "address": key,
            "name": name or key,
            "raw": stripped,
        })
    return servers


def get_server_sources_payload():
    return [SERVER_SOURCES[key] for key in sorted(SERVER_SOURCES, key=lambda k: SERVER_SOURCES[k]["priority"], reverse=True)]


def normalize_source_order(sources):
    items = []
    for source in sources or []:
        if isinstance(source, str):
            source = source.strip()
        if source:
            items.append(source)
    if not items:
        items = ["official"]

    def score(value):
        meta = SERVER_SOURCES.get(value)
        if meta:
            return meta["priority"]
        if value.startswith("http://") or value.startswith("https://"):
            return 10
        return 0

    return sorted(dict.fromkeys(items), key=score, reverse=True)


def import_server_source(source):
    meta = SERVER_SOURCES.get(source)
    if meta:
        label = meta["label"]
        url = meta["url"]
        if meta["kind"] == "serverlist":
            output = run_amulecmd(f"add {build_serverlist_link(url)}", timeout=30)
            return {
                "source": source,
                "label": label,
                "url": url,
                "kind": meta["kind"],
                "ok": not output.startswith("ERROR"),
                "added": None,
                "output": output,
            }
        html_text = fetch_text_url(url, timeout=20)
        servers = extract_servers_from_text(html_text)
        added = 0
        outputs = []
        for server in servers:
            link = f"ed2k://|server|{server['ip']}|{server['port']}|/"
            out = run_amulecmd(f"add {link}", timeout=10)
            outputs.append(out)
            if not out.startswith("ERROR"):
                added += 1
        ok = bool(servers) and added > 0
        return {
            "source": source,
            "label": label,
            "url": url,
            "kind": meta["kind"],
            "ok": ok,
            "added": added,
            "detected": len(servers),
            "output": "\n".join(filter(None, outputs[:10])) or f"{len(servers)} serveur(s) détecté(s)",
        }

    if source.startswith("http://") or source.startswith("https://"):
        output = run_amulecmd(f"add {build_serverlist_link(source)}", timeout=30)
        return {
            "source": source,
            "label": source,
            "url": source,
            "kind": "custom_serverlist",
            "ok": not output.startswith("ERROR"),
            "added": None,
            "output": output,
        }

    raise ValueError("Source inconnue")


def import_server_sources(sources, reconnect=True):
    results = []
    total_added = 0
    for source in normalize_source_order(sources):
        try:
            result = import_server_source(source)
        except Exception as exc:
            result = {
                "source": source,
                "label": source,
                "ok": False,
                "output": str(exc),
            }
        if isinstance(result.get("added"), int):
            total_added += result["added"]
        results.append(result)

    connect_output = ""
    if reconnect:
        connect_output = run_amulecmd("connect ed2k", timeout=20)

    cache_clear("status", "servers")
    return {
        "ok": any(item.get("ok") for item in results),
        "results": results,
        "total_added": total_added,
        "connect_output": connect_output,
    }


def _exec_amulecmd(command, password, timeout=15):
    """Low-level amulecmd execution with a specific password."""
    try:
        cmd = ["amulecmd", "-h", EC_HOST, "-p", EC_PORT, "-P", password, "-c", command]
        _log(f"EXEC: amulecmd -h {EC_HOST} -p {EC_PORT} -P {'***'+password[-4:] if len(password)>4 else '***'} -c {command}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = result.stdout + result.stderr
        _log(f"  RC={result.returncode} | output={output[:200].replace(chr(10),' | ')}")
        return output
    except subprocess.TimeoutExpired:
        _log(f"  TIMEOUT after {timeout}s")
        return "ERROR: timeout"
    except Exception as e:
        _log(f"  EXCEPTION: {e}")
        return f"ERROR: {e}"


# ── Logging ring buffer (last 50 entries, visible in /api/debug) ──
_log_buffer = collections.deque(maxlen=50)

def _log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _log_buffer.append(line)
    print(f"[DASHBOARD] {line}", flush=True)


def _clean_amulecmd_output(output):
    """Remove amulecmd header lines, keep status lines (starting with >)."""
    lines = output.split("\n")
    clean = []
    skip_header = True
    for line in lines:
        # Status lines always start with ">" — never skip them
        if line.strip().startswith(">"):
            skip_header = False
            clean.append(line)
            continue
        # Skip header boilerplate
        if skip_header:
            stripped = line.strip()
            if (not stripped
                    or "This is amulecmd" in line
                    or "Creating client" in line
                    or "Succeeded!" in line
                    or "Connection established" in line
                    or stripped == "---"):
                continue
            # Any other non-empty line ends the header
            skip_header = False
        clean.append(line)
    return "\n".join(clean).strip()


def run_amulecmd(command, timeout=15):
    """Execute amulecmd with auto-detection of password mode.

    Tries: plain password → computed hash → hash from amule.conf.
    Caches which mode works.
    """
    global _password_mode

    # Build password candidates
    if _password_mode == "plain":
        passwords = [(EC_PASSWORD, "plain")]
    elif _password_mode == "hash":
        passwords = [(EC_PASSWORD_HASH, "hash")]
    elif _password_mode == "conf_hash":
        passwords = [(_conf_ec_hash, "conf_hash")]
    else:
        passwords = []
        if EC_PASSWORD:
            passwords.append((EC_PASSWORD, "plain"))
        if EC_PASSWORD_HASH and EC_PASSWORD_HASH != EC_PASSWORD:
            passwords.append((EC_PASSWORD_HASH, "hash"))
        # Also try hash read from amule.conf
        if _conf_ec_hash and _conf_ec_hash not in (EC_PASSWORD, EC_PASSWORD_HASH):
            passwords.append((_conf_ec_hash, "conf_hash"))

    for i, (pwd, mode) in enumerate(passwords):
        if not pwd:
            continue
        output = _exec_amulecmd(command, pwd, timeout)

        if "Authentication failed" in output or "wrong password" in output.lower():
            if i < len(passwords) - 1:
                _log(f"  Auth failed with mode={mode}, trying next...")
                continue
            return _clean_amulecmd_output(output)

        # Success — remember mode
        if _password_mode is None and "Unable to connect" not in output:
            _password_mode = mode
            _log(f"PASSWORD MODE LOCKED: {mode}")

        return _clean_amulecmd_output(output)

    return "ERROR: no password configured"


# ── Read ECPassword hash from amule.conf at startup ──
_conf_ec_hash = ""
_amule_conf_path = os.path.join(AMULE_HOME, "amule.conf")
try:
    with open(_amule_conf_path) as _f:
        for _line in _f:
            if _line.strip().startswith("ECPassword="):
                _conf_ec_hash = _line.strip().split("=", 1)[1]
                print(f"[DASHBOARD] Read ECPassword from amule.conf: {_conf_ec_hash[:10]}...")
                break
except FileNotFoundError:
    print(f"[DASHBOARD] amule.conf not found at {_amule_conf_path}")
except Exception as _e:
    print(f"[DASHBOARD] Error reading amule.conf: {_e}")


def parse_status(raw):
    info = {
        "connected_ed2k": False, "connected_kad": False,
        "ed2k_status": "disconnected",   # disconnected | low_id | high_id
        "kad_status": "disconnected",     # disconnected | firewalled | connected
        "ed2k_server": "",
        "ed2k_id_type": "",
        "download_speed": 0, "upload_speed": 0,
        "queue_length": 0, "shared_files": 0,
        "clients_in_queue": 0, "total_sources": 0,
        "raw": raw
    }
    for line in raw.split("\n"):
        ll = line.lower().strip()
        orig = line.strip()

        # ── ED2K detection ──
        if "ed2k" in ll or "edonkey" in ll:
            if "not connected" in ll or "disconnected" in ll:
                info["ed2k_status"] = "disconnected"
                info["connected_ed2k"] = False
            elif "now connecting" in ll:
                info["ed2k_status"] = "connecting"
                info["connected_ed2k"] = False
            elif "connected" in ll:
                info["connected_ed2k"] = True
                if "high" in ll or "highid" in ll or "high id" in ll:
                    info["ed2k_status"] = "high_id"
                    info["ed2k_id_type"] = "High ID"
                elif "low" in ll or "lowid" in ll or "low id" in ll:
                    info["ed2k_status"] = "low_id"
                    info["ed2k_id_type"] = "Low ID"
                else:
                    info["ed2k_status"] = "connected"
                # Extract server name: "Connected to ServerName [ip:port]" or "(ip:port)"
                m = re.search(r'connected\s+to\s+(.+?)(?:\s*[\[\(]|$)', orig, re.I)
                if m:
                    info["ed2k_server"] = m.group(1).strip()

        # ── Kad detection ──
        if "kad" in ll:
            if "not connected" in ll or "not running" in ll or "disconnected" in ll:
                info["kad_status"] = "disconnected"
                info["connected_kad"] = False
            elif "firewalled" in ll:
                info["kad_status"] = "firewalled"
                info["connected_kad"] = True
            elif "connected" in ll or "running" in ll:
                info["kad_status"] = "connected"
                info["connected_kad"] = True

        # ── Speeds (multiple formats) ──
        m = re.search(r'dl:\s*([\d.]+)\s*kb/s.*ul:\s*([\d.]+)\s*kb/s', ll)
        if m:
            info["download_speed"] = float(m.group(1))
            info["upload_speed"] = float(m.group(2))

        # "Download: X bytes/sec" format
        if "download:" in ll:
            m2 = re.search(r'download:\s*([\d.]+)\s*(bytes|kb|mb)', ll)
            if m2:
                val = float(m2.group(1))
                unit = m2.group(2)
                if unit == "bytes": val /= 1024
                elif unit == "mb": val *= 1024
                info["download_speed"] = val
        if "upload:" in ll:
            m2 = re.search(r'upload:\s*([\d.]+)\s*(bytes|kb|mb)', ll)
            if m2:
                val = float(m2.group(1))
                unit = m2.group(2)
                if unit == "bytes": val /= 1024
                elif unit == "mb": val *= 1024
                info["upload_speed"] = val

        # Clients / Sources
        if "clients in queue" in ll:
            m3 = re.search(r'(\d+)', ll)
            if m3: info["clients_in_queue"] = int(m3.group(1))
        if "total sources" in ll:
            m3 = re.search(r'(\d+)', ll)
            if m3: info["total_sources"] = int(m3.group(1))

    # If ED2K connected but no ID type detected, try to figure out from server line
    if info["connected_ed2k"] and info["ed2k_status"] == "connected":
        # Default to low_id if behind VPN (most likely)
        info["ed2k_status"] = "low_id"
        info["ed2k_id_type"] = "Low ID"

    return info


def parse_downloads(raw):
    """Parse amulecmd 'show dl' output.
    
    Debian amulecmd 2.3.3 format:
      > HASH Filename
        subsequent lines with size, progress, sources, speed, status
    
    A new download entry is ONLY started by a line matching "> [32-hex-hash] name".
    All other lines are attributes of the current download.
    """
    downloads = []
    current = None

    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # ── New download: "> HASH Filename" ──
        # Match: optional ">", then 32 hex chars, then filename
        m_entry = re.match(r'^>?\s*([0-9A-Fa-f]{32})\s+(.+)', stripped)
        if m_entry:
            if current:
                downloads.append(current)
            current = {
                "name": m_entry.group(2).strip(),
                "hash": m_entry.group(1),
                "size": "",
                "size_bytes": None,
                "size_mb": None,
                "progress": 0,
                "speed": 0,
                "sources": 0,
                "status": "queued"
            }
            continue

        # ── Attribute lines (everything else goes to current download) ──
        if not current:
            continue

        # Remove leading "> " or ">" from attribute lines
        attr = re.sub(r'^>\s*', '', stripped)
        al = attr.lower()

        # Progress: "28.2%" or "28.2 %"
        m_pct = re.search(r'([\d.]+)\s*%', attr)
        if m_pct:
            current["progress"] = float(m_pct.group(1))

        # Size: "34.5/122.3 MB" or "34.5 MB / 122.3 MB" or "Size: 122.3 MB"
        m_frac = re.search(r'([\d.]+)\s*/\s*([\d.]+)\s*([KMGT]?i?[Bb])', attr)
        if m_frac:
            current["size"] = f"{m_frac.group(2)} {m_frac.group(3)}"
            # Also compute progress if not already set
            if current["progress"] == 0:
                try:
                    done = float(m_frac.group(1))
                    total = float(m_frac.group(2))
                    if total > 0:
                        current["progress"] = round(done / total * 100, 1)
                except ValueError:
                    pass
        elif not current["size"]:
            m_size = re.search(r'([\d.]+)\s*([KMGT]i?[Bb])', attr)
            if m_size:
                current["size"] = f"{m_size.group(1)} {m_size.group(2)}"

        # Sources: "3 source(s)" or "Sources: 3" or "3 src"
        m_src = re.search(r'(\d+)\s*(?:source|src)', al)
        if m_src:
            current["sources"] = int(m_src.group(1))
        if not m_src:
            m_src2 = re.search(r'sources?:\s*(\d+)', al)
            if m_src2:
                current["sources"] = int(m_src2.group(1))

        # Speed
        m_spd = re.search(r'([\d.]+)\s*[kK][bB]/s', attr)
        if m_spd:
            current["speed"] = float(m_spd.group(1))
        else:
            m_spd2 = re.search(r'([\d.]+)\s*[Bb]ytes/s', attr)
            if m_spd2:
                current["speed"] = round(float(m_spd2.group(1)) / 1024, 2)

        # Status keywords
        for st in ["downloading", "paused", "waiting", "completing",
                    "complete", "hashing", "error", "stopped",
                    "getting sources", "allocating", "connecting", "queued"]:
            if st in al:
                current["status"] = st
                break

    if current:
        downloads.append(current)

    for item in downloads:
        try:
            size_bytes = size_to_bytes(item.get("size", ""))
        except Exception:
            size_bytes = None
        item["size_bytes"] = size_bytes
        item["size_mb"] = round(size_bytes / (1024 ** 2), 2) if size_bytes else None

    _log(f"parse_downloads: {len(downloads)} downloads parsed")
    for i, dl in enumerate(downloads[:5]):
        _log(f"  [{i}] {dl['name'][:50]}... | {dl['progress']:.1f}% | {dl['status']} | src={dl['sources']}")

    return downloads


def estimate_eta_text(download):
    try:
        speed = float(download.get("speed") or 0)
        progress = float(download.get("progress") or 0)
    except Exception:
        return "—"
    if speed <= 0 or progress <= 0 or progress >= 100:
        return "—"
    size = str(download.get("size") or "")
    m = re.match(r'([\d.]+)\s*([KMGT]?i?[Bb])', size, re.I)
    if not m:
        return "—"
    total = float(m.group(1))
    unit = m.group(2).upper().replace("IB", "B")
    mult = {"B": 1/1024, "KB": 1, "MB": 1024, "GB": 1024*1024, "TB": 1024*1024*1024}
    total_kb = total * mult.get(unit, 1)
    remaining_kb = total_kb * (1 - progress / 100)
    if remaining_kb <= 0:
        return "—"
    secs = remaining_kb / speed
    if secs < 60:
        return f"{round(secs)}s"
    if secs < 3600:
        return f"{round(secs/60)} min"
    if secs < 86400:
        hours = int(secs // 3600)
        mins = int((secs % 3600) // 60)
        return f"{hours}h {mins:02d}m"
    days = int(secs // 86400)
    hours = int((secs % 86400) // 3600)
    return f"{days}j {hours}h"


def get_download_detail(hash_value, raw=None, downloads=None):
    hash_value = str(hash_value or "").strip()
    if not hash_value:
        return None
    raw = raw if raw is not None else run_amulecmd("show dl")
    downloads = downloads if downloads is not None else parse_downloads(raw)
    target = None
    for item in downloads:
        if item.get("hash") == hash_value:
            target = dict(item)
            break
    if not target:
        return None

    blocks = []
    current = []
    current_hash = None
    for line in raw.splitlines():
        stripped = line.rstrip()
        m_entry = re.match(r'^>?\s*([0-9A-Fa-f]{32})\s+(.+)', stripped.strip())
        if m_entry:
            if current_hash and current:
                blocks.append((current_hash, current[:]))
            current_hash = m_entry.group(1)
            current = [stripped]
            continue
        if current_hash:
            if stripped.strip():
                current.append(stripped)
    if current_hash and current:
        blocks.append((current_hash, current[:]))

    block_lines = []
    for block_hash, lines in blocks:
        if block_hash == hash_value:
            block_lines = lines
            break

    target["eta"] = estimate_eta_text(target)
    target["raw_block"] = "\n".join(block_lines)
    target["raw_lines"] = block_lines
    target["is_active"] = target.get("status") in {"downloading", "getting sources", "waiting", "connecting"}
    target["can_resume"] = target.get("status") in {"paused", "stopped"}
    target["can_pause"] = not target["can_resume"]
    return target


def summarize_downloads(downloads):
    summary = {
        "total": len(downloads or []),
        "active": 0,
        "paused": 0,
        "waiting": 0,
        "errors": 0,
        "completed": 0,
        "total_speed": 0.0,
        "total_sources": 0,
        "avg_progress": 0.0,
        "counts_by_status": {},
    }
    if not downloads:
        return summary

    waiting_states = {"waiting", "getting sources", "connecting", "queued", "allocating"}
    paused_states = {"paused", "stopped"}
    active_states = {"downloading", "hashing", "completing"}

    progress_values = []
    for dl in downloads:
        status = str(dl.get("status") or "queued")
        summary["counts_by_status"][status] = summary["counts_by_status"].get(status, 0) + 1
        if status in paused_states:
            summary["paused"] += 1
        elif status in waiting_states:
            summary["waiting"] += 1
        elif status == "error":
            summary["errors"] += 1
        elif status == "complete":
            summary["completed"] += 1
        elif status in active_states:
            summary["active"] += 1
        else:
            summary["waiting"] += 1
        try:
            summary["total_speed"] += float(dl.get("speed") or 0)
        except Exception:
            pass
        try:
            summary["total_sources"] += int(dl.get("sources") or 0)
        except Exception:
            pass
        try:
            progress_values.append(float(dl.get("progress") or 0))
        except Exception:
            pass

    if progress_values:
        summary["avg_progress"] = round(sum(progress_values) / len(progress_values), 1)
    summary["total_speed"] = round(summary["total_speed"], 1)
    return summary


def build_status_payload(raw=None):
    data = parse_status(raw if raw is not None else run_amulecmd("status"))
    data["disk"] = get_disk_info()
    digest_data = {
        "ed2k_status": data.get("ed2k_status"),
        "ed2k_server": data.get("ed2k_server"),
        "kad_status": data.get("kad_status"),
        "download_speed": data.get("download_speed"),
        "upload_speed": data.get("upload_speed"),
        "queue_length": data.get("queue_length"),
        "shared_files": data.get("shared_files"),
        "clients_in_queue": data.get("clients_in_queue"),
        "total_sources": data.get("total_sources"),
        "disk": data.get("disk"),
    }
    data["digest"] = payload_digest(digest_data)
    data["generated_at"] = int(time.time())
    return data


def build_downloads_payload(raw=None, include_raw=True):
    raw = raw if raw is not None else run_amulecmd("show dl")
    data = parse_downloads(raw)
    for item in data:
        item["eta"] = estimate_eta_text(item)
    summary = summarize_downloads(data)
    digest_items = [{
        "hash": item.get("hash"),
        "name": item.get("name"),
        "progress": item.get("progress"),
        "speed": item.get("speed"),
        "sources": item.get("sources"),
        "status": item.get("status"),
        "size_bytes": item.get("size_bytes"),
        "eta": item.get("eta"),
    } for item in data]
    payload = {
        "downloads": data,
        "count": len(data),
        "summary": summary,
        "digest": payload_digest({"downloads": digest_items, "summary": summary}),
        "generated_at": int(time.time()),
    }
    if include_raw:
        payload["raw"] = raw
    return payload


def build_action_history_payload(limit=30):
    actions = get_action_history(limit)
    return {
        "actions": actions,
        "limit": _action_history.maxlen,
        "digest": payload_digest(actions),
        "generated_at": int(time.time()),
    }


def parse_search_results(raw):
    """Parse amulecmd 'results' output.
    
    Debian amulecmd 2.3.3 table format:
      Nr.    Filename:                        Size(MB):  Sources:
      -----------------------------------------------------------
      0.    Lana Rhoades Blacked Anal.mp4     259.101    16
      1.    Some Other File.mkv               1024.500   3
    
    Also handles: N) filename SIZE Sources: N
    """
    results = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("---") or line.startswith("Nr.") or line.startswith("Filename"):
            continue

        # Format 1: "N.  filename  SIZE  SOURCES" (Debian table format)
        m_table = re.match(r'^(\d+)\.\s+(.+?)\s{2,}([\d.]+)\s+(\d+)\s*$', line)
        if m_table:
            size_mb = float(m_table.group(3))
            if size_mb > 1024:
                size_str = f"{size_mb/1024:.1f} GB"
            else:
                size_str = f"{size_mb:.1f} MB"
            results.append({
                "id": int(m_table.group(1)),
                "name": m_table.group(2).strip(),
                "size": size_str,
                "size_mb": size_mb,
                "sources": int(m_table.group(4))
            })
            continue

        # Format 2: "N) filename SIZE Sources: N" (other versions)
        m_paren = re.match(r'^(\d+)\)\s+(.+?)\s+([\d.]+\s*[KMGT]?i?B)\s+Source[s]?:\s*(\d+)', line, re.I)
        if m_paren:
            results.append({
                "id": int(m_paren.group(1)),
                "name": m_paren.group(2).strip(),
                "size": m_paren.group(3),
                "sources": int(m_paren.group(4))
            })
            continue

        # Format 3: "N) filename" or "N. filename" (with possible trailing size/sources)
        m_min = re.match(r'^(\d+)[.)]\s+(.+)', line)
        if m_min:
            rest = m_min.group(2).strip()
            # Try: filename  SIZE  SOURCES (2+ spaces)
            m_tail = re.search(r'^(.+?)\s{2,}([\d.]+)\s+(\d+)\s*$', rest)
            if m_tail:
                size_mb = float(m_tail.group(2))
                size_str = f"{size_mb/1024:.1f} GB" if size_mb > 1024 else f"{size_mb:.1f} MB"
                results.append({
                    "id": int(m_min.group(1)),
                    "name": m_tail.group(1).strip(),
                    "size": size_str,
                    "size_mb": size_mb,
                    "sources": int(m_tail.group(3))
                })
            else:
                # Try: filename SIZE SOURCES (single space, match trailing numbers)
                m_tail2 = re.search(r'^(.+?)\s+([\d.]{3,})\s+(\d+)\s*$', rest)
                if m_tail2 and float(m_tail2.group(2)) > 1:
                    size_mb = float(m_tail2.group(2))
                    size_str = f"{size_mb/1024:.1f} GB" if size_mb > 1024 else f"{size_mb:.1f} MB"
                    results.append({
                        "id": int(m_min.group(1)),
                        "name": m_tail2.group(1).strip(),
                        "size": size_str,
                        "size_mb": size_mb,
                        "sources": int(m_tail2.group(3))
                    })
                else:
                    results.append({
                        "id": int(m_min.group(1)),
                        "name": rest,
                        "size": "",
                        "sources": 0
                    })

    # Sort by sources descending
    results.sort(key=lambda x: x.get("sources", 0), reverse=True)

    _log(f"parse_search_results: {len(results)} results parsed")
    return results


def get_disk_info():
    info = {}
    for name, path in [("incoming", INCOMING_DIR), ("temp", TEMP_DIR)]:
        try:
            u = shutil.disk_usage(path)
            info[name] = {"total_gb": round(u.total/(1024**3),2), "used_gb": round(u.used/(1024**3),2),
                          "free_gb": round(u.free/(1024**3),2),
                          "percent": round(u.used/u.total*100,1), "path": path}
        except Exception as e:
            info[name] = {"error": str(e), "path": path}
    return info


def format_size(n):
    for u in ['o','Ko','Mo','Go','To']:
        if n < 1024.0: return f"{n:.1f} {u}"
        n /= 1024.0
    return f"{n:.1f} Po"


def get_category(name):
    ext = name.rsplit('.',1)[-1].lower() if '.' in name else ''
    cats = {
        'video': ['mkv','avi','mp4','mov','wmv','flv','m4v','mpg','mpeg','webm','ts','vob','divx','rmvb'],
        'music': ['mp3','flac','ogg','wav','aac','wma','m4a','opus','ape','alac','aiff'],
        'image': ['jpg','jpeg','png','gif','bmp','tiff','tif','webp','svg','heic','raw'],
        'document': ['pdf','doc','docx','txt','epub','djvu','mobi','rtf','odt','nfo','srt','sub'],
        'software': ['iso','exe','msi','dmg','deb','rpm','apk','bin'],
        'archive': ['zip','rar','7z','tar','gz','bz2','xz','zst','cab']
    }
    for c, exts in cats.items():
        if ext in exts: return c
    return 'other'


def list_files(directory):
    files = []
    base = Path(directory)
    if not base.exists(): return files
    for p in sorted(base.rglob("*")):
        if p.is_file() and not p.name.startswith('.'):
            try:
                st = p.stat()
                files.append({"name": p.name, "path": str(p.relative_to(base)),
                              "size": st.st_size, "size_human": format_size(st.st_size),
                              "modified": int(st.st_mtime), "category": get_category(p.name)})
            except (OSError, ValueError): pass
    return files


# ══════════════════════════════════════════
# Search History & Favorites
# ══════════════════════════════════════════
HISTORY_FILE = os.path.join(AMULE_HOME, "dashboard-history.json")
MAX_SEARCH_HISTORY = 50
MAX_FAVORITES = 200
MAX_SAVED_SEARCHES = 40
APP_EXPORT_VERSION = 1


def _normalize_history_shape(data):
    if not isinstance(data, dict):
        data = {}
    data.setdefault("searches", [])
    data.setdefault("favorites", [])
    data.setdefault("saved_searches", [])
    data.setdefault("action_history", [])
    return data


def _load_history():
    try:
        with open(HISTORY_FILE, "r") as f:
            return _normalize_history_shape(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"searches": [], "favorites": [], "saved_searches": [], "action_history": []}


def _save_history(data):
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(_normalize_history_shape(data), f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def _merge_unique(items, key_fn, limit=None):
    merged = []
    seen = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            key = key_fn(item)
        except Exception:
            key = None
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if limit and len(merged) >= limit:
            break
    return merged


def build_export_bundle(include_action_history=False, include_stats=False):
    history = _load_history()
    bundle_history = {
        "searches": list(history.get("searches", []))[:MAX_SEARCH_HISTORY],
        "favorites": list(history.get("favorites", []))[:MAX_FAVORITES],
        "saved_searches": list(history.get("saved_searches", []))[:MAX_SAVED_SEARCHES],
    }
    if include_action_history:
        bundle_history["action_history"] = get_action_history(get_dashboard_config().get("action_history_limit", DEFAULT_DASHBOARD_CONFIG["action_history_limit"]))
    bundle = {
        "format": "amule_dashboard_bundle",
        "version": APP_EXPORT_VERSION,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "settings": load_settings(),
        "history": bundle_history,
        "meta": {
            "favorites": len(bundle_history.get("favorites", [])),
            "saved_searches": len(bundle_history.get("saved_searches", [])),
            "search_history": len(bundle_history.get("searches", [])),
            "action_history": len(bundle_history.get("action_history", [])),
        },
    }
    if include_stats:
        bundle["stats"] = _load_stats()
    return bundle


def import_dashboard_bundle(bundle, mode="merge"):
    if isinstance(bundle, str):
        bundle = json.loads(bundle)
    if not isinstance(bundle, dict):
        raise ValueError("Bundle invalide")
    if bundle.get("format") != "amule_dashboard_bundle":
        raise ValueError("Format de bundle non reconnu")
    mode = str(mode or "merge").strip().lower()
    if mode not in ("merge", "replace"):
        mode = "merge"

    incoming_settings = normalize_settings(bundle.get("settings"))
    incoming_history = _normalize_history_shape(bundle.get("history"))

    current_settings = load_settings()
    current_history = _load_history()

    if mode == "replace":
        final_settings = incoming_settings
        final_history = {
            "searches": list(incoming_history.get("searches", []))[:MAX_SEARCH_HISTORY],
            "favorites": list(incoming_history.get("favorites", []))[:MAX_FAVORITES],
            "saved_searches": list(incoming_history.get("saved_searches", []))[:MAX_SAVED_SEARCHES],
            "action_history": list(incoming_history.get("action_history", []))[: get_dashboard_config().get("action_history_limit", DEFAULT_DASHBOARD_CONFIG["action_history_limit"])],
        }
    else:
        final_settings = normalize_settings(current_settings)
        incoming_sources = incoming_settings.get("server_sources", [])
        current_sources = final_settings.get("server_sources", [])
        final_settings["server_sources"] = _merge_unique(
            list(incoming_sources) + list(current_sources),
            lambda item: (item.get("url") or item.get("key") or "").strip().lower(),
        )
        if incoming_settings.get("last_scan") and not final_settings.get("last_scan"):
            final_settings["last_scan"] = incoming_settings.get("last_scan")
        final_settings["dashboard"] = normalize_dashboard_config({**final_settings.get("dashboard", {}), **incoming_settings.get("dashboard", {})})

        final_history = {
            "searches": _merge_unique(list(incoming_history.get("searches", [])) + list(current_history.get("searches", [])),
                                      lambda item: f"{str(item.get('type') or '').lower()}::{str(item.get('query') or '').strip().lower()}",
                                      MAX_SEARCH_HISTORY),
            "favorites": _merge_unique(list(incoming_history.get("favorites", [])) + list(current_history.get("favorites", [])),
                                       lambda item: str(item.get("link") or "").strip().lower(),
                                       MAX_FAVORITES),
            "saved_searches": _merge_unique(list(incoming_history.get("saved_searches", [])) + list(current_history.get("saved_searches", [])),
                                            lambda item: str(item.get("key") or item.get("id") or "").strip().lower(),
                                            MAX_SAVED_SEARCHES),
            "action_history": _merge_unique(list(incoming_history.get("action_history", [])) + list(current_history.get("action_history", [])),
                                            lambda item: f"{item.get('ts') or 0}:{item.get('action') or ''}:{item.get('target') or ''}:{item.get('code') or ''}",
                                            get_dashboard_config().get("action_history_limit", DEFAULT_DASHBOARD_CONFIG["action_history_limit"])),
        }

    if not save_settings(final_settings):
        raise RuntimeError("Impossible de sauvegarder les paramètres importés")
    if not _save_history(final_history):
        raise RuntimeError("Impossible de sauvegarder l'historique importé")

    global SERVER_SOURCES
    SERVER_SOURCES = get_server_sources_from_settings()
    init_action_history_store()

    return {
        "mode": mode,
        "settings": {
            "server_sources": len(final_settings.get("server_sources", [])),
            "read_only": bool(final_settings.get("dashboard", {}).get("read_only")),
            "refresh_interval_sec": final_settings.get("dashboard", {}).get("refresh_interval_sec"),
        },
        "history": {
            "searches": len(final_history.get("searches", [])),
            "favorites": len(final_history.get("favorites", [])),
            "saved_searches": len(final_history.get("saved_searches", [])),
            "action_history": len(final_history.get("action_history", [])),
        },
    }


def init_action_history_store():
    global _action_history
    history = _load_history()
    limit = get_dashboard_config().get("action_history_limit", DEFAULT_DASHBOARD_CONFIG["action_history_limit"])
    events = [e for e in history.get("action_history", []) if isinstance(e, dict)][:limit]
    with _action_history_lock:
        _action_history = collections.deque(events, maxlen=limit)


init_action_history_store()


def add_search_history(query, search_type, result_count):
    h = _load_history()
    entry = {"query": query, "type": search_type, "results": result_count,
             "timestamp": int(time.time()), "date": time.strftime("%Y-%m-%d %H:%M")}
    # Remove duplicate queries
    h["searches"] = [s for s in h.get("searches", []) if s.get("query") != query]
    h["searches"].insert(0, entry)
    h["searches"] = h["searches"][:MAX_SEARCH_HISTORY]
    _save_history(h)

def add_favorite(name, ed2k_link, size="", sources=0):
    h = _load_history()
    if "favorites" not in h:
        h["favorites"] = []
    # No duplicate links
    if any(f.get("link") == ed2k_link for f in h["favorites"]):
        return False
    h["favorites"].insert(0, {"name": name, "link": ed2k_link, "size": size,
                                "sources": sources, "added": time.strftime("%Y-%m-%d %H:%M")})
    h["favorites"] = h["favorites"][:MAX_FAVORITES]
    _save_history(h)
    return True

def remove_favorite(link):
    h = _load_history()
    before = len(h.get("favorites", []))
    h["favorites"] = [f for f in h.get("favorites", []) if f.get("link") != link]
    _save_history(h)
    return before - len(h["favorites"])

def get_saved_searches():
    h = _load_history()
    saved = h.get("saved_searches", [])
    saved.sort(key=lambda item: (-(item.get("last_run_ts") or 0), -(item.get("created_ts") or 0)))
    return saved


def add_saved_search(query, search_type="kad", label=""):
    query = str(query or "").strip()
    search_type = str(search_type or "kad").strip().lower()
    label = str(label or "").strip()
    if not query:
        return False, "Query vide"
    if search_type not in ("kad", "global", "local"):
        search_type = "kad"
    h = _load_history()
    saved = h.get("saved_searches", [])
    normalized_key = f"{search_type}::{query.lower()}"
    for item in saved:
        if item.get("key") == normalized_key:
            if label and item.get("label") != label:
                item["label"] = label
                _save_history(h)
            return False, "Déjà enregistrée"
    now_ts = int(time.time())
    saved.insert(0, {
        "id": hashlib.sha1(f"{normalized_key}|{now_ts}".encode()).hexdigest()[:12],
        "key": normalized_key,
        "query": query,
        "type": search_type,
        "label": label or query,
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "created_ts": now_ts,
        "last_run": "",
        "last_run_ts": 0,
        "run_count": 0,
    })
    h["saved_searches"] = saved[:MAX_SAVED_SEARCHES]
    _save_history(h)
    return True, "Recherche enregistrée"


def remove_saved_search(search_id):
    h = _load_history()
    before = len(h.get("saved_searches", []))
    h["saved_searches"] = [s for s in h.get("saved_searches", []) if s.get("id") != search_id]
    _save_history(h)
    return before - len(h.get("saved_searches", []))


def touch_saved_search(query, search_type="kad"):
    query = str(query or "").strip()
    search_type = str(search_type or "kad").strip().lower()
    if not query:
        return
    key = f"{search_type}::{query.lower()}"
    h = _load_history()
    changed = False
    for item in h.get("saved_searches", []):
        if item.get("key") == key:
            item["last_run"] = time.strftime("%Y-%m-%d %H:%M")
            item["last_run_ts"] = int(time.time())
            item["run_count"] = int(item.get("run_count") or 0) + 1
            changed = True
            break
    if changed:
        _save_history(h)


# ══════════════════════════════════════════
# Stats History (daily DL/UL tracking)
# ══════════════════════════════════════════
STATS_FILE = os.path.join(AMULE_HOME, "dashboard-stats.json")

def _load_stats():
    try:
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"daily": {}, "snapshots": []}

def _save_stats(data):
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass

def record_stats_snapshot(dl_speed, ul_speed):
    """Called every status poll to accumulate daily stats."""
    stats = _load_stats()
    today = time.strftime("%Y-%m-%d")

    if today not in stats["daily"]:
        stats["daily"][today] = {"dl_bytes": 0, "ul_bytes": 0, "samples": 0,
                                  "peak_dl": 0, "peak_ul": 0}

    day = stats["daily"][today]
    # Accumulate bytes (speed is KB/s, poll interval ~5s)
    day["dl_bytes"] += int(dl_speed * 1024 * 5)
    day["ul_bytes"] += int(ul_speed * 1024 * 5)
    day["samples"] += 1
    if dl_speed > day["peak_dl"]:
        day["peak_dl"] = round(dl_speed, 1)
    if ul_speed > day["peak_ul"]:
        day["peak_ul"] = round(ul_speed, 1)

    # Keep only last 90 days
    cutoff = sorted(stats["daily"].keys())
    if len(cutoff) > 90:
        for old_day in cutoff[:-90]:
            del stats["daily"][old_day]

    _save_stats(stats)


# ══════════════════════════════════════════
# Bookmarklet
# ══════════════════════════════════════════
def get_bookmarklet_code(dashboard_url, token):
    """Generate a bookmarklet JS that sends ed2k links to the dashboard."""
    return (
        f"javascript:void((function(){{"
        f"var links=document.querySelectorAll('a[href^=\"ed2k://\"]');"
        f"if(!links.length){{var sel=window.getSelection().toString().trim();"
        f"if(sel.startsWith('ed2k://')){{links=[{{href:sel}}]}}}};"
        f"if(!links.length){{alert('Aucun lien ed2k trouvé sur cette page');return}};"
        f"var added=0;for(var i=0;i<links.length;i++){{"
        f"var h=links[i].href||links[i];fetch('{dashboard_url}/api/add_ed2k?link='"
        f"+encodeURIComponent(h)+'&token={token}').then(function(){{added++}})}};"
        f"setTimeout(function(){{alert(links.length+' lien(s) ed2k envoyé(s) au dashboard')}},1500)"
        f"}})())"
    )


def normalize_action_error(action, code, detail=""):
    detail = (detail or "").strip()
    messages = {
        "INVALID_INPUT": "Entrée invalide.",
        "CORE_UNREACHABLE": "Impossible de joindre le core aMule.",
        "SESSION_ERROR": "La session aMule a échoué.",
        "SEARCH_EXPIRED": "La recherche a expiré ou n'est plus disponible.",
        "RESULT_NOT_FOUND": "Résultat de recherche introuvable.",
        "TRANSFER_NOT_FOUND": "Transfert introuvable.",
        "ALREADY_EXISTS": "Ce fichier est déjà présent dans les transferts.",
        "COMMAND_FAILED": "La commande aMule a échoué.",
        "STATE_NOT_CONFIRMED": "Action non confirmée par aMule.",
        "TIMEOUT": "aMule a mis trop de temps à répondre.",
        "LOCKED": "Une action de même type est déjà en cours.",
    }
    msg = messages.get(code, "Erreur inconnue.")
    if detail and code not in ("ALREADY_EXISTS", "LOCKED"):
        msg = f"{msg} {detail}".strip()
    return msg


def classify_amule_error(output):
    text = (output or "").strip()
    low = text.lower()
    if not text:
        return None
    if "timeout" in low:
        return "TIMEOUT"
    if "unable to connect" in low or "can't connect" in low or "failed to connect" in low:
        return "CORE_UNREACHABLE"
    if "authentication failed" in low or "wrong password" in low:
        return "SESSION_ERROR"
    if low.startswith("error") or "invalid command" in low or "exception" in low:
        return "COMMAND_FAILED"
    return None


def parse_ed2k_link(link):
    match = re.match(r'^ed2k://\|file\|(.+?)\|(\d+)\|([0-9A-Fa-f]{32})\|', link or '')
    if not match:
        return None
    return {"name": match.group(1), "size": int(match.group(2)), "hash": match.group(3).upper(), "link": link}

def extract_ed2k_links(text):
    raw = (text or '').strip()
    if not raw:
        return []
    matches = re.findall(r'ed2k://\|.*?\|/', raw, flags=re.I | re.S)
    if not matches and raw.lower().startswith('ed2k://'):
        matches = [raw]
    cleaned = []
    seen = set()
    for match in matches:
        link = re.sub(r'\s+', '', match.strip())
        if not link or not link.lower().startswith('ed2k://'):
            continue
        key = link.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(link)
    return cleaned


def size_to_bytes(size_text):
    if not size_text:
        return None
    m = re.match(r'([\d.]+)\s*([KMGTP]?)(?:i?B|o)', str(size_text).strip(), re.I)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2).upper()
    factors = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}
    return int(val * factors.get(unit, 1))


def transfer_matches(download, *, name=None, hash_value=None, size_bytes=None):
    if hash_value and download.get("hash", "").upper() == hash_value.upper():
        return True
    if name and download.get("name") == name:
        if size_bytes is None:
            return True
        dl_size = size_to_bytes(download.get("size", ""))
        if dl_size is None:
            return True
        tolerance = max(int(size_bytes * 0.02), 2 * 1024 * 1024)
        return abs(dl_size - size_bytes) <= tolerance
    return False


def check_duplicate_downloads(*, name=None, hash_value=None, size_bytes=None, downloads=None):
    downloads = downloads if downloads is not None else parse_downloads(run_amulecmd("show dl"))
    for item in downloads:
        if transfer_matches(item, name=name, hash_value=hash_value, size_bytes=size_bytes):
            return item
    return None


def acquire_action_lock(action):
    lock = _action_locks[action]
    if not lock.acquire(blocking=False):
        return None
    return lock


def run_amulecmd_interactive(commands, timeout=25):
    global _password_mode
    if _password_mode == "plain":
        passwords = [(EC_PASSWORD, "plain")]
    elif _password_mode == "hash":
        passwords = [(EC_PASSWORD_HASH, "hash")]
    elif _password_mode == "conf_hash":
        passwords = [(_conf_ec_hash, "conf_hash")]
    else:
        passwords = []
        if EC_PASSWORD:
            passwords.append((EC_PASSWORD, "plain"))
        if EC_PASSWORD_HASH and EC_PASSWORD_HASH != EC_PASSWORD:
            passwords.append((EC_PASSWORD_HASH, "hash"))
        if _conf_ec_hash and _conf_ec_hash not in (EC_PASSWORD, EC_PASSWORD_HASH):
            passwords.append((_conf_ec_hash, "conf_hash"))

    script_lines = []
    for entry in commands:
        if isinstance(entry, tuple) and entry and entry[0] == "sleep":
            script_lines.append(("sleep", float(entry[1])))
        else:
            script_lines.append(("cmd", str(entry)))
    script_lines.append(("cmd", "quit"))

    for i, (pwd, mode) in enumerate(passwords):
        if not pwd:
            continue
        try:
            proc = subprocess.Popen(["amulecmd", "-h", EC_HOST, "-p", EC_PORT, "-P", pwd], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for kind, value in script_lines:
                if kind == "sleep":
                    time.sleep(value)
                else:
                    proc.stdin.write(value + "\n")
                    proc.stdin.flush()
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return "ERROR: timeout"
        except Exception as exc:
            return f"ERROR: {exc}"
        if "Authentication failed" in out or "wrong password" in out.lower():
            if i < len(passwords) - 1:
                continue
            return _clean_amulecmd_output(out)
        if _password_mode is None and "Unable to connect" not in out:
            _password_mode = mode
            _log(f"PASSWORD MODE LOCKED: {mode}")
        return _clean_amulecmd_output(out)
    return "ERROR: no password configured"


def download_from_cached_search(result_id):
    ctx = _last_search_context.copy()
    if not ctx.get("query"):
        return action_response("download", False, "SEARCH_EXPIRED", normalize_action_error("download", "SEARCH_EXPIRED"), status=409)
    if ctx.get("timestamp") and time.time() - ctx.get("timestamp", 0) > 15 * 60:
        return action_response("download", False, "SEARCH_EXPIRED", normalize_action_error("download", "SEARCH_EXPIRED"), status=409)
    matched = next((r for r in ctx.get("results", []) if int(r.get("id", -1)) == int(result_id)), None)
    if not matched:
        return action_response("download", False, "RESULT_NOT_FOUND", normalize_action_error("download", "RESULT_NOT_FOUND"), status=404)
    current_downloads = parse_downloads(run_amulecmd("show dl"))
    existing = check_duplicate_downloads(name=matched.get("name"), size_bytes=size_to_bytes(matched.get("size", "")), downloads=current_downloads)
    if existing:
        return action_response("download", False, "ALREADY_EXISTS", normalize_action_error("download", "ALREADY_EXISTS"), confirmed=True, data={"existing": existing}, status=409)
    interactive_output = run_amulecmd_interactive([f"search {ctx.get('type', 'kad')} {ctx.get('query', '')}", ("sleep", 3.5), "results", ("sleep", 0.3), f"download {int(result_id)}"], timeout=30)
    err = classify_amule_error(interactive_output)
    if err:
        return action_response("download", False, err, normalize_action_error("download", err, interactive_output), status=502)
    time.sleep(0.6)
    refreshed = parse_downloads(run_amulecmd("show dl"))
    created = check_duplicate_downloads(name=matched.get("name"), size_bytes=size_to_bytes(matched.get("size", "")), downloads=refreshed)
    if created:
        cache_clear("downloads")
        return action_response("download", True, "SUCCESS", "Téléchargement confirmé dans les transferts.", confirmed=True, data={"download": created})
    return action_response("download", False, "STATE_NOT_CONFIRMED", normalize_action_error("download", "STATE_NOT_CONFIRMED"), status=502, data={"output": interactive_output})


def add_ed2k_confirmed(link):
    link = (link or "").strip()
    if not link.startswith("ed2k://"):
        return action_response("add_ed2k", False, "INVALID_INPUT", normalize_action_error("add_ed2k", "INVALID_INPUT"), status=400)

    # Standard ed2k file link: fully confirm in downloads.
    parsed = parse_ed2k_link(link)
    if parsed:
        current_downloads = parse_downloads(run_amulecmd("show dl"))
        existing = check_duplicate_downloads(name=parsed["name"], hash_value=parsed["hash"], size_bytes=parsed["size"], downloads=current_downloads)
        if existing:
            return action_response("add_ed2k", False, "ALREADY_EXISTS", normalize_action_error("add_ed2k", "ALREADY_EXISTS"), confirmed=True, data={"existing": existing, "link": link}, status=409)
        output = run_amulecmd(f"add {link}", timeout=20)
        err = classify_amule_error(output)
        if err:
            return action_response("add_ed2k", False, err, normalize_action_error("add_ed2k", err, output), status=502, data={"link": link})
        time.sleep(0.6)
        refreshed = parse_downloads(run_amulecmd("show dl"))
        created = check_duplicate_downloads(name=parsed["name"], hash_value=parsed["hash"], size_bytes=parsed["size"], downloads=refreshed)
        if created:
            cache_clear("downloads", "servers")
            return action_response("add_ed2k", True, "SUCCESS", "Lien ED2K confirmé dans les transferts.", confirmed=True, data={"download": created, "link": link})
        return action_response("add_ed2k", False, "STATE_NOT_CONFIRMED", normalize_action_error("add_ed2k", "STATE_NOT_CONFIRMED"), status=502, data={"output": output, "link": link})

    # Server list link: best-effort confirmation through server list growth.
    if link.startswith("ed2k://|serverlist|"):
        before_raw = run_amulecmd("show servers", timeout=10)
        before_servers = parse_servers(before_raw)
        output = run_amulecmd(f"add {link}", timeout=20)
        err = classify_amule_error(output)
        if err:
            return action_response("add_ed2k", False, err, normalize_action_error("add_ed2k", err, output), status=502, data={"link": link})
        time.sleep(1.2)
        after_raw = run_amulecmd("show servers", timeout=10)
        after_servers = parse_servers(after_raw)
        cache_clear("servers")
        added = max(0, len(after_servers) - len(before_servers))
        confirmed = added > 0
        msg = "Liste de serveurs importée et confirmée." if confirmed else "Liste de serveurs envoyée à aMule. Vérifie l'onglet Serveurs."
        return action_response("add_ed2k", True, "SUCCESS", msg, confirmed=confirmed, data={"before_count": len(before_servers), "after_count": len(after_servers), "link": link})

    return action_response("add_ed2k", False, "INVALID_INPUT", normalize_action_error("add_ed2k", "INVALID_INPUT"), status=400)


def add_multiple_ed2k_confirmed(raw_text):
    links = extract_ed2k_links(raw_text)
    if not links:
        return action_response('add_ed2k', False, 'INVALID_INPUT', 'Aucun lien ED2K valide trouvé.', status=400)
    if len(links) == 1:
        return add_ed2k_confirmed(links[0])

    added = 0
    already = 0
    failed = 0
    confirmed_count = 0
    results = []

    for link in links:
        payload, status = add_ed2k_confirmed(link)
        info = {
            'link': link,
            'ok': bool(payload.get('ok')),
            'confirmed': bool(payload.get('confirmed')),
            'code': payload.get('code'),
            'message': payload.get('message', ''),
        }
        dl = (payload.get('data') or {}).get('download') or (payload.get('data') or {}).get('existing')
        if isinstance(dl, dict) and dl.get('name'):
            info['name'] = dl.get('name')
        results.append(info)
        if payload.get('ok') and payload.get('code') == 'SUCCESS':
            added += 1
        elif payload.get('code') == 'ALREADY_EXISTS':
            already += 1
        else:
            failed += 1
        if payload.get('confirmed'):
            confirmed_count += 1

    if failed == 0 and added > 0:
        code = 'SUCCESS'
        ok = True
        status = 200
    elif failed == 0 and already > 0:
        code = 'ALREADY_EXISTS'
        ok = True
        status = 200
    elif added > 0 or already > 0:
        code = 'PARTIAL_SUCCESS'
        ok = True
        status = 207
    else:
        code = 'COMMAND_FAILED'
        ok = False
        status = 502

    message = f"Traitement ED2K: {added} ajouté(s), {already} déjà présent(s), {failed} échec(s)."
    return action_response('add_ed2k', ok, code, message, confirmed=(confirmed_count == len(links) and len(links) > 0), status=status, data={
        'total': len(links),
        'added': added,
        'already_exists': already,
        'failed': failed,
        'results': results[:50],
    })


def change_transfer_state(action, hash_value=None):
    hash_value = (hash_value or "").strip()
    downloads = parse_downloads(run_amulecmd("show dl"))
    if hash_value and not any(d.get("hash") == hash_value for d in downloads):
        return action_response(action, False, "TRANSFER_NOT_FOUND", normalize_action_error(action, "TRANSFER_NOT_FOUND"), status=404)

    command = {"pause": "pause", "resume": "resume", "cancel": "cancel"}[action]
    output = run_amulecmd(f"{command} {hash_value}" if hash_value else command, timeout=20)
    err = classify_amule_error(output)
    if err:
        return action_response(action, False, err, normalize_action_error(action, err, output), status=502, data={"hash": hash_value} if hash_value else {})

    time.sleep(0.6)
    refreshed = parse_downloads(run_amulecmd("show dl"))

    if action == "cancel":
        if hash_value and any(d.get("hash") == hash_value for d in refreshed):
            return action_response(action, False, "STATE_NOT_CONFIRMED", normalize_action_error(action, "STATE_NOT_CONFIRMED"), status=502, data={"hash": hash_value})
        cache_clear("downloads")
        return action_response(action, True, "SUCCESS", "Suppression confirmée.", confirmed=True, data={"hash": hash_value, "changed_hashes": [hash_value] if hash_value else [], "removed_hashes": [hash_value] if hash_value else []} if hash_value else {"changed_hashes": [], "removed_hashes": []})

    target_states = {"pause": {"paused", "stopped"}, "resume": {"downloading", "waiting", "getting sources", "connecting", "queued"}}[action]

    if hash_value:
        after = next((d for d in refreshed if d.get("hash") == hash_value), None)
        if not after:
            return action_response(action, False, "TRANSFER_NOT_FOUND", normalize_action_error(action, "TRANSFER_NOT_FOUND"), status=404, data={"hash": hash_value})
        if after.get("status") in target_states:
            cache_clear("downloads")
            return action_response(action, True, "SUCCESS", "Pause confirmée." if action == "pause" else "Reprise confirmée.", confirmed=True, data={"download": after, "hash": hash_value, "changed_hashes": [hash_value]})
        return action_response(action, False, "STATE_NOT_CONFIRMED", normalize_action_error(action, "STATE_NOT_CONFIRMED"), status=502, data={"download": after, "hash": hash_value})

    # Bulk action best-effort confirmation
    before_paused = sum(1 for d in downloads if d.get("status") in {"paused", "stopped"})
    before_active = sum(1 for d in downloads if d.get("status") not in {"paused", "stopped"})
    after_paused = sum(1 for d in refreshed if d.get("status") in {"paused", "stopped"})
    after_active = sum(1 for d in refreshed if d.get("status") not in {"paused", "stopped"})
    confirmed = False
    if action == "pause":
        confirmed = after_active < before_active or (before_active > 0 and after_active == 0)
        message = "Pause globale confirmée." if confirmed else "Commande de pause envoyée. Vérifie les transferts."
    else:
        confirmed = after_paused < before_paused or before_paused == 0
        message = "Reprise globale confirmée." if confirmed else "Commande de reprise envoyée. Vérifie les transferts."
    cache_clear("downloads")
    return action_response(action, True, "SUCCESS", message, confirmed=confirmed, data={"before_active": before_active, "after_active": after_active, "before_paused": before_paused, "after_paused": after_paused, "changed_hashes": []})


def change_transfer_state_bulk(action, hashes):
    action = str(action or "").strip().lower()
    if action not in {"pause", "resume", "cancel"}:
        return action_response(f"bulk_{action or 'unknown'}", False, "INVALID_INPUT", "Action de lot invalide.", status=400)

    raw_hashes = hashes if isinstance(hashes, list) else []
    cleaned = []
    seen = set()
    for value in raw_hashes:
        h = str(value or "").strip()
        if not re.fullmatch(r"[0-9A-Fa-f]{32}", h):
            continue
        if h in seen:
            continue
        seen.add(h)
        cleaned.append(h)
    if not cleaned:
        return action_response(f"bulk_{action}", False, "INVALID_INPUT", "Aucun hash de transfert valide fourni.", status=400)

    before = parse_downloads(run_amulecmd("show dl"))
    before_map = {d.get("hash"): d for d in before if d.get("hash")}
    valid_hashes = []
    results = []

    for h in cleaned:
        dl = before_map.get(h)
        if not dl:
            results.append({
                "hash": h,
                "ok": False,
                "confirmed": False,
                "code": "TRANSFER_NOT_FOUND",
                "message": normalize_action_error(action, "TRANSFER_NOT_FOUND"),
            })
            continue
        valid_hashes.append(h)
        results.append({
            "hash": h,
            "name": dl.get("name", ""),
            "before_status": dl.get("status", ""),
            "ok": False,
            "confirmed": False,
            "code": "PENDING",
            "message": "En attente de confirmation",
        })

    if not valid_hashes:
        return action_response(f"bulk_{action}", False, "TRANSFER_NOT_FOUND", "Aucun transfert sélectionné n'existe encore dans aMule.", status=404, data={"results": results})

    command = {"pause": "pause", "resume": "resume", "cancel": "cancel"}[action]
    interactive_output = run_amulecmd_interactive([f"{command} {h}" for h in valid_hashes], timeout=max(25, 5 + len(valid_hashes) * 3))
    err = classify_amule_error(interactive_output)
    time.sleep(0.8)
    after = parse_downloads(run_amulecmd("show dl"))
    after_map = {d.get("hash"): d for d in after if d.get("hash")}
    target_states = {"pause": {"paused", "stopped"}, "resume": {"downloading", "waiting", "getting sources", "connecting", "queued", "allocating", "completing"}}.get(action, set())

    success = 0
    already = 0
    failed = 0
    confirmed_count = 0

    for item in results:
        if item.get("code") == "TRANSFER_NOT_FOUND":
            failed += 1
            continue
        h = item.get("hash")
        after_dl = after_map.get(h)
        item["after_status"] = after_dl.get("status") if after_dl else None
        if action == "cancel":
            if not after_dl:
                item.update({"ok": True, "confirmed": True, "code": "SUCCESS", "message": "Suppression confirmée."})
                success += 1
                confirmed_count += 1
            else:
                item.update({"code": "STATE_NOT_CONFIRMED", "message": normalize_action_error(action, "STATE_NOT_CONFIRMED")})
                failed += 1
            continue

        before_status = item.get("before_status") or ""
        after_status = item.get("after_status") or ""
        if action == "pause" and before_status in target_states and after_status in target_states:
            item.update({"ok": True, "confirmed": True, "code": "ALREADY_EXISTS", "message": "Déjà en pause."})
            already += 1
            confirmed_count += 1
        elif action == "resume" and before_status not in {"paused", "stopped"} and after_status not in {"paused", "stopped"}:
            item.update({"ok": True, "confirmed": True, "code": "ALREADY_EXISTS", "message": "Déjà actif."})
            already += 1
            confirmed_count += 1
        elif after_dl and after_status in target_states:
            item.update({"ok": True, "confirmed": True, "code": "SUCCESS", "message": "Pause confirmée." if action == "pause" else "Reprise confirmée."})
            success += 1
            confirmed_count += 1
        else:
            item.update({"code": "STATE_NOT_CONFIRMED", "message": normalize_action_error(action, "STATE_NOT_CONFIRMED")})
            failed += 1

    cache_clear("downloads")

    summary = {
        "total": len(cleaned),
        "valid": len(valid_hashes),
        "success": success,
        "already": already,
        "failed": failed,
        "missing": sum(1 for r in results if r.get("code") == "TRANSFER_NOT_FOUND"),
    }
    if failed == 0 and summary["missing"] == 0 and success > 0:
        code = "SUCCESS"
        ok = True
        status = 200
    elif success > 0 or already > 0:
        code = "PARTIAL_SUCCESS"
        ok = True
        status = 207
    elif err:
        code = err
        ok = False
        status = 502
    else:
        code = "STATE_NOT_CONFIRMED"
        ok = False
        status = 502

    action_label = {"pause": "Pause", "resume": "Reprise", "cancel": "Suppression"}[action]
    message = f"{action_label} lot: {success} confirmé(s), {already} déjà OK, {summary['missing']} introuvable(s), {failed} échec(s)."
    overview = summarize_transfer_action_results(results)
    removed_hashes = overview["confirmed_hashes"][:] if action == "cancel" else []
    return action_response(f"bulk_{action}", ok, code, message, confirmed=(confirmed_count == len(valid_hashes) and summary["missing"] == 0), status=status, data={
        "summary": summary,
        "results": results[:200],
        "overview": overview,
        "changed_hashes": overview["confirmed_hashes"],
        "removed_hashes": removed_hashes,
        "output": interactive_output if err else "",
    })


def execute_locked_action(action, fn):
    lock = acquire_action_lock(action)
    if not lock:
        payload, status = action_response(action, False, "LOCKED", normalize_action_error(action, "LOCKED"), status=409)
        record_action_event(payload, status)
        return payload, status
    try:
        payload, status = fn()
        record_action_event(payload, status)
        return payload, status
    finally:
        lock.release()


def build_debug_snapshot():
    diag = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}

    try:
        ps = subprocess.run(["pgrep", "-x", "amuled"], capture_output=True, text=True, timeout=5)
        diag["amuled_running"] = ps.returncode == 0
        diag["amuled_pid"] = ps.stdout.strip() or None
    except Exception as e:
        diag["amuled_running"] = False
        diag["amuled_pid_error"] = str(e)

    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        result_conn = s.connect_ex(("localhost", int(EC_PORT)))
        s.close()
        diag["port_4712_open"] = result_conn == 0
        diag["port_4712_errno"] = result_conn
    except Exception as e:
        diag["port_4712_open"] = False
        diag["port_4712_error"] = str(e)

    diag["ec_host"] = EC_HOST
    diag["ec_port"] = EC_PORT
    diag["password_mode_detected"] = _password_mode or "not yet"
    diag["cred_file"] = _cred_file
    diag["cred_file_exists"] = os.path.isfile(_cred_file)

    status_raw = run_amulecmd("status", timeout=8)
    status_info = parse_status(status_raw)
    diag["status"] = status_info
    diag["status_raw"] = status_raw[:800]

    downloads_raw = run_amulecmd("show dl", timeout=8)
    downloads = parse_downloads(downloads_raw)
    counts = {}
    for dl in downloads:
        key = dl.get("status") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    diag["downloads_count"] = len(downloads)
    diag["download_status_counts"] = counts

    diag["action_locks"] = {name: lock.locked() for name, lock in _action_locks.items()}
    search_age = int(time.time() - (_last_search_context.get("timestamp") or 0)) if _last_search_context.get("timestamp") else None
    diag["last_search"] = {
        "query": _last_search_context.get("query", ""),
        "type": _last_search_context.get("type", ""),
        "results_count": len(_last_search_context.get("results") or []),
        "age_seconds": search_age,
    }
    diag["recent_actions"] = get_action_history(12)
    diag["dashboard_config"] = get_dashboard_config()
    if get_dashboard_config().get("debug_mode", True):
        diag["recent_logs"] = list(_log_buffer)[-15:]
    else:
        diag["recent_logs"] = ["Mode debug étendu désactivé"]
    diag["digest"] = payload_digest({
        "amuled_running": diag.get("amuled_running"),
        "port_4712_open": diag.get("port_4712_open"),
        "status": diag.get("status"),
        "downloads_count": diag.get("downloads_count"),
        "download_status_counts": diag.get("download_status_counts"),
        "action_locks": diag.get("action_locks"),
        "last_search": diag.get("last_search"),
        "recent_actions": diag.get("recent_actions"),
        "dashboard_config": diag.get("dashboard_config"),
    })
    diag["generated_at"] = int(time.time())
    return diag


def classify_log_level(line):
    low = str(line or "").lower()
    if any(token in low for token in ("error", "exception", "fatal", "traceback", "failed")):
        return "error"
    if any(token in low for token in ("warn", "warning")):
        return "warn"
    if any(token in low for token in ("info", "notice", "ok")):
        return "info"
    return "other"


def filter_log_lines(lines, level="all", contains="", limit=120):
    try:
        limit = max(1, min(500, int(limit)))
    except Exception:
        limit = 120
    level = str(level or "all").strip().lower()
    contains = str(contains or "").strip().lower()
    tail = list(lines or [])[-1000:]
    filtered = []
    counts = {"error": 0, "warn": 0, "info": 0, "other": 0}
    for line in tail:
        lvl = classify_log_level(line)
        counts[lvl] = counts.get(lvl, 0) + 1
        if level != "all" and lvl != level:
            continue
        if contains and contains not in str(line).lower():
            continue
        filtered.append(line)
    return {
        "lines": filtered[-limit:],
        "counts": counts,
        "returned": min(len(filtered), limit),
        "matched_total": len(filtered),
        "available_total": len(tail),
        "level": level,
        "contains": contains,
        "limit": limit,
    }


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def check_auth(self):
        cookie = self.headers.get('Cookie', '')
        if f'token={AUTH_TOKEN}' in cookie: return True
        qs = parse_qs(urlparse(self.path).query)
        return qs.get('token', [None])[0] == AUTH_TOKEN

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/login":
            self.serve_login(); return

        if path == "/api/login":
            cfg = get_dashboard_config()
            retry_after = rate_limit_retry_after("login", get_client_ip(self), cfg.get("login_rate_limit_per_minute", 20), 60)
            if retry_after:
                self.send_json({"ok": False, "error": f"Trop de tentatives. Réessaie dans {retry_after}s.", "retry_after": retry_after}, 429)
                return
            pwd = qs.get("password", [""])[0]
            if pwd == DASHBOARD_PWD:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Set-Cookie', f'token={AUTH_TOKEN}; Path=/; HttpOnly; SameSite=Strict')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "token": AUTH_TOKEN}).encode())
            else:
                self.send_json({"ok": False, "error": "Mot de passe incorrect"}, 401)
            return

        if path == "/api/logout":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Set-Cookie', 'token=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
            return

        if not self.check_auth():
            if path.startswith("/api/"): self.send_json({"error": "unauthorized"}, 401)
            else:
                self.send_response(302); self.send_header('Location', '/login'); self.end_headers()
            return

        # ── API Routes ──
        if path == "/api/status":
            cached = cache_get("status", 3)
            if_digest = qs.get("if_digest", [""])[0]
            if not cached:
                cached = build_status_payload()
                cache_set("status", cached)
            # Record stats snapshot for daily tracking
            try:
                record_stats_snapshot(cached.get("download_speed", 0), cached.get("upload_speed", 0))
            except Exception:
                pass
            if if_digest and cached.get("digest") == if_digest:
                self.send_json(unchanged_payload(cached.get("digest"), cached=True))
                return
            self.send_json(cached)

        elif path == "/api/downloads":
            include_raw = qs.get("include_raw", ["1"])[0] not in ("0", "false", "no")
            if_digest = qs.get("if_digest", [""])[0]
            cached = cache_get("downloads", 2)
            if not cached:
                cached = build_downloads_payload(include_raw=include_raw)
                cache_set("downloads", cached)
            elif include_raw and "raw" not in cached:
                cached = build_downloads_payload(include_raw=True)
                cache_set("downloads", cached)
            if if_digest and cached.get("digest") == if_digest:
                self.send_json(unchanged_payload(cached.get("digest"), count=cached.get("count", 0), summary=cached.get("summary", {}), cached=True))
                return
            if include_raw:
                self.send_json(cached)
            else:
                payload = dict(cached)
                payload.pop("raw", None)
                self.send_json(payload)

        elif path == "/api/download_detail":
            hash_value = qs.get("hash", [""])[0]
            if not hash_value:
                self.send_json({"ok": False, "error": "hash requis"}, 400)
                return
            raw = run_amulecmd("show dl")
            downloads = parse_downloads(raw)
            detail = get_download_detail(hash_value, raw=raw, downloads=downloads)
            if not detail:
                self.send_json({"ok": False, "error": "transfert introuvable"}, 404)
                return
            self.send_json({"ok": True, "download": detail})

        elif path == "/api/search":
            query = qs.get("q", [""])[0]
            stype = qs.get("type", ["kad"])[0]
            if not query: self.send_json({"error": "q requis"}, 400); return
            if stype not in ("kad", "global", "local"): stype = "kad"
            run_amulecmd(f"search {stype} {query}")
            time.sleep(3)
            raw = run_amulecmd("results")
            results = parse_search_results(raw)
            # Record search history
            try:
                add_search_history(query, stype, len(results))
                touch_saved_search(query, stype)
            except Exception:
                pass
            set_last_search_context(query, stype, results)
            record_action_event({
                "action": "search",
                "ok": True,
                "confirmed": True,
                "code": "SUCCESS",
                "message": f"Recherche terminée: {len(results)} résultat(s).",
                "data": {"query": query},
            }, 200)
            self.send_json({"query": query, "type": stype, "results": results, "raw": raw})

        elif path == "/api/results":
            raw = run_amulecmd("results")
            self.send_json({"results": parse_search_results(raw)})

        elif path == "/api/download":
            blocked = guard_write_action(self, "download")
            if blocked:
                self.send_json(*blocked)
                return
            num = qs.get("id", [""])[0]
            if not num.isdigit():
                payload, status = action_response("download", False, "INVALID_INPUT", normalize_action_error("download", "INVALID_INPUT"), status=400)
            else:
                payload, status = execute_locked_action("download", lambda: download_from_cached_search(int(num)))
            self.send_json(payload, status)

        elif path == "/api/add_ed2k":
            blocked = guard_write_action(self, "add_ed2k")
            if blocked:
                self.send_json(*blocked)
                return
            link = qs.get("link", [""])[0]
            payload, status = execute_locked_action("add_ed2k", lambda: add_multiple_ed2k_confirmed(link))
            self.send_json(payload, status)

        elif path == "/api/files":
            cached = cache_get("files", 10)
            if cached: self.send_json(cached); return
            data = list_files(INCOMING_DIR)
            cache_set("files", data)
            self.send_json(data)

        elif path == "/api/disk":
            self.send_json(get_disk_info())

        elif path == "/api/pause":
            blocked = guard_write_action(self, "pause")
            if blocked:
                self.send_json(*blocked)
                return
            h = qs.get("hash", [""])[0]
            payload, status = execute_locked_action("pause", lambda: change_transfer_state("pause", h))
            self.send_json(payload, status)

        elif path == "/api/resume":
            blocked = guard_write_action(self, "resume")
            if blocked:
                self.send_json(*blocked)
                return
            h = qs.get("hash", [""])[0]
            payload, status = execute_locked_action("resume", lambda: change_transfer_state("resume", h))
            self.send_json(payload, status)

        elif path == "/api/cancel":
            blocked = guard_write_action(self, "cancel")
            if blocked:
                self.send_json(*blocked)
                return
            h = qs.get("hash", [""])[0]
            if not h:
                payload, status = action_response("cancel", False, "INVALID_INPUT", "Hash requis.", status=400)
            else:
                payload, status = execute_locked_action("cancel", lambda: change_transfer_state("cancel", h))
            self.send_json(payload, status)

        elif path == "/api/connect":
            blocked = guard_write_action(self, "connect")
            if blocked:
                self.send_json(*blocked)
                return
            target = qs.get("target", ["all"])[0]  # all, ed2k, kad
            results = {}

            if target in ("all", "ed2k"):
                _log("CONNECT ED2K: sending 'connect ed2k'")
                out1 = run_amulecmd("connect ed2k", timeout=10)
                results["connect_ed2k"] = out1
                results["connect_ok"] = "successful" in out1.lower()

                # Wait for handshake then check
                time.sleep(5)
                status_raw = run_amulecmd("status", timeout=8)
                results["status_after"] = status_raw

                # Detect state
                sl = status_raw.lower()
                if "now connecting" in sl:
                    results["ed2k_state"] = "connecting"
                    # Wait a bit more and re-check
                    time.sleep(5)
                    status_raw2 = run_amulecmd("status", timeout=8)
                    results["status_final"] = status_raw2
                    sl2 = status_raw2.lower()
                    if re.search(r'ed2k.*connected', sl2) and "not connected" not in sl2:
                        results["ed2k_state"] = "connected"
                    elif "now connecting" in sl2:
                        results["ed2k_state"] = "connecting"
                elif re.search(r'ed2k.*connected', sl) and "not connected" not in sl:
                    results["ed2k_state"] = "connected"
                else:
                    results["ed2k_state"] = "disconnected"
                    # Try specific servers as fallback
                    _log("CONNECT ED2K: trying specific servers...")
                    servers_raw = run_amulecmd("show servers", timeout=8)
                    server_addrs = re.findall(r'((?:\d{1,3}\.){3}\d{1,3}:\d{2,5})', servers_raw)
                    results["server_attempts"] = []
                    for addr in server_addrs[:3]:
                        out = run_amulecmd(f"connect {addr}", timeout=10)
                        results["server_attempts"].append({"addr": addr, "output": out[:200]})
                        time.sleep(1)
                    time.sleep(5)
                    final = run_amulecmd("status", timeout=8)
                    results["status_final"] = final
                    fl = final.lower()
                    if re.search(r'ed2k.*connected', fl) and "not connected" not in fl:
                        results["ed2k_state"] = "connected"
                    elif "now connecting" in fl:
                        results["ed2k_state"] = "connecting"

                _log(f"CONNECT ED2K final state: {results['ed2k_state']}")

            if target in ("all", "kad"):
                out_kad = run_amulecmd("connect kad", timeout=10)
                results["connect_kad"] = out_kad

            cache_clear("status", "servers")
            self.send_json({"ok": True, "results": results})

        elif path == "/api/servers" or path == "/api/server_sources":
            raw = run_amulecmd("show servers")
            self.send_json({"sources": get_server_sources_payload(), "servers": parse_servers(raw), "raw": raw})

        elif path == "/api/stats":
            self.send_json({"raw": run_amulecmd("statistics")})

        elif path == "/api/action_history":
            limit = int(qs.get("limit", ["30"])[0] or "30")
            payload = build_action_history_payload(limit)
            if_digest = qs.get("if_digest", [""])[0]
            if if_digest and payload.get("digest") == if_digest:
                self.send_json(unchanged_payload(payload.get("digest"), limit=payload.get("limit", _action_history.maxlen)))
            else:
                self.send_json(payload)

        elif path == "/api/app_config":
            self.send_json({"ok": True, "config": get_dashboard_config(), "read_only": is_read_only_enabled()})

        elif path == "/api/export_bundle":
            include_action_history = qs.get("include_action_history", ["0"])[0] in ("1", "true", "yes")
            include_stats = qs.get("include_stats", ["0"])[0] in ("1", "true", "yes")
            self.send_json(build_export_bundle(include_action_history=include_action_history, include_stats=include_stats))

        elif path == "/api/debug":
            payload = build_debug_snapshot()
            if_digest = qs.get("if_digest", [""])[0]
            if if_digest and payload.get("digest") == if_digest:
                self.send_json(unchanged_payload(payload.get("digest")))
            else:
                self.send_json(payload)

        elif path == "/api/organize":
            blocked = guard_write_action(self, "organize")
            if blocked:
                self.send_json(*blocked)
                return
            try:
                subprocess.run(["/opt/scripts/file-organizer.sh"], capture_output=True, timeout=30)
                cache_clear("files")
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/api/settings":
            settings = load_settings()
            self.send_json({"ok": True, "settings": settings, "dashboard": settings.get("dashboard", {})})

        elif path == "/api/kad/status":
            raw = run_amulecmd("status")
            kad_ok = bool(re.search(r'kad.*(running|connected|firewalled)', raw, re.I))
            ed2k_ok = bool(re.search(r'ed2k.*connected', raw, re.I)) and not bool(re.search(r'ed2k.*not connected', raw, re.I))
            self.send_json({"kad_connected": kad_ok, "ed2k_connected": ed2k_ok, "raw": raw})

        elif path == "/api/kad/reconnect":
            blocked = guard_write_action(self, "reconnect")
            if blocked:
                self.send_json(*blocked)
                return
            out1 = run_amulecmd("connect kad")
            out2 = run_amulecmd("connect ed2k")
            cache_clear("status")
            self.send_json({"ok": True, "output": out1 + "\n" + out2})

        elif path == "/api/scan_now":
            blocked = guard_write_action(self, "scan_now")
            if blocked:
                self.send_json(*blocked)
                return
            # Trigger an immediate source scan
            def do_scan():
                try:
                    subprocess.run(["/opt/scripts/source-scanner.sh"], capture_output=True, timeout=120)
                except Exception:
                    pass
                cache_clear("status", "servers")
            threading.Thread(target=do_scan, daemon=True).start()
            self.send_json({"ok": True, "message": "Scan lancé en arrière-plan"})

        elif path == "/api/logs":
            if not get_dashboard_config().get("debug_mode", True):
                self.send_json({"ok": False, "error": "Mode debug désactivé"}, 403)
                return
            log_name = qs.get("name", [""])[0]
            level = qs.get("level", ["all"])[0]
            contains = qs.get("contains", [""])[0]
            lines_limit = qs.get("lines", ["120"])[0]
            valid_logs = {"kad-monitor": "/var/log/kad-monitor.log", "source-scanner": "/var/log/source-scanner.log",
                          "server-update": "/var/log/server-update.log", "file-organizer": "/var/log/file-organizer.log",
                          "backup": "/var/log/backup.log", "stall-detector": "/var/log/stall-detector.log"}
            if log_name in valid_logs:
                try:
                    with open(valid_logs[log_name], "r") as f:
                        payload = filter_log_lines(f.readlines(), level=level, contains=contains, limit=lines_limit)
                    payload.update({"ok": True, "name": log_name})
                    self.send_json(payload)
                except FileNotFoundError:
                    self.send_json({"ok": True, "name": log_name, "lines": ["(aucun log encore)"], "counts": {"error": 0, "warn": 0, "info": 0, "other": 0}, "returned": 1, "matched_total": 1, "available_total": 0, "level": level, "contains": contains, "limit": int(lines_limit or 120)})
            else:
                self.send_json({"error": "Log inconnu"}, 400)

        elif path == "/api/search_history":
            h = _load_history()
            self.send_json({"searches": h.get("searches", [])})

        elif path == "/api/saved_searches":
            self.send_json({"saved_searches": get_saved_searches()})

        elif path == "/api/favorites":
            h = _load_history()
            self.send_json({"favorites": h.get("favorites", [])})

        elif path == "/api/stats_history":
            stats = _load_stats()
            self.send_json({"daily": stats.get("daily", {})})

        elif path == "/api/bookmarklet":
            # Generate bookmarklet code
            host = self.headers.get("Host", "localhost:8078")
            scheme = "http"
            url = f"{scheme}://{host}"
            code = get_bookmarklet_code(url, AUTH_TOKEN)
            self.send_json({"bookmarklet": code, "url": url})

        elif path == "/" or path == "/index.html":
            self.serve_file(STATIC_DIR / "index.html", "text/html")

        elif path == "/manifest.json":
            self.serve_file(STATIC_DIR / "manifest.json", "application/json")

        elif path.startswith("/icons/"):
            fname = path.split("/")[-1]
            safe = re.sub(r'[^a-zA-Z0-9._-]', '', fname)
            fpath = STATIC_DIR / "icons" / safe
            ctype = "image/png"
            if safe.endswith(".ico"): ctype = "image/x-icon"
            self.serve_file(fpath, ctype)

        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if not self.check_auth():
            self.send_json({"error": "unauthorized"}, 401); return
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode() if length else ""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self.send_json({"error": "JSON invalide"}, 400)
            return

        if parsed.path == "/api/action_history/clear":
            clear_action_history_store()
            self.send_json({"ok": True, "message": "Historique des actions vidé."})
            return

        if parsed.path == "/api/transfers/bulk_action":
            blocked = guard_write_action(self, "bulk_action")
            if blocked:
                self.send_json(*blocked)
                return
            action = str(data.get("action") or "").strip().lower()
            hashes = data.get("hashes") if isinstance(data.get("hashes"), list) else []
            payload, status = execute_locked_action(f"bulk_{action or 'unknown'}", lambda: change_transfer_state_bulk(action, hashes))
            self.send_json(payload, status)
            return

        if parsed.path == "/api/logout":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Set-Cookie', 'token=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
            return

        if parsed.path == "/api/dashboard_config":
            settings = load_settings()
            settings["dashboard"] = normalize_dashboard_config(data)
            if save_settings(settings):
                self.send_json({"ok": True, "config": settings["dashboard"]})
            else:
                self.send_json({"ok": False, "error": "Impossible de sauvegarder la configuration"}, 500)
            return

        if parsed.path == "/api/import_bundle":
            blocked = guard_write_action(self, "import_bundle")
            if blocked:
                self.send_json(*blocked)
                return
            raw_bundle = data.get("bundle")
            mode = str(data.get("mode") or "merge")
            if raw_bundle is None:
                self.send_json({"ok": False, "error": "Bundle requis"}, 400)
                return
            try:
                summary = import_dashboard_bundle(raw_bundle, mode=mode)
                self.send_json({"ok": True, "summary": summary, "config": get_dashboard_config()})
            except json.JSONDecodeError:
                self.send_json({"ok": False, "error": "JSON du bundle invalide"}, 400)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return

        blocked = guard_write_action(self, parsed.path.rsplit('/', 1)[-1])
        if blocked:
            self.send_json(*blocked)
            return

        if parsed.path == "/api/add_ed2k":
            link_blob = str(data.get("link") or data.get("text") or "")
            payload, status = execute_locked_action("add_ed2k", lambda: add_multiple_ed2k_confirmed(link_blob))
            self.send_json(payload, status)
        elif parsed.path == "/api/server_sources/import":
            sources = data.get("sources") or []
            custom_url = str(data.get("custom_url", "")).strip()
            if custom_url:
                sources = list(sources) + [custom_url]
            reconnect = bool(data.get("reconnect", True))
            result = import_server_sources(sources, reconnect=reconnect)
            status = 200 if result.get("ok") else 502
            self.send_json(result, status)

        elif parsed.path == "/api/settings":
            # Save settings
            global SERVER_SOURCES
            new_settings = normalize_settings(data)
            if save_settings(new_settings):
                SERVER_SOURCES = get_server_sources_from_settings()
                self.send_json({"ok": True, "settings": new_settings})
            else:
                self.send_json({"error": "Impossible de sauvegarder"}, 500)

        elif parsed.path == "/api/settings/add_source":
            # Add a new server source
            settings = load_settings()
            new_src = {
                "key": data.get("key", f"custom_{int(time.time())}"),
                "label": data.get("label", data.get("url", "Custom")),
                "kind": data.get("kind", "serverlist"),
                "url": data.get("url", ""),
                "priority": int(data.get("priority", 50)),
                "enabled": True,
                "description": data.get("description", "Source ajoutée manuellement"),
            }
            if not new_src["url"]:
                self.send_json({"error": "URL requise"}, 400)
                return
            if "server_sources" not in settings:
                settings["server_sources"] = []
            # Check duplicate
            existing_urls = [s.get("url") for s in settings["server_sources"]]
            if new_src["url"] in existing_urls:
                self.send_json({"error": "Source déjà existante"}, 409)
                return
            settings["server_sources"].append(new_src)
            if save_settings(settings):
                SERVER_SOURCES = get_server_sources_from_settings()
                self.send_json({"ok": True, "source": new_src})
            else:
                self.send_json({"error": "Erreur sauvegarde"}, 500)

        elif parsed.path == "/api/settings/remove_source":
            settings = load_settings()
            key = data.get("key", "")
            url = data.get("url", "")
            before = len(settings.get("server_sources", []))
            settings["server_sources"] = [
                s for s in settings.get("server_sources", [])
                if s.get("key") != key and s.get("url") != url
            ]
            after = len(settings["server_sources"])
            if save_settings(settings):
                SERVER_SOURCES = get_server_sources_from_settings()
                self.send_json({"ok": True, "removed": before - after})
            else:
                self.send_json({"error": "Erreur sauvegarde"}, 500)

        elif parsed.path == "/api/settings/toggle_source":
            settings = load_settings()
            key = data.get("key", "")
            toggled = False
            for s in settings.get("server_sources", []):
                if s.get("key") == key:
                    s["enabled"] = not s.get("enabled", True)
                    toggled = True
                    break
            if toggled and save_settings(settings):
                SERVER_SOURCES = get_server_sources_from_settings()
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "Source non trouvée"}, 404)

        elif parsed.path == "/api/saved_searches/add":
            query = str(data.get("query") or "").strip()
            search_type = str(data.get("type") or "kad").strip().lower()
            label = str(data.get("label") or "").strip()
            ok, message = add_saved_search(query, search_type, label)
            if ok:
                self.send_json({"ok": True, "message": message})
            else:
                self.send_json({"ok": False, "error": message}, 409 if query else 400)

        elif parsed.path == "/api/saved_searches/remove":
            search_id = str(data.get("id") or "").strip()
            if not search_id:
                self.send_json({"ok": False, "error": "id requis"}, 400)
                return
            removed = remove_saved_search(search_id)
            self.send_json({"ok": bool(removed), "removed": removed}, 200 if removed else 404)

        elif parsed.path == "/api/favorites/add":
            name = data.get("name", "")
            link = data.get("link", "")
            if not link.startswith("ed2k://"):
                self.send_json({"error": "Lien ed2k invalide"}, 400)
                return
            added = add_favorite(name, link, data.get("size", ""), data.get("sources", 0))
            self.send_json({"ok": True, "added": added})

        elif parsed.path == "/api/favorites/remove":
            link = data.get("link", "")
            removed = remove_favorite(link)
            self.send_json({"ok": True, "removed": removed})

        elif parsed.path == "/api/favorites/download":
            link_blob = str(data.get("link") or data.get("text") or "")
            payload, status = execute_locked_action("add_ed2k", lambda: add_multiple_ed2k_confirmed(link_blob))
            self.send_json(payload, status)

        elif parsed.path == "/api/search_history/clear":
            h = _load_history()
            h["searches"] = []
            _save_history(h)
            self.send_json({"ok": True})

        else:
            self.send_json({"error": "not found"}, 404)

    def serve_file(self, filepath, ctype):
        try:
            content = open(filepath, 'rb').read()
            self.send_response(200)
            self.send_header('Content-Type', f'{ctype}; charset=utf-8')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404); self.end_headers()

    def serve_login(self):
        html = b"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>aMule Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,system-ui,sans-serif;background:#0f1117;color:#e4e4e7;
display:flex;align-items:center;justify-content:center;min-height:100vh}
.c{background:#1a1b23;border:1px solid #2a2b35;border-radius:16px;padding:40px;
width:360px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.4)}
h1{font-size:1.4em;margin-bottom:8px}
.s{color:#71717a;margin-bottom:24px;font-size:.9em}
input{width:100%;padding:12px 16px;background:#0f1117;border:1px solid #2a2b35;
border-radius:8px;color:#e4e4e7;font-size:1em;margin-bottom:16px;outline:none}
input:focus{border-color:#6366f1}
button{width:100%;padding:12px;background:#6366f1;color:#fff;border:none;border-radius:8px;
font-size:1em;cursor:pointer;font-weight:600}
button:hover{background:#4f46e5}
.e{color:#ef4444;font-size:.85em;margin-bottom:12px;display:none}
</style></head><body>
<div class="c"><h1>&#128052; aMule Dashboard</h1><p class="s">ZimaBoard Edition</p>
<div class="e" id="e">Mot de passe incorrect</div>
<input type="password" id="p" placeholder="Mot de passe" autofocus onkeydown="if(event.key==='Enter')go()">
<button onclick="go()">Connexion</button></div>
<script>async function go(){const p=document.getElementById('p').value;
const r=await fetch('/api/login?password='+encodeURIComponent(p));const d=await r.json();
if(d.ok)window.location='/';else document.getElementById('e').style.display='block'}</script>
</body></html>"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(html))
        self.end_headers()
        self.wfile.write(html)


if __name__ == "__main__":
    print(f"[DASHBOARD] Port {DASHBOARD_PORT} — en attente de connexions...")
    http.server.HTTPServer(("0.0.0.0", DASHBOARD_PORT), Handler).serve_forever()
