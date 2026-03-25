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


def load_settings():
    """Load persistent settings from JSON file."""
    try:
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_settings(settings):
    """Save settings to JSON file."""
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
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
import collections
_log_buffer = collections.deque(maxlen=50)

def _log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _log_buffer.append(line)
    print(f"[DASHBOARD] {line}", flush=True)


def _clean_amulecmd_output(output):
    """Remove amulecmd header lines from output."""
    lines = output.split("\n")
    clean = []
    skip_header = True
    for line in lines:
        if skip_header and ("Connected to" in line or "This is amulecmd" in line
                            or "Creating client" in line or "---" in line
                            or line.strip() == "" or "aMule" in line):
            continue
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
                # Try to extract server name: "Connected to ServerName (ip:port)"
                m = re.search(r'connected\s+to\s+(.+?)(?:\s*\(|$)', orig, re.I)
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
    downloads = []
    current = None
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue

        # New download entry: "> filename" or "N) filename"
        if line.startswith(">") or re.match(r'^\d+\)', line):
            if current:
                downloads.append(current)
            name = re.sub(r'^[>\d\)\s]+', '', line).strip()
            current = {"name": name, "size": "", "progress": 0, "speed": 0,
                        "sources": 0, "status": "unknown", "hash": ""}
        elif current:
            ll = line.lower()
            # Size - multiple formats
            if "size" in ll:
                m = re.search(r'([\d.]+\s*[KMGT]?i?B)', line, re.I)
                if m:
                    current["size"] = m.group(1)
            # Progress percentage
            if "%" in line:
                m = re.search(r'([\d.]+)\s*%', line)
                if m:
                    current["progress"] = float(m.group(1))
            # "Done: X / Y" format
            if "done" in ll:
                m = re.search(r'done.*?([\d.]+)\s*/\s*([\d.]+)', ll)
                if m:
                    try:
                        done = float(m.group(1))
                        total = float(m.group(2))
                        if total > 0:
                            current["progress"] = round(done / total * 100, 1)
                    except ValueError:
                        pass
            # Sources
            if "source" in ll:
                m = re.search(r'(\d+)\s*(?:source|src)', ll)
                if m:
                    current["sources"] = int(m.group(1))
            # Speed - multiple formats
            if "kb/s" in ll or "bytes/s" in ll or "speed" in ll:
                m = re.search(r'([\d.]+)\s*kb/s', ll)
                if m:
                    current["speed"] = float(m.group(1))
                else:
                    m2 = re.search(r'([\d.]+)\s*bytes/s', ll)
                    if m2:
                        current["speed"] = float(m2.group(1)) / 1024
            # Hash
            if re.match(r'^[0-9a-f]{32}$', line):
                current["hash"] = line
            # Status keywords
            for st in ["downloading", "paused", "waiting", "completing",
                        "complete", "hashing", "error", "stopped",
                        "getting sources", "allocating"]:
                if st in ll:
                    current["status"] = st
                    break

    if current:
        downloads.append(current)
    return downloads


