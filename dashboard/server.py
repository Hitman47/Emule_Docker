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
EC_HOST = "localhost"
EC_PORT = os.environ.get("AMULE_EC_PORT", "4712")
EC_PASSWORD = os.environ.get("AMULE_EC_PASSWORD", "")
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "4713"))
DASHBOARD_PWD = os.environ.get("DASHBOARD_PWD", "admin")
INCOMING_DIR = os.environ.get("INCOMING_DIR", "/incoming")
TEMP_DIR = os.environ.get("TEMP_DIR", "/temp")

STATIC_DIR = Path(__file__).parent / "static"

SERVER_SOURCES = {
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


def run_amulecmd(command, timeout=15):
    """Execute an amulecmd command and return cleaned output."""
    try:
        result = subprocess.run(
            ["amulecmd", "-h", EC_HOST, "-p", EC_PORT, "-P", EC_PASSWORD, "-c", command],
            capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout + result.stderr
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
    except subprocess.TimeoutExpired:
        return "ERROR: timeout"
    except Exception as e:
        return f"ERROR: {e}"


def parse_status(raw):
    info = {
        "connected_ed2k": False, "connected_kad": False,
        "download_speed": 0, "upload_speed": 0,
        "queue_length": 0, "shared_files": 0, "raw": raw
    }
    for line in raw.split("\n"):
        ll = line.lower().strip()
        if "ed2k" in ll and "connected" in ll and "not" not in ll:
            info["connected_ed2k"] = True
        if "kad" in ll and ("connected" in ll or "running" in ll) and "not" not in ll:
            info["connected_kad"] = True
        m = re.search(r'dl:\s*([\d.]+)\s*kb/s.*ul:\s*([\d.]+)\s*kb/s', ll)
        if m:
            info["download_speed"] = float(m.group(1))
            info["upload_speed"] = float(m.group(2))
        if "download" in ll and "kb/s" in ll:
            m2 = re.search(r'([\d.]+)\s*kb/s', ll)
            if m2: info["download_speed"] = float(m2.group(1))
        if "upload" in ll and "kb/s" in ll:
            m2 = re.search(r'([\d.]+)\s*kb/s', ll)
            if m2: info["upload_speed"] = float(m2.group(1))
    return info


def parse_downloads(raw):
    downloads = []
    current = None
    for line in raw.split("\n"):
        line = line.strip()
        if not line: continue
        if line.startswith(">") or re.match(r'^\d+\)', line):
            if current: downloads.append(current)
            current = {"name": re.sub(r'^[>\d\)\s]+', '', line).strip(),
                        "size": "", "progress": 0, "speed": 0, "sources": 0, "status": "unknown"}
        elif current:
            if "size" in line.lower():
                m = re.search(r'([\d.]+\s*[KMGT]?B)', line, re.I)
                if m: current["size"] = m.group(1)
            if "%" in line:
                m = re.search(r'([\d.]+)\s*%', line)
                if m: current["progress"] = float(m.group(1))
            if "source" in line.lower():
                m = re.search(r'(\d+)\s*source', line, re.I)
                if m: current["sources"] = int(m.group(1))
            if "kb/s" in line.lower():
                m = re.search(r'([\d.]+)\s*kb/s', line, re.I)
                if m: current["speed"] = float(m.group(1))
            for st in ["downloading","paused","waiting","completing","complete","hashing","error"]:
                if st in line.lower(): current["status"] = st
    if current: downloads.append(current)
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
            cached = cache_get("downloads", 3)
            if cached: self.send_json(cached); return
            data = parse_downloads(run_amulecmd("show dl"))
            cache_set("downloads", data)
            self.send_json(data)

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
            output = run_amulecmd("connect")
            cache_clear("status", "servers")
            self.send_json({"ok": True, "output": output})

        elif path == "/api/servers" or path == "/api/server_sources":
            raw = run_amulecmd("show servers")
            self.send_json({"sources": get_server_sources_payload(), "servers": parse_servers(raw), "raw": raw})

        elif path == "/api/stats":
            self.send_json({"raw": run_amulecmd("statistics")})

        elif path == "/api/organize":
            try:
                subprocess.run(["/home/amule/scripts/file-organizer.sh"], capture_output=True, timeout=30)
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/" or path == "/index.html":
            self.serve_file(STATIC_DIR / "index.html", "text/html")

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
