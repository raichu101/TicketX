
from __future__ import annotations

# --- TicketX (deployable) ---
# Changes vs your original:
# - Binds to 0.0.0.0 and reads PORT env var for cloud platforms.
# - Adds /manifest.json and /sw.js for basic PWA installability.
# - Adds /health for platform health checks.
# - Adds simple Caching headers for uploads.
# - Injects <link rel="manifest"> and meta tags in <head>.

import argparse, html, io, os, random, re, secrets, time, urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
import unittest

# ----------------------------- Config ----------------------------------------
UPLOAD_ROOT = Path("uploads"); UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
PER_PAGE = 10

# ----------------------------- Events ----------------------------------------
@dataclass(frozen=True)
class Event:
    id: str
    title: str
    category: str
    city: str
    venue: str
    date: datetime
    image: str
    fromPrice: float
    rating: float

MOCK_EVENTS: List[Event] = [
    Event("evt_001","Aurora Nights Tour","Concert","Los Angeles","Emberglen Arena",datetime(2025,9,12,19,30),"",49,4.7),
    Event("evt_002","San Fernando Phoenix vs. Bay City Waves","Sports","San Francisco","Harbor Dome",datetime(2025,10,5,17,0),"",35,4.4),
    Event("evt_003","The Hollow Crown of Emberglen (Live)","Theater","Los Angeles","Royal Stage",datetime(2025,9,28,20,0),"",59,4.9),
]

def currency(n: float) -> str: return f"${n:,.2f}"
def same_day(a: datetime, b: date) -> bool: return (a.year,a.month,a.day)==(b.year,b.month,b.day)

@dataclass(frozen=True)
class Seat:
    id: str
    section: str
    row: int
    col: int
    price: float
    available: bool

def generate_seats(seed: Optional[int] = None) -> List[Seat]:
    rnd = random.Random(seed) if seed is not None else random
    sections = [{"key":"A","rows":5,"cols":9,"base":120},{"key":"B","rows":5,"cols":9,"base":85},{"key":"C","rows":5,"cols":9,"base":60}]
    out: List[Seat] = []
    for s in sections:
        for r in range(1, s["rows"]+1):
            for c in range(1, s["cols"]+1):
                price = s["base"] - (r-1)*3 + (5 if c%3==0 else 0)
                out.append(Seat(f"{s['key']}-{r}-{c}", s["key"], r, c, float(price), rnd.random()>0.12))
    return out

def calc_totals(items: Sequence[Dict[str, float]], fee_rate: float = 0.18):
    subtotal = round(sum(i["price"] for i in items),2); fees = round(subtotal*fee_rate,2); total = round(subtotal+fees,2)
    return subtotal, fees, total

def svg_seat_map(seats: Sequence[Seat], selected: Optional[Set[str]] = None) -> str:
    selected = selected or set(); layout = {"A": (20, 30), "B": (20, 150), "C": (20, 270)}
    xgap, ygap, r = 18, 14, 6
    parts = ["<svg viewBox='0 0 400 360'>","<rect width='100%' height='100%' fill='white'/>",
             "<rect x='290' y='14' width='90' height='20' rx='6' fill='black'/>",
             "<text x='335' y='28' fill='white' text-anchor='middle'>STAGE</text>"]
    for s in seats:
        ox, oy = layout[s.section]; x = ox + (s.col-1)*xgap; y = oy + (s.row-1)*ygap
        fill = "#d1d5db" if s.available else "#cbd5e1"; 
        if s.id in selected: fill = "#111827"
        parts.append(f"<circle cx='{x}' cy='{y}' r='{r}' fill='{fill}' />")
    parts.append("</svg>"); return "".join(parts)

# ----------------------------- Social ----------------------------------------
@dataclass
class User:
    username: str
    password_hash: str
    bio: str = ""
    avatar_path: Optional[str] = None
    followers: Set[str] = field(default_factory=set)
    following: Set[str] = field(default_factory=set)

def demo_hash(pw: str) -> str: return str(abs(hash(("tx_salt", pw))))

@dataclass
class Comment: author: str; text: str; ts: float