def parse_search_results(raw):
    results = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("---"): continue
        m = re.match(r'^(\d+)\)\s+(.+?)\s+([\d.]+\s*[KMGT]?B)\s+Source[s]?:\s*(\d+)', line, re.I)
        if m:
            results.append({"id": int(m.group(1)), "name": m.group(2).strip(),
                            "size": m.group(3), "sources": int(m.group(4))})
        else:
            m2 = re.match(r'^(\d+)\)\s+(.+)', line)
            if m2:
                results.append({"id": int(m2.group(1)), "name": m2.group(2).strip(),
                                "size": "", "sources": 0})
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


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
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
            pwd = qs.get("password", [""])[0]
            if pwd == DASHBOARD_PWD:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Set-Cookie', f'token={AUTH_TOKEN}; Path=/; HttpOnly; SameSite=Strict')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "token": AUTH_TOKEN}).encode())
            else:
                self.send_json({"ok": False, "error": "Mot de passe incorrect"}, 401)
            return

        if not self.check_auth():
            if path.startswith("/api/"): self.send_json({"error": "unauthorized"}, 401)
            else:
                self.send_response(302); self.send_header('Location', '/login'); self.end_headers()
            return

        # ── API Routes ──
        if path == "/api/status":
            cached = cache_get("status", 3)
            if cached: self.send_json(cached); return
            data = parse_status(run_amulecmd("status"))
            data["disk"] = get_disk_info()
            cache_set("status", data)
            self.send_json(data)

        elif path == "/api/downloads":
            raw = run_amulecmd("show dl")
            data = parse_downloads(raw)
            # Don't cache too aggressively during transfers
            cache_set("downloads", {"downloads": data, "raw": raw})
            self.send_json({"downloads": data, "raw": raw, "count": len(data)})

        elif path == "/api/search":
            query = qs.get("q", [""])[0]
            stype = qs.get("type", ["kad"])[0]
            if not query: self.send_json({"error": "q requis"}, 400); return
            if stype not in ("kad", "global", "local"): stype = "kad"
            run_amulecmd(f"search {stype} {query}")
            time.sleep(3)
            raw = run_amulecmd("results")
            self.send_json({"query": query, "type": stype, "results": parse_search_results(raw), "raw": raw})

        elif path == "/api/results":
            raw = run_amulecmd("results")
            self.send_json({"results": parse_search_results(raw)})

        elif path == "/api/download":
            num = qs.get("id", [""])[0]
            if num: self.send_json({"ok": True, "output": run_amulecmd(f"download {num}")})
            else: self.send_json({"error": "id requis"}, 400)

        elif path == "/api/add_ed2k":
            link = qs.get("link", [""])[0]
            if link and link.startswith("ed2k://"):
                output = run_amulecmd(f"add {link}")
                cache_clear("servers")
                self.send_json({"ok": True, "output": output})
            else: self.send_json({"error": "lien ed2k invalide"}, 400)

        elif path == "/api/files":
            cached = cache_get("files", 10)
            if cached: self.send_json(cached); return
            data = list_files(INCOMING_DIR)
            cache_set("files", data)
            self.send_json(data)

        elif path == "/api/disk":
            self.send_json(get_disk_info())

        elif path == "/api/pause":
            h = qs.get("hash", [""])[0]
            self.send_json({"ok": True, "output": run_amulecmd(f"pause {h}" if h else "pause")})

        elif path == "/api/resume":
            h = qs.get("hash", [""])[0]
            self.send_json({"ok": True, "output": run_amulecmd(f"resume {h}" if h else "resume")})

        elif path == "/api/cancel":
            h = qs.get("hash", [""])[0]
            if h: self.send_json({"ok": True, "output": run_amulecmd(f"cancel {h}")})
            else: self.send_json({"error": "hash requis"}, 400)

        elif path == "/api/connect":
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

        elif path == "/api/debug":
            diag = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}

            # 1. Is amuled running?
            try:
                ps = subprocess.run(["pgrep", "-x", "amuled"], capture_output=True, text=True, timeout=5)
                diag["amuled_running"] = ps.returncode == 0
                diag["amuled_pid"] = ps.stdout.strip() or None
            except Exception as e:
                diag["amuled_running"] = f"check failed: {e}"

            # 2. Is port 4712 listening?
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3)
                result_conn = s.connect_ex(("localhost", int(EC_PORT)))
                s.close()
                diag["port_4712_open"] = result_conn == 0
                diag["port_4712_errno"] = result_conn
            except Exception as e:
                diag["port_4712_open"] = f"check failed: {e}"

            # Also try 127.0.0.1
            try:
                import socket
                s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s2.settimeout(3)
                r2 = s2.connect_ex(("127.0.0.1", int(EC_PORT)))
                s2.close()
                diag["port_4712_open_127"] = r2 == 0
            except Exception:
                pass

            # 3. Passwords configured
            diag["ec_host"] = EC_HOST
            diag["ec_port"] = EC_PORT
            diag["password_plain"] = EC_PASSWORD[:3] + "***" + EC_PASSWORD[-2:] if len(EC_PASSWORD) > 5 else repr(EC_PASSWORD)
            diag["password_hash"] = EC_PASSWORD_HASH[:10] + "..." if EC_PASSWORD_HASH else "(empty)"
            diag["password_mode_detected"] = _password_mode or "not yet"
            diag["cred_file"] = _cred_file
            diag["cred_file_exists"] = os.path.isfile(_cred_file)

            # 4. Read what amule.conf says the ECPassword should be
            amule_conf = os.path.join(AMULE_HOME, "amule.conf")
            diag["amule_conf_exists"] = os.path.isfile(amule_conf)
            if os.path.isfile(amule_conf):
                try:
                    with open(amule_conf) as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("ECPassword="):
                                conf_hash = line.split("=", 1)[1]
                                diag["amule_conf_ECPassword"] = conf_hash
                                diag["our_hash_matches_conf"] = (EC_PASSWORD_HASH == conf_hash)
                                # Also check: maybe amulecmd needs the hash that's IN the conf
                                if not diag.get("our_hash_matches_conf"):
                                    diag["MISMATCH_DETAIL"] = f"amule.conf has '{conf_hash}' but we computed '{EC_PASSWORD_HASH}'"
                                break
                except Exception as e:
                    diag["amule_conf_read_error"] = str(e)

            # 5. Try raw amulecmd with plain password
            raw_plain = _exec_amulecmd("status", EC_PASSWORD, timeout=8) if EC_PASSWORD else "(no plain pwd)"
            diag["test_plain"] = raw_plain[:400]
            diag["test_plain_auth_ok"] = "Authentication failed" not in str(raw_plain)

            # 6. Try raw amulecmd with hash
            if EC_PASSWORD_HASH:
                raw_hash = _exec_amulecmd("status", EC_PASSWORD_HASH, timeout=8)
                diag["test_hash"] = raw_hash[:400]
                diag["test_hash_auth_ok"] = "Authentication failed" not in str(raw_hash)

            # 7. Try with the hash FROM amule.conf directly
            conf_hash = diag.get("amule_conf_ECPassword", "")
            if conf_hash and conf_hash != EC_PASSWORD_HASH and conf_hash != EC_PASSWORD:
                raw_conf = _exec_amulecmd("status", conf_hash, timeout=8)
                diag["test_conf_hash"] = raw_conf[:400]
                diag["test_conf_hash_auth_ok"] = "Authentication failed" not in str(raw_conf)

            # 8. Recent dashboard logs
            diag["recent_logs"] = list(_log_buffer)[-15:]

            self.send_json(diag)

        elif path == "/api/organize":
            try:
                subprocess.run(["/opt/scripts/file-organizer.sh"], capture_output=True, timeout=30)
                cache_clear("files")
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/api/settings":
            settings = load_settings()
            if settings:
                self.send_json({"ok": True, "settings": settings})
            else:
                self.send_json({"ok": False, "error": "Fichier settings introuvable"}, 404)

        elif path == "/api/kad/status":
            raw = run_amulecmd("status")
            kad_ok = bool(re.search(r'kad.*(running|connected|firewalled)', raw, re.I))
            ed2k_ok = bool(re.search(r'ed2k.*connected', raw, re.I)) and not bool(re.search(r'ed2k.*not connected', raw, re.I))
            self.send_json({"kad_connected": kad_ok, "ed2k_connected": ed2k_ok, "raw": raw})

        elif path == "/api/kad/reconnect":
            out1 = run_amulecmd("connect kad")
            out2 = run_amulecmd("connect ed2k")
            cache_clear("status")
            self.send_json({"ok": True, "output": out1 + "\n" + out2})

        elif path == "/api/scan_now":
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
            log_name = qs.get("name", [""])[0]
            valid_logs = {"kad-monitor": "/var/log/kad-monitor.log", "source-scanner": "/var/log/source-scanner.log",
                          "server-update": "/var/log/server-update.log", "file-organizer": "/var/log/file-organizer.log",
                          "backup": "/var/log/backup.log"}
            if log_name in valid_logs:
                try:
                    with open(valid_logs[log_name], "r") as f:
                        lines = f.readlines()
                    self.send_json({"ok": True, "lines": lines[-100:]})
                except FileNotFoundError:
                    self.send_json({"ok": True, "lines": ["(aucun log encore)"]})
            else:
                self.send_json({"error": "Log inconnu"}, 400)

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

        if parsed.path == "/api/add_ed2k":
            link = str(data.get("link", "")).strip()
            if link and link.startswith("ed2k://"):
                output = run_amulecmd(f"add {link}")
                cache_clear("servers")
                self.send_json({"ok": True, "output": output})
            else:
                self.send_json({"error": "lien ed2k invalide"}, 400)
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
            new_settings = data
            if save_settings(new_settings):
                SERVER_SOURCES = get_server_sources_from_settings()
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "Impossible de sauvegarder"}, 500)

        elif parsed.path == "/api/settings/add_source":
            # Add a new server source
            settings = load_settings()
            if not settings:
                self.send_json({"error": "Settings introuvable"}, 500)
                return
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
            if not settings:
                self.send_json({"error": "Settings introuvable"}, 500)
                return
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
            if not settings:
                self.send_json({"error": "Settings introuvable"}, 500)
                return
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
