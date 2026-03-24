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
import mimetypes
from urllib.parse import urlparse, parse_qs
from pathlib import Path

# ── Config ──
EC_HOST = os.environ.get("AMULE_EC_HOST", os.environ.get("EC_HOST", "localhost"))
EC_PORT = os.environ.get("AMULE_EC_PORT", os.environ.get("EC_PORT", "4712"))
EC_PASSWORD = os.environ.get("AMULE_EC_PASSWORD", os.environ.get("EC_PASSWORD", ""))
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8078"))
DASHBOARD_PWD = os.environ.get("DASHBOARD_PWD", "admin")
INCOMING_DIR = os.environ.get("INCOMING_DIR", "/incoming")
TEMP_DIR = os.environ.get("TEMP_DIR", "/temp")

STATIC_DIR = Path(__file__).parent / "static"

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
                self.send_json({"ok": True, "output": run_amulecmd(f"add {link}")})
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
            self.send_json({"ok": True, "output": run_amulecmd("connect")})

        elif path == "/api/servers":
            self.send_json({"raw": run_amulecmd("show servers")})

        elif path == "/api/stats":
            self.send_json({"raw": run_amulecmd("statistics")})

        elif path == "/api/organize":
            try:
                subprocess.run(["/opt/scripts/file-organizer.sh"], capture_output=True, timeout=30)
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/" or path == "/index.html":
            self.serve_static("index.html")

        elif path == "/manifest.json":
            self.serve_static("manifest.json")

        elif path == "/apple-touch-icon.png":
            self.serve_static("icons/apple-touch-icon.png")

        elif path == "/favicon-32x32.png":
            self.serve_static("icons/favicon-32x32.png")

        elif path == "/favicon.ico":
            self.serve_static("icons/favicon.ico")

        elif path.startswith("/icons/"):
            self.serve_static(path.lstrip("/"))

        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if not self.check_auth():
            self.send_json({"error": "unauthorized"}, 401); return
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode() if length else ""
        if parsed.path == "/api/add_ed2k":
            try:
                data = json.loads(body) if body else {}
                link = data.get("link", "")
                if link and link.startswith("ed2k://"):
                    self.send_json({"ok": True, "output": run_amulecmd(f"add {link}")})
                else: self.send_json({"error": "lien ed2k invalide"}, 400)
            except json.JSONDecodeError:
                self.send_json({"error": "JSON invalide"}, 400)
        else: self.send_json({"error": "not found"}, 404)

    def serve_file(self, filepath, ctype=None):
        try:
            content = Path(filepath).read_bytes()
            guessed_type = ctype or mimetypes.guess_type(str(filepath))[0] or 'application/octet-stream'
            self.send_response(200)
            if guessed_type.startswith('text/') or guessed_type in {'application/json', 'application/manifest+json', 'application/javascript', 'image/svg+xml'}:
                self.send_header('Content-Type', f'{guessed_type}; charset=utf-8')
            else:
                self.send_header('Content-Type', guessed_type)
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404); self.end_headers()

    def serve_static(self, relative_path):
        target = (STATIC_DIR / relative_path).resolve()
        static_root = STATIC_DIR.resolve()
        if static_root not in target.parents and target != static_root:
            self.send_response(403)
            self.end_headers()
            return
        self.serve_file(target)

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