@dataclass
class Post:
    id: str; author: str; text: str; ts: float
    image_url: Optional[str] = None
    likes: Set[str] = field(default_factory=set)
    comments: List[Comment] = field(default_factory=list)
    hashtags: Set[str] = field(default_factory=set)
    mentions: Set[str] = field(default_factory=set)

class SocialStore:
    TAG_RE = re.compile(r"(?i)(?<!\w)#([a-z0-9_]{1,30})"); AT_RE = re.compile(r"(?i)(?<!\w)@([a-z0-9_]{1,30})")
    def __init__(self):
        self.users: Dict[str, User] = {}; self.sessions: Dict[str, str] = {}; self.csrf_tokens: Dict[str, str] = {}
        self.posts: Dict[str, Post] = {}; self.user_posts: Dict[str, List[str]] = {}
    def create_user(self, username: str, pw: str) -> bool:
        if not username or not pw or username in self.users: return False
        self.users[username] = User(username=username, password_hash=demo_hash(pw)); self.user_posts.setdefault(username, []); return True
    def update_profile(self, username: str, bio: Optional[str]=None, avatar_path: Optional[str]=None) -> bool:
        u = self.users.get(username); 
        if not u: return False
        if bio is not None: u.bio = bio[:200]
        if avatar_path is not None: u.avatar_path = avatar_path
        return True
    def verify_login(self, username: str, pw: str) -> bool: 
        u = self.users.get(username); return bool(u and u.password_hash == demo_hash(pw))
    def new_session(self, username: str) -> str:
        sid = secrets.token_hex(16); self.sessions[sid] = username; self.csrf_tokens[sid] = secrets.token_hex(16); return sid
    def username_for_sid(self, sid: Optional[str]) -> Optional[str]: return self.sessions.get(sid) if sid else None
    def csrf_for_sid(self, sid: Optional[str]) -> Optional[str]: return self.csrf_tokens.get(sid) if sid else None
    def destroy_session(self, sid: Optional[str]) -> None:
        if not sid: return
        self.sessions.pop(sid, None); self.csrf_tokens.pop(sid, None)
    def follow(self, follower: str, target: str) -> bool:
        if follower == target or follower not in self.users or target not in self.users: return False
        self.users[follower].following.add(target); self.users[target].followers.add(follower); return True
    def unfollow(self, follower: str, target: str) -> bool:
        if follower not in self.users or target not in self.users: return False
        self.users[follower].following.discard(target); self.users[target].followers.discard(follower); return True
    @classmethod
    def extract_tags_mentions(cls, text: str):
        tags = {m.group(1).lower() for m in cls.TAG_RE.finditer(text or "")}
        ats = {m.group(1).lower() for m in cls.AT_RE.finditer(text or "")}
        return tags, ats
    def create_post(self, author: str, text: str, image_url: Optional[str] = None) -> Optional[str]:
        if author not in self.users or not (text or image_url): return None
        tags, ats = self.extract_tags_mentions(text)
        pid = f"p_{int(time.time()*1000)}_{secrets.token_hex(3)}"
        self.posts[pid] = Post(id=pid, author=author, text=(text or "")[:280], ts=time.time(), image_url=(image_url or None), hashtags=tags, mentions=ats)
        self.user_posts.setdefault(author, []).insert(0, pid); return pid
    def toggle_like(self, pid: str, username: str) -> bool:
        p = self.posts.get(pid); 
        if not p or username not in self.users: return False
        (p.likes.remove(username) if username in p.likes else p.likes.add(username)); return True
    def add_comment(self, pid: str, author: str, text: str) -> bool:
        p = self.posts.get(pid); 
        if not p or author not in self.users or not text: return False
        p.comments.append(Comment(author=author, text=text[:200], ts=time.time())); return True
    def feed_for(self, username: str) -> List[Post]:
        if username not in self.users: return []
        authors = {username} | set(self.users[username].following); ids: List[str] = []
        for a in authors: ids.extend(self.user_posts.get(a, []))
        posts = [self.posts[i] for i in ids if i in self.posts]; posts.sort(key=lambda p: p.ts, reverse=True); return posts
    def global_feed(self) -> List[Post]:
        posts = list(self.posts.values()); posts.sort(key=lambda p: p.ts, reverse=True); return posts
    def by_hashtag(self, tag: str) -> List[Post]:
        tag = (tag or "").lower(); posts = [p for p in self.posts.values() if tag in p.hashtags]; posts.sort(key=lambda p: p.ts, reverse=True); return posts
    def mentioning(self, name: str) -> List[Post]:
        name = (name or "").lower(); posts = [p for p in self.posts.values() if name in p.mentions]; posts.sort(key=lambda p: p.ts, reverse=True); return posts
    def trending(self) -> List[Post]:
        now = time.time()
        def score(p: Post) -> float:
            hours = max(1.0, (now - p.ts)/3600.0); return (len(p.likes)*3 + len(p.comments)*2 + 1) / (hours**0.7)
        return sorted(self.posts.values(), key=score, reverse=True)

# ----------------------------- Pagination ------------------------------------
def paginate(items: Sequence, page: int, per_page: int = PER_PAGE):
    total_pages = max(1, (len(items) + per_page - 1) // per_page); page = max(1, min(page, total_pages))
    start = (page - 1) * per_page; end = start + per_page; return list(items[start:end]), total_pages

# ----------------------------- App State -------------------------------------
class App:
    def __init__(self) -> None:
        self.events = MOCK_EVENTS[:]; self.seats_cache: Dict[str, List[Seat]] = {}; self.cart: List[Dict[str, object]] = []
        self.social = SocialStore()
    def ensure_seats(self, eid: str) -> List[Seat]:
        if eid not in self.seats_cache: self.seats_cache[eid] = generate_seats()
        return self.seats_cache[eid]
    def selected_ids(self) -> Set[str]: return {i["seatId"] for i in self.cart}

# ----------------------------- Web Handler -----------------------------------
class WebHandler(BaseHTTPRequestHandler):
    app = App()

    # helpers
    def parse(self):
        path, _, qs = self.path.partition("?"); params = urllib.parse.parse_qs(qs); return path, params
    def body_params(self) -> Dict[str, List[str]]:
        length = int(self.headers.get('Content-Length','0') or '0'); data = self.rfile.read(length) if length>0 else b''; ctype = self.headers.get('Content-Type','')
        if ctype.startswith('multipart/form-data'): return {}
        return urllib.parse.parse_qs(data.decode('utf-8'))
    def read_multipart(self):
        length = int(self.headers.get('Content-Length','0') or '0'); data = self.rfile.read(length); ctype = self.headers.get('Content-Type','')
        m = re.search(r'boundary=([^;]+)', ctype); 
        if not m: return {}, {}
        boundary = ('--' + m.group(1)).encode('utf-8'); parts = data.split(boundary)
        fields: Dict[str,str] = {}; files: Dict[str,Tuple[str,bytes]] = {}
        for part in parts:
            if not part or part in (b'--\r\n', b'--'): continue
            header,_,body = part.partition(b"\r\n\r\n")
            if not body: continue
            body = body.rsplit(b"\r\n",1)[0]; headers = header.decode('utf-8','ignore')
            disp = re.search(r'form-data;\s*name="([^"]+)"(?:;\s*filename="([^"]*)")?', headers)
            if not disp: continue
            name = disp.group(1); filename = disp.group(2)
            if filename is not None and filename != "": files[name] = (filename, body)
            else: fields[name] = body.decode('utf-8','ignore')
        return fields, files
    def sid(self) -> Optional[str]:
        raw = self.headers.get("Cookie"); 
        if not raw: return None
        c = cookies.SimpleCookie(); c.load(raw); m = c.get("sid"); return m.value if m else None
    def username(self) -> Optional[str]: return self.app.social.username_for_sid(self.sid())
    def csrf_token(self) -> Optional[str]: return self.app.social.csrf_for_sid(self.sid())
    def set_login_cookie(self, username: str):
        sid = self.app.social.new_session(username); c = cookies.SimpleCookie(); c['sid'] = sid; c['sid']['path'] = '/'
        self.send_header('Set-Cookie', c.output(header='').strip())
    def clear_login_cookie(self):
        s = self.sid(); self.app.social.destroy_session(s)
        c = cookies.SimpleCookie(); c['sid']=''; c['sid']['path']='/'; c['sid']['expires']='Thu, 01 Jan 1970 00:00:00 GMT'
        self.send_header('Set-Cookie', c.output(header='').strip())
    def send_html(self, body: str, code: int = 200):
        data = body.encode('utf-8'); self.send_response(code)
        self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def send_json(self, body: str, code: int = 200):
        data = body.encode('utf-8'); self.send_response(code)
        self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def redirect(self, url: str):
        self.send_response(303); self.send_header('Location', url); self.end_headers()
    def check_csrf(self, token: Optional[str]) -> bool: return token and token == self.csrf_token()
    def page_links(self, base: str, page: int, total_pages: int) -> str:
        prev_link = f"<a href='{base}?page={page-1}'>Prev</a>" if page>1 else ""; next_link = f"<a href='{base}?page={page+1}'>Next</a>" if page<total_pages else ""
        return f"<div>{prev_link} {page}/{total_pages} {next_link}</div>"

    # ------------------ GET ------------------
    def do_GET(self):
        path, params = self.parse(); u = self.username()
        # Health & PWA assets
        if path == '/health': self.send_json('{"ok":true}'); return
        if path == '/manifest.json':
            manifest = {
                "name":"TicketX","short_name":"TicketX","start_url":"/","display":"standalone",
                "background_color":"#ffffff","theme_color":"#111827",
                "icons":[
                    {"src":"/uploads/icon-192.png","sizes":"192x192","type":"image/png"},
                    {"src":"/uploads/icon-512.png","sizes":"512x512","type":"image/png"}
                ]
            }
            self.send_json(json.dumps(manifest)); return
        if path == '/sw.js':
            js = """
                self.addEventListener('install', e => { self.skipWaiting(); });
                self.addEventListener('activate', e => { e.waitUntil(clients.claim()); });
                self.addEventListener('fetch', e => { return; });
            """.strip()
            data = js.encode('utf-8'); self.send_response(200)
            self.send_header('Content-Type','application/javascript'); self.send_header('Content-Length',str(len(data)))
            self.end_headers(); self.wfile.write(data); return

        if path == '/':
            self.send_html(self.render_index(u)); return
        if path == '/explore':
            page = int(params.get('page',['1'])[0] or '1'); self.send_html(self.render_feed(u, mode='global', page=page)); return
        if path == '/trending':
            page = int(params.get('page',['1'])[0] or '1'); self.send_html(self.render_trending(u, page=page)); return
        if path == '/feed':
            if not u: self.redirect('/login'); return
            page = int(params.get('page',['1'])[0] or '1'); self.send_html(self.render_feed(u, mode='following', page=page)); return
        if path == '/login': self.send_html(self.render_login_form()); return
        if path == '/signup': self.send_html(self.render_signup_form()); return
        if path == '/settings':
            if not u: self.redirect('/login'); return
            self.send_html(self.render_settings(u)); return
        if path == '/u':
            name = params.get('name',[None])[0]
            if not name or name not in self.app.social.users: self.send_html(self.render_msg('User not found'),404); return
            page = int(params.get('page',['1'])[0] or '1'); self.send_html(self.render_profile(name, u, page)); return
        if path == '/event':
            eid = params.get('id',[None])[0]
            if not eid: self.send_html('Missing id',400); return
            self.send_html(self.render_event(eid, u)); return
        if path == '/cart': self.send_html(self.render_cart(u)); return
        if path == '/tag':
            tag = params.get('name',[''])[0]; page = int(params.get('page',['1'])[0] or '1')
            self.send_html(self.render_tag(u, tag, page)); return
        if path == '/at':
            name = params.get('name',[''])[0]; page = int(params.get('page',['1'])[0] or '1')
            self.send_html(self.render_at(u, name, page)); return
        if self.path.startswith("/uploads/"): return self.serve_upload()
        self.send_html('Not found',404)

    def serve_upload(self):
        from urllib.parse import unquote
        rel = unquote(self.path.lstrip("/")); p = Path(rel)
        if not p.exists() or not p.is_file() or not p.resolve().as_posix().startswith(UPLOAD_ROOT.resolve().as_posix()):
            self.send_error(404, "File not found"); return
        ext = p.suffix.lower()
        ctype = {".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".gif":"image/gif",".webp":"image/webp"}.get(ext,"application/octet-stream")
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(p.stat().st_size))
        self.send_header("Cache-Control","public, max-age=86400")
        self.end_headers(); 
        with open(p,"rb") as f: self.wfile.write(f.read())

    # ------------------ POST ------------------
    def do_POST(self):
        path, _ = self.parse(); u = self.username(); form = self.body_params()
        token = (form.get('csrf',[None])[0]) if form else None
        if path == '/login':
            name = (form.get('u',[''])[0]).strip(); pw = (form.get('p',[''])[0]).strip()
            if self.app.social.verify_login(name, pw): self.set_login_cookie(name); self.redirect('/feed'); return
            self.send_html(self.render_msg('Login failed.'),401); return
        if path == '/signup':
            name = (form.get('u',[''])[0]).strip(); pw = (form.get('p',[''])[0]).strip()
            if self.app.social.create_user(name, pw): self.set_login_cookie(name); self.redirect('/settings'); return
            self.send_html(self.render_msg('Signup failed.'),400); return
        if path == '/logout': self.clear_login_cookie(); self.redirect('/'); return

        # Multipart avatar upload
        if path == '/upload_avatar':
            if not u: self.redirect('/login'); return
            ctype = self.headers.get('Content-Type','')
            if not ctype.startswith('multipart/form-data'): self.send_html(self.render_msg('Bad content type'),400); return
            fields, files = self.read_multipart()
            if (fields.get('csrf') or None) != self.csrf_token(): self.send_html(self.render_msg('Invalid CSRF token'),400); return
            if 'avatar' not in files: self.redirect('/settings'); return
            filename, content = files['avatar']
            if len(content) > 2*1024*1024: self.send_html(self.render_msg('File too large (2MB max).'),400); return
            ext = os.path.splitext(filename)[1].lower() or '.bin'
            safe = re.sub(r'[^a-zA-Z0-9_.-]','_', f"{u}_avatar{ext}"); path_out = UPLOAD_ROOT / safe
            with open(path_out,'wb') as f: f.write(content)
            self.app.social.update_profile(u, avatar_path=str(path_out)); self.redirect('/settings'); return

        # CSRF paths
        if not self.check_csrf(token): self.send_html(self.render_msg('Invalid CSRF token'), 400); return
        if path == '/settings':
            if not u: self.redirect('/login'); return
            bio = (form.get('bio',[''])[0])[:200]; self.app.social.update_profile(u, bio=bio); self.redirect('/settings'); return
        if path == '/follow':
            if not u: self.redirect('/login'); return
            target = (form.get('u',[''])[0]); self.app.social.follow(u, target); self.redirect(f"/u?name={urllib.parse.quote(target)}"); return
        if path == '/unfollow':
            if not u: self.redirect('/login'); return
            target = (form.get('u',[''])[0]); self.app.social.unfollow(u, target); self.redirect(f"/u?name={urllib.parse.quote(target)}"); return
        if path == '/post':
            if not u: self.redirect('/login'); return
            text = (form.get('text',[''])[0])[:280]; img = (form.get('image_url',[''])[0]).strip() or None
            self.app.social.create_post(u, text, img); self.redirect('/feed' if self.headers.get('Referer','').endswith('/feed') else '/explore'); return
        if path == '/like':
            if not u: self.redirect('/login'); return
            pid = (form.get('pid',[''])[0]); self.app.social.toggle_like(pid, u); self.redirect(self.headers.get('Referer','/feed')); return
        if path == '/comment':
            if not u: self.redirect('/login'); return
            pid = (form.get('pid',[''])[0]); text = (form.get('text',[''])[0]); self.app.social.add_comment(pid, u, text); self.redirect(self.headers.get('Referer','/feed')); return
        if path == '/add':
            eid = (form.get('eid',[''])[0]); sid = (form.get('sid',[''])[0])
            ev = next((e for e in self.app.events if e.id == eid), None); seat = next((s for s in self.app.ensure_seats(eid) if s.id == sid), None)
            if ev and seat and seat.available: self.app.cart.append({"eventId": eid, "seatId": sid, "title": ev.title, "price": seat.price})
            self.redirect(f"/event?id={eid}"); return
        self.send_html('Not found',404)

    # ------------------ HTML ------------------
    def page(self, title: str, body_html: str, user: Optional[str] = None) -> str:
        auth = (f"<span>Signed in as <a href='/u?name={html.escape(user)}'>{html.escape(user)}</a></span> "
                f"<form style='display:inline' action='/logout' method='post'><button>logout</button></form>"
                if user else "<a href='/login'>Sign in</a> or <a href='/signup'>Sign up</a>")
        nav = ("<nav><a href='/'>Events</a> · <a href='/feed'>Following</a> · <a href='/explore'>Global</a> · "
               "<a href='/trending'>Trending</a> · <a href='/cart'>Cart</a></nav>")
        head = (
            f"<head><title>{html.escape(title)}</title>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'/>"
            "<link rel='manifest' href='/manifest.json'/>"
            "<meta name='theme-color' content='#111827'/>"
            "<script>if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js')}</script>"
            "</head>"
        )
        return f"<html>{head}<body><header>{nav}<div style='float:right'>{auth}</div><hr/></header>{body_html}</body></html>"

    def csrf_input(self) -> str:
        token = self.csrf_token() or ''; return f"<input type='hidden' name='csrf' value='{html.escape(token)}'/>"

    def render_index(self, user: Optional[str]) -> str:
        items = [f\"<li><a href='/event?id={e.id}'>{html.escape(e.title)}</a> - {currency(e.fromPrice)}</li>\" for e in self.app.events]
        body = (\"<h1>Events</h1><ul>\"+\"\".join(items)+\"</ul>\" \"<p>Sign in to share events and follow friends. Try #tags and @mentions in posts.</p>\")
        return self.page(\"Events\", body, user)

    def render_event(self, eid: str, user: Optional[str]) -> str:
        e = next((x for x in self.app.events if x.id == eid), None)
        if not e: return self.page(\"Event\", \"<p>Event not found</p>\", user)
        seats = self.app.ensure_seats(eid); svg = svg_seat_map(seats, self.app.selected_ids())
        seat_items = []
        for s in seats[:20]:
            if s.available:
                seat_items.append(
                    f\"<li>{s.id} - {currency(s.price)}\"
                    f\"<form style='display:inline' method='post' action='/add'>{self.csrf_input()}\"
                    f\"<input type='hidden' name='eid' value='{eid}'/>\"
                    f\"<input type='hidden' name='sid' value='{s.id}'/>\"
                    f\"<button>Add</button></form></li>\"
                )
            else:
                seat_items.append(f\"<li>{s.id} - {currency(s.price)} (sold)</li>\")
        share = (\"<form method='post' action='/post'>\" f\"{self.csrf_input()}<input name='text' maxlength='280' placeholder='Say something… include #tags and @friends'/>\" f\"<input name='image_url' placeholder='Image URL (optional)'/>\" f\"<button>Share</button></form>\" if user else \"\")
        body = (f\"<h1>{html.escape(e.title)}</h1>\" f\"<p>{html.escape(e.venue)} · {html.escape(e.city)} · {e.date.strftime('%a, %b %d • %I:%M %p')}</p>\" f\"{svg}<ul>{''.join(seat_items)}</ul>{share}<br/><a href='/cart'>Cart ({len(self.app.cart)})</a>\")
        return self.page(e.title, body, user)

    def render_cart(self, user: Optional[str]) -> str:
        sub, fees, total = calc_totals(self.app.cart); items = \"\".join(f\"<li>{i['title']} {i['seatId']} {currency(i['price'])}</li>\" for i in self.app.cart)
        body = f\"<h1>Cart</h1><ul>{items or '<li>(empty)</li>'}</ul><p>Total: {currency(total)}</p><a href='/'>Home</a>\"
        return self.page(\"Cart\", body, user)

    def render_feed(self, user: Optional[str], mode: str, page: int = 1) -> str:
        all_posts = self.app.social.global_feed() if mode=='global' else self.app.social.feed_for(user or '')
        posts, total_pages = paginate(all_posts, page, PER_PAGE)
        items = [self.render_post_li(p, user) for p in posts]; form = self.post_form(user)
        tabs = \"<div><a href='/feed'>Following</a> | <a href='/explore'>Global</a> | <a href='/trending'>Trending</a></div>\"
        body = f\"<h1>{'Global' if mode=='global' else 'Following'} Feed</h1>{tabs}{form}<ul>{''.join(items) or '<li>No posts yet.</li>'}</ul>{self.page_links('/explore' if mode=='global' else '/feed', page, total_pages)}\"
        return self.page(f\"{'Global' if mode=='global' else 'Following'} Feed\", body, user)

    def render_trending(self, user: Optional[str], page: int = 1) -> str:
        all_posts = self.app.social.trending(); posts, total_pages = paginate(all_posts, page, PER_PAGE)
        items = [self.render_post_li(p, user) for p in posts]
        body = f\"<h1>Trending</h1><ul>{''.join(items) or '<li>No posts yet.</li>'}</ul>{self.page_links('/trending', page, total_pages)}\"
        return self.page(\"Trending\", body, user)

    def render_tag(self, user: Optional[str], tag: str, page: int = 1) -> str:
        all_posts = self.app.social.by_hashtag(tag); posts, total_pages = paginate(all_posts, page, PER_PAGE)
        items = [self.render_post_li(p, user) for p in posts]
        body = f\"<h1>#{html.escape(tag)}</h1><ul>{''.join(items) or '<li>No posts yet.</li>'}</ul>{self.page_links(f'/tag?name={urllib.parse.quote(tag)}', page, total_pages)}\"
        return self.page(f\"#{tag}\", body, user)

    def render_at(self, user: Optional[str], name: str, page: int = 1) -> str:
        all_posts = self.app.social.mentioning(name); posts, total_pages = paginate(all_posts, page, PER_PAGE)
        items = [self.render_post_li(p, user) for p in posts]
        body = f\"<h1>@{html.escape(name)}</h1><ul>{''.join(items) or '<li>No mentions yet.</li>'}</ul>{self.page_links(f'/at?name={urllib.parse.quote(name)}', page, total_pages)}\"
        return self.page(f\"@{name}\", body, user)

    def post_form(self, user: Optional[str]) -> str:
        if not user: return \"\"
        return (\"<form action='/post' method='post'>\" f\"{self.csrf_input()}\" \"<input name='text' maxlength='280' placeholder='Share something… use #tags and @friends'/>\" \"<input name='image_url' placeholder='Image URL (optional)'/>\" \"<button type='submit'>Post</button>\" \"</form>\")

    def render_post_li(self, p: Post, user: Optional[str]) -> str:
        like_form = (f\"<form style='display:inline' action='/like' method='post'>{self.csrf_input()}<input type='hidden' name='pid' value='{p.id}'/><button>♥ {len(p.likes)}</button></form>\" if user else f\"♥ {len(p.likes)}\")
        img = f\"<div><img src='{html.escape(p.image_url)}' alt='' style='max-width:320px'/></div>\" if p.image_url else \"\"
        def linkify(text: str) -> str:
            text = re.sub(SocialStore.TAG_RE, lambda m: f\"<a href='/tag?name={m.group(1).lower()}'>#{m.group(1)}</a>\", text)
            text = re.sub(SocialStore.AT_RE, lambda m: f\"<a href='/u?name={m.group(1)}'>@{m.group(1)}</a>\", text)
            return html.escape(text, quote=False).replace('&lt;a ', '<a ').replace('</a&gt;', '</a>')
        comments = \"\".join(f\"<li><b>{html.escape(c.author)}</b>: {html.escape(c.text)}</li>\" for c in p.comments)
        cform = (f\"<form action='/comment' method='post'>{self.csrf_input()}<input type='hidden' name='pid' value='{p.id}'/><input name='text' maxlength='200' placeholder='Comment…'/><button>Reply</button></form>\" if user else \"\")
        return (f\"<li><b><a href='/u?name={p.author}'>{html.escape(p.author)}</a></b>: {linkify(p.text)} \" f\"<small>{time.strftime('%b %d %H:%M', time.localtime(p.ts))}</small> — {like_form}{img}<ul>{comments}</ul>{cform}</li>\")

    def render_settings(self, user: str) -> str:
        u = self.app.social.users[user]
        avatar_tag = f\"<img src='file://{html.escape(u.avatar_path)}' alt='avatar' style='max-width:120px'/>\" if u.avatar_path else \"(no avatar)\"
        bio = html.escape(u.bio)
        body = (\"<h1>Settings</h1>\" f\"<p>Avatar: {avatar_tag}</p>\" \"<form action='/upload_avatar' method='post' enctype='multipart/form-data'>\" f\"{self.csrf_input()}<input type='file' name='avatar' accept='image/*'/> <button>Upload</button>\" \"</form>\" \"<form action='/settings' method='post'>\" f\"{self.csrf_input()}<textarea name='bio' rows='3' cols='50' placeholder='Your bio (200 chars max)'>\"+bio+\"</textarea>\" \"<br/><button>Save</button></form>\")
        return self.page(\"Settings\", body, user)

    def render_profile(self, name: str, viewer: Optional[str], page: int = 1) -> str:
        u = self.app.social.users[name]; is_following = bool(viewer and name in self.app.social.users.get(viewer, User('', '')).following)
        btn = \"\"
        if viewer and viewer != name:
            action = 'unfollow' if is_following else 'follow'
            btn = (f\"<form style='display:inline' action='/{action}' method='post'>{self.csrf_input()}<input type='hidden' name='u' value='{name}'/><button>{action}</button></form>\")
        posts_all = [self.app.social.posts[pid] for pid in self.app.social.user_posts.get(name, [])]
        posts, total_pages = paginate(posts_all, page, PER_PAGE); items = \"\".join(self.render_post_li(p, viewer) for p in posts)
        avatar_tag = f\"<img src='file://{html.escape(u.avatar_path)}' alt='avatar' style='max-width:120px'/>\" if u.avatar_path else \"\"
        body = (f\"<h1>@{html.escape(name)}</h1>\" f\"{avatar_tag}<p>{html.escape(u.bio) or ''}</p>\" f\"<p>{len(u.followers)} followers · {len(u.following)} following</p>\" f\"{btn}<h3>Posts</h3><ul>{items or '<li>No posts yet.</li>'}</ul>\" f\"{self.page_links(f'/u?name={urllib.parse.quote(name)}', page, total_pages)}\")
        return self.page(f\"@{name}\", body, viewer)

    def render_login_form(self) -> str:
        body = (\"<h1>Sign in</h1>\" \"<form action='/login' method='post'>\" f\"<input name='u' placeholder='username'/>\" f\"<input name='p' placeholder='password'/>\" f\"<button>Login</button>\" \"</form>\")
        return self.page(\"Login\", body, None)

    def render_signup_form(self) -> str:
        body = (\"<h1>Sign up</h1>\" \"<form action='/signup' method='post'>\" \"<input name='u' placeholder='username'/>\" \"<input name='p' placeholder='password'/>\" \"<button>Create account</button>\" \"</form>\")
        return self.page(\"Sign up\", body, None)

    def render_msg(self, msg: str) -> str: return self.page(\"Message\", f\"<p>{html.escape(msg)}</p>\")

# ------------------------------ Tests ----------------------------------------
class TicketXTests(unittest.TestCase):
    def test_currency(self): self.assertEqual(currency(0), "$0.00")
    def test_generate(self): self.assertEqual(len(generate_seats(seed=1)), 135)

def run_tests() -> int:
    suite = unittest.TestSuite(); suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TicketXTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite); return 0 if result.wasSuccessful() else 1

# ------------------------------ Main -----------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="TicketX — social demo (deployable)")
    p.add_argument("--test", action="store_true")
    p.add_argument("--web", action="store_true")
    args = p.parse_args(argv)
    if args.test: return run_tests()
    if args.web:
        port = int(os.environ.get("PORT", "8000"))
        server = HTTPServer(("0.0.0.0", port), WebHandler)
        print(f"Serving on http://0.0.0.0:{port}  (Ctrl+C to stop)")
        try: server.serve_forever()
        except KeyboardInterrupt: print("\nShutting down…"); server.server_close()
        return 0
    print("Nothing to do. Use --web or --test."); return 0

if __name__ == "__main__":
    code = main()
    if code: raise SystemExit(code)