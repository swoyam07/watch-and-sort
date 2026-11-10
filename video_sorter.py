#!/usr/bin/env python3
"""Video Sorter — watch videos from a folder, then move them into category folders or delete them.
Run:  python3 video_sorter.py   then open http://127.0.0.1:8765
"""
import json, os, shutil, subprocess, mimetypes, webbrowser, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = 8765
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".ogv", ".avi"}
CONFIG = os.path.expanduser("~/.config/video-sorter.json")


def load_config():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except Exception:
        return {"source": "", "destinations": []}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)


def list_videos(path):
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except Exception:
        return []
    out = []
    for n in names:
        p = os.path.join(path, n)
        if os.path.isfile(p) and os.path.splitext(n)[1].lower() in VIDEO_EXT:
            out.append({"name": n, "path": p, "size": os.path.getsize(p)})
    return out


def unique_target(dest_dir, name):
    base, ext = os.path.splitext(name)
    target, i = os.path.join(dest_dir, name), 1
    while os.path.exists(target):
        target = os.path.join(dest_dir, f"{base} ({i}){ext}")
        i += 1
    return target


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/":
            data = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif u.path == "/api/config":
            self.send_json(load_config())
        elif u.path == "/api/browse":
            path = os.path.abspath(os.path.expanduser(q.get("path") or "~"))
            if not os.path.isdir(path):
                return self.send_json({"error": "Not a folder"}, 400)
            dirs = []
            try:
                for n in sorted(os.listdir(path), key=str.lower):
                    p = os.path.join(path, n)
                    if not n.startswith(".") and os.path.isdir(p):
                        dirs.append({"name": n, "path": p})
            except PermissionError:
                pass
            home = os.path.expanduser("~")
            places = [{"name": "Home", "path": home}] + [
                {"name": d, "path": os.path.join(home, d)}
                for d in ("Videos", "Downloads", "Desktop", "Documents")
                if os.path.isdir(os.path.join(home, d))
            ]
            self.send_json({"path": path, "parent": os.path.dirname(path) if path != "/" else None,
                            "dirs": dirs, "videoCount": len(list_videos(path)), "places": places})
        elif u.path == "/api/videos":
            self.send_json(list_videos(q.get("path", "")))
        elif u.path == "/video":
            self.stream(q.get("path", ""))
        else:
            self.send_error(404)

    def stream(self, path):
        if not os.path.isfile(path):
            return self.send_error(404)
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "video/mp4"
        if path.lower().endswith(".mkv"):
            ctype = "video/webm"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            s, _, e = rng[6:].partition("-")
            start = int(s) if s else 0
            end = int(e) if e else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        length = end - start + 1
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                while length > 0:
                    chunk = f.read(min(1 << 20, length))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    length -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        u = urlparse(self.path)
        try:
            b = self.body()
            if u.path == "/api/config":
                save_config(b)
                return self.send_json({"ok": True})
            if u.path == "/api/move":
                src, dest = b["src"], b["dest"]
                if not os.path.isfile(src) or not os.path.isdir(dest):
                    return self.send_json({"error": "Source or destination missing"}, 400)
                target = unique_target(dest, os.path.basename(src))
                shutil.move(src, target)
                return self.send_json({"ok": True, "target": target})
            if u.path == "/api/delete":
                src = b["src"]
                if not os.path.isfile(src):
                    return self.send_json({"error": "File missing"}, 400)
                try:  # send to Trash so it can be recovered
                    subprocess.run(["gio", "trash", src], check=True, capture_output=True)
                    return self.send_json({"ok": True, "trashed": True})
                except Exception:
                    os.remove(src)
                    return self.send_json({"ok": True, "trashed": False})
            self.send_error(404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Video Sorter</title>
<style>
:root{--bg:#0e1016;--panel:#171a23;--panel2:#1f2330;--line:#2b3042;--text:#e9ecf3;--muted:#8d94a8;
--accent:#7c5cff;--accent2:#22c3a6;--danger:#ff5470;--warn:#ffb547;--r:16px}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{background:radial-gradient(1200px 600px at 10% -10%,#251d4d55,transparent),var(--bg);color:var(--text);
font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
button{font:inherit;color:inherit;cursor:pointer;border:0}
.hidden{display:none!important}
header{display:flex;align-items:center;gap:12px;padding:18px 28px;border-bottom:1px solid var(--line)}
.logo{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:grid;place-items:center;font-weight:800}
header h1{font-size:18px;margin:0}header .sub{color:var(--muted);font-size:13px}
.wrap{max-width:1100px;margin:0 auto;padding:28px 20px}
.steps{display:grid;grid-template-columns:1fr 1fr;gap:22px}
@media(max-width:820px){.steps{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:22px}
.card h2{margin:0 0 4px;font-size:17px;display:flex;align-items:center;gap:10px}
.num{width:26px;height:26px;border-radius:50%;background:var(--panel2);border:1px solid var(--line);display:grid;place-items:center;font-size:13px}
.num.done{background:var(--accent2);color:#06231d;border-color:transparent}
.card p.hint{color:var(--muted);margin:0 0 16px;font-size:13.5px}
.drop{border:2px dashed var(--line);border-radius:14px;padding:26px;text-align:center;transition:.15s;background:#ffffff03}
.drop:hover{border-color:var(--accent)}
.big{font-size:34px}
.btn{background:var(--panel2);border:1px solid var(--line);padding:10px 16px;border-radius:12px;transition:.15s}
.btn:hover{border-color:var(--accent);background:#272c3c}
.btn.primary{background:var(--accent);border-color:transparent;font-weight:600}
.btn.primary:hover{filter:brightness(1.12)}
.btn.danger{background:#3a1520;border-color:#6b2333;color:#ffb3c0}
.btn:disabled{opacity:.4;cursor:not-allowed}
.chosen{display:flex;gap:14px;align-items:center;background:var(--panel2);border:1px solid var(--line);border-radius:14px;padding:14px}
.chosen .ic{font-size:28px}.chosen .nm{font-weight:600}.path{color:var(--muted);font-size:12.5px;word-break:break-all}
.pill{display:inline-block;background:#22c3a622;color:var(--accent2);border-radius:99px;padding:2px 10px;font-size:12px;font-weight:600;margin-top:4px}
.dests{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-bottom:14px}
.dest{position:relative;background:var(--panel2);border:1px solid var(--line);border-radius:14px;padding:16px 14px;text-align:left}
.dest .ic{font-size:26px}.dest .nm{font-weight:600;margin-top:6px;word-break:break-word}
.dest .x{position:absolute;top:8px;right:8px;width:24px;height:24px;border-radius:8px;background:#0005;color:var(--muted)}
.dest .x:hover{color:var(--danger)}
.add{border:2px dashed var(--line);background:transparent;display:grid;place-items:center;color:var(--muted);min-height:108px}
.add:hover{border-color:var(--accent);color:var(--text)}
.startbar{margin-top:24px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.startbar .btn.primary{padding:14px 28px;font-size:16px}
/* modal */
.overlay{position:fixed;inset:0;background:#05060acc;backdrop-filter:blur(6px);display:grid;place-items:center;z-index:50;padding:16px}
.modal{background:var(--panel);border:1px solid var(--line);border-radius:20px;width:min(860px,100%);max-height:88vh;display:flex;flex-direction:column;box-shadow:0 30px 80px #000a;animation:pop .18s ease}
@keyframes pop{from{transform:scale(.96);opacity:0}}
.mhead{padding:18px 22px;border-bottom:1px solid var(--line)}
.mhead h3{margin:0;font-size:18px}
.crumbs{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.crumbs button{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:4px 10px;font-size:13px}
.crumbs button:hover{border-color:var(--accent)}
.mbody{display:grid;grid-template-columns:170px 1fr;min-height:0;flex:1}
.places{border-right:1px solid var(--line);padding:12px;display:flex;flex-direction:column;gap:6px;overflow:auto}
.places button{text-align:left;background:transparent;padding:9px 12px;border-radius:10px}
.places button:hover{background:var(--panel2)}
.grid{padding:16px;overflow:auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;align-content:start}
.folder{background:var(--panel2);border:1px solid var(--line);border-radius:14px;padding:14px 10px;text-align:center}
.folder:hover{border-color:var(--accent);transform:translateY(-1px)}
.folder .ic{font-size:34px}.folder .nm{font-size:13px;margin-top:6px;word-break:break-word}
.empty{color:var(--muted);grid-column:1/-1;text-align:center;padding:40px}
.mfoot{padding:14px 22px;border-top:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
@media(max-width:640px){.mbody{grid-template-columns:1fr}.places{display:none}}
/* player */
.player{display:grid;grid-template-columns:1fr 300px;gap:18px;padding:18px;height:calc(100vh - 73px)}
@media(max-width:900px){.player{grid-template-columns:1fr;height:auto}}
.stage{display:flex;flex-direction:column;gap:12px;min-height:0}
.vbox{position:relative;flex:1 1 0;background:#000;border-radius:var(--r);overflow:hidden;min-height:0;display:grid;place-items:center}
@media(max-width:900px){.vbox{flex:none;height:70vh}}
video{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;cursor:pointer;background:#000}
#picker{z-index:60}
.flash{position:absolute;font-size:60px;opacity:0;pointer-events:none;transition:opacity .4s}
.flash.on{opacity:.9;transition:none}
.controls{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:12px 16px}
.seek{width:100%;accent-color:var(--accent);cursor:pointer}
.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:8px}
.cbtn{background:var(--panel2);border:1px solid var(--line);border-radius:11px;padding:8px 12px;min-width:44px}
.cbtn:hover{border-color:var(--accent)}
.cbtn.play{background:var(--accent);border-color:transparent;font-size:18px;min-width:56px}
.speeds{display:flex;gap:4px;background:var(--panel2);padding:4px;border-radius:12px;border:1px solid var(--line)}
.speeds button{background:transparent;padding:5px 10px;border-radius:8px;font-size:13px}
.speeds button.on{background:var(--accent);font-weight:700}
.time{color:var(--muted);font-variant-numeric:tabular-nums;font-size:13px}
.spacer{flex:1}
.title{font-weight:600;font-size:16px;word-break:break-all}
.side{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);display:flex;flex-direction:column;min-height:0}
.side h4{margin:0;padding:14px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}
.list{overflow:auto;padding:8px;flex:1}
.item{display:block;width:100%;text-align:left;background:transparent;padding:9px 10px;border-radius:10px;font-size:13px;word-break:break-all}
.item:hover{background:var(--panel2)}.item.cur{background:#7c5cff33;outline:1px solid var(--accent)}
.item.done{opacity:.4;text-decoration:line-through}
/* end prompt */
.choose{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;padding:20px 22px;overflow:auto}
.choice{background:var(--panel2);border:1px solid var(--line);border-radius:16px;padding:20px 14px;text-align:center;transition:.12s}
.choice:hover{border-color:var(--accent2);background:#1c2a2b;transform:translateY(-2px)}
.choice:focus{outline:3px solid var(--accent2);outline-offset:3px;border-color:var(--accent2);background:#1c2a2b;transform:translateY(-2px)}
.choice.del:focus{outline-color:var(--danger)}
.choice .ic{font-size:36px}.choice .nm{font-weight:700;margin-top:8px;font-size:15px}
.choice .key{display:inline-block;margin-top:6px;font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:1px 6px}
.choice.del{background:#2a1219;border-color:#6b2333}.choice.del:hover{border-color:var(--danger);background:#3a1520}
.choice.keep{border-style:dashed}
.toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:var(--panel2);border:1px solid var(--line);padding:12px 20px;border-radius:12px;z-index:99;box-shadow:0 10px 30px #0008}
.done-screen{text-align:center;padding:80px 20px}
</style></head><body>
<header><div class="logo">▶</div><div><h1>Video Sorter</h1><div class="sub">Watch → choose a folder → next video</div></div>
<div class="spacer" style="flex:1"></div><button id="backBtn" class="btn hidden" onclick="showSetup()">⚙ Setup</button></header>

<!-- SETUP -->
<section id="setup" class="wrap">
  <div class="steps">
    <div class="card">
      <h2><span class="num" id="n1">1</span> Video folder</h2>
      <p class="hint">Pick the folder with the videos you want to sort.</p>
      <div id="srcEmpty" class="drop"><div class="big">🎞️</div><p>No folder selected</p>
        <button class="btn primary" onclick="openPicker('source')">Choose video folder</button></div>
      <div id="srcChosen" class="hidden">
        <div class="chosen"><div class="ic">📂</div><div style="flex:1;min-width:0"><div class="nm" id="srcName"></div>
          <div class="path" id="srcPath"></div><span class="pill" id="srcCount"></span></div>
          <button class="btn" onclick="openPicker('source')">Change</button></div>
      </div>
    </div>
    <div class="card">
      <h2><span class="num" id="n2">2</span> Destination folders</h2>
      <p class="hint">Add a folder for each category (Memes, Love, Music…). Each one is named after its folder.</p>
      <div class="dests" id="dests"></div>
    </div>
  </div>
  <div class="startbar">
    <div class="path" id="saveNote">Your choices are saved, so they'll still be here next time.</div>
    <button class="btn primary" id="startBtn" onclick="startPlayer()" disabled>▶ Start watching</button>
  </div>
</section>

<!-- PLAYER -->
<section id="player" class="player hidden">
  <div class="stage">
    <div class="title" id="vTitle"></div>
    <div class="vbox"><video id="v" playsinline></video><div class="flash" id="flash">⏸</div></div>
    <div class="controls">
      <input type="range" class="seek" id="seek" min="0" max="1000" value="0">
      <div class="row">
        <button class="cbtn" title="Previous (P)" onclick="go(-1)">⏮</button>
        <button class="cbtn" title="Back 10s (←)" onclick="skip(-10)">⏪ 10</button>
        <button class="cbtn play" id="playBtn" title="Play/Pause (Space)" onclick="toggle()">▶</button>
        <button class="cbtn" title="Forward 10s (→)" onclick="skip(10)">10 ⏩</button>
        <button class="cbtn" title="Next (N)" onclick="go(1)">⏭</button>
        <span class="time" id="time">0:00 / 0:00</span>
        <span class="spacer"></span>
        <div class="speeds" id="speeds"></div>
        <button class="cbtn" title="Sort now (S)" onclick="askSort()">📥 Sort now</button>
        <button class="cbtn" title="Fullscreen (F)" onclick="fs()">⛶</button>
      </div>
    </div>
  </div>
  <aside class="side"><h4><span>Queue</span><span class="time" id="qCount"></span></h4><div class="list" id="list"></div></aside>
</section>

<section id="finished" class="wrap hidden"><div class="card done-screen"><div class="big">🎉</div><h2 style="justify-content:center">All videos sorted!</h2>
<p class="hint">There are no videos left in this folder.</p><button class="btn primary" onclick="showSetup()">Back to setup</button></div></section>

<!-- FOLDER PICKER -->
<div class="overlay hidden" id="picker"><div class="modal">
  <div class="mhead"><h3 id="pTitle">Choose a folder</h3><div class="crumbs" id="crumbs"></div></div>
  <div class="mbody"><div class="places" id="places"></div><div class="grid" id="pgrid"></div></div>
  <div class="mfoot"><div><div class="nm" id="pCur" style="font-weight:600"></div><div class="path" id="pInfo"></div></div>
    <div style="display:flex;gap:8px"><button class="btn" onclick="closePicker()">Cancel</button>
    <button class="btn primary" id="pSelect" onclick="pickHere()">Select this folder</button></div></div>
</div></div>

<!-- END-OF-VIDEO PROMPT -->
<div class="overlay hidden" id="sorter"><div class="modal">
  <div class="mhead"><h3>Where should this video go?</h3><div class="path" id="sName"></div></div>
  <div class="choose" id="choices"></div>
  <div class="mfoot"><div class="path">Press a number key to pick quickly. Esc closes this.</div>
    <button class="btn" onclick="replay()">↺ Watch again</button></div>
</div></div>

<script>
const $=id=>document.getElementById(id);
let cfg={source:"",destinations:[]}, pickMode=null, pickPath="", videos=[], idx=0, done=new Set(), busy=false;
const v=$("v"), SPEEDS=[0.5,1,1.25,1.5,2];
const api=(u,b)=>fetch(u,b?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}:{}).then(r=>r.json());
const base=p=>p.replace(/\/+$/,"").split("/").pop()||"/";
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function toast(m){const t=document.createElement("div");t.className="toast";t.textContent=m;document.body.append(t);setTimeout(()=>t.remove(),2200)}
const save=()=>api("/api/config",cfg);

async function init(){cfg=await api("/api/config");cfg.destinations ||= [];renderSetup()}
async function renderSetup(){
  if(cfg.source){
    const vids=await api("/api/videos?path="+encodeURIComponent(cfg.source));
    $("srcEmpty").classList.add("hidden");$("srcChosen").classList.remove("hidden");
    $("srcName").textContent=base(cfg.source);$("srcPath").textContent=cfg.source;
    $("srcCount").textContent=vids.length+" video"+(vids.length==1?"":"s")+" found";
  }else{$("srcEmpty").classList.remove("hidden");$("srcChosen").classList.add("hidden")}
  $("dests").innerHTML=cfg.destinations.map((d,i)=>`<div class="dest"><button class="x" title="Remove" onclick="rmDest(${i})">✕</button>
    <div class="ic">📁</div><div class="nm">${esc(d.name)}</div><div class="path">${esc(d.path)}</div></div>`).join("")+
    `<button class="dest add" onclick="openPicker('dest')"><div><div class="big">＋</div>Add a folder</div></button>`;
  $("n1").className="num"+(cfg.source?" done":"");$("n1").textContent=cfg.source?"✓":"1";
  $("n2").className="num"+(cfg.destinations.length?" done":"");$("n2").textContent=cfg.destinations.length?"✓":"2";
  $("startBtn").disabled=!(cfg.source&&cfg.destinations.length);
}
function rmDest(i){cfg.destinations.splice(i,1);save();renderSetup()}

/* picker */
function openPicker(mode){pickMode=mode;$("pTitle").textContent=mode=="source"?"Choose your video folder":"Add a destination folder";
  $("picker").classList.remove("hidden");browse(mode=="source"?cfg.source:(cfg.destinations.at(-1)?.path.replace(/\/[^/]+$/,"")||""))}
function closePicker(){$("picker").classList.add("hidden")}
async function browse(p){
  const r=await api("/api/browse?path="+encodeURIComponent(p||""));if(r.error)return toast(r.error);
  pickPath=r.path;const parts=r.path.split("/").filter(Boolean);let acc="";
  $("crumbs").innerHTML=`<button data-p="/">🖥 /</button>`+parts.map(x=>{acc+="/"+x;return `<button data-p="${esc(acc)}">${esc(x)}</button>`}).join("");
  $("places").innerHTML=r.places.map(x=>`<button data-p="${esc(x.path)}">${{Home:"🏠",Videos:"🎬",Downloads:"⬇️",Desktop:"🖥",Documents:"📄"}[x.name]||"📁"} ${esc(x.name)}</button>`).join("");
  $("pgrid").innerHTML=(r.parent?`<button class="folder" data-p="${esc(r.parent)}"><div class="ic">⬆️</div><div class="nm">Up</div></button>`:"")+
    (r.dirs.length?r.dirs.map(d=>`<button class="folder" data-p="${esc(d.path)}"><div class="ic">📁</div><div class="nm">${esc(d.name)}</div></button>`).join(""):`<div class="empty">No sub-folders here</div>`);
  document.querySelectorAll("#picker [data-p]").forEach(b=>b.onclick=()=>browse(b.dataset.p));
  $("pCur").textContent="📂 "+base(r.path);
  $("pInfo").textContent=pickMode=="source"?`${r.videoCount} video${r.videoCount==1?"":"s"} in this folder`:r.path;
  const dup=pickMode=="dest"&&(cfg.destinations.some(d=>d.path==r.path)||r.path==cfg.source);
  $("pSelect").disabled=dup;$("pSelect").textContent=dup?"Can't use this folder":"Select “"+base(r.path)+"”";
}
function pickHere(){
  if(pickMode=="destSort"){cfg.destinations.push({name:base(pickPath),path:pickPath});save();closePicker();moveTo(cfg.destinations.length-1);return}
  if(pickMode=="source")cfg.source=pickPath;else cfg.destinations.push({name:base(pickPath),path:pickPath});
  save();closePicker();renderSetup();toast(pickMode=="source"?"Video folder set":"Added “"+base(pickPath)+"”");
}

/* player */
function showSetup(){v.pause();["player","finished"].forEach(x=>$(x).classList.add("hidden"));$("setup").classList.remove("hidden");$("backBtn").classList.add("hidden");renderSetup()}
async function startPlayer(){
  videos=await api("/api/videos?path="+encodeURIComponent(cfg.source));done=new Set();
  if(!videos.length)return toast("There are no videos in that folder");
  $("setup").classList.add("hidden");$("finished").classList.add("hidden");$("player").classList.remove("hidden");$("backBtn").classList.remove("hidden");
  $("speeds").innerHTML=SPEEDS.map(s=>`<button data-s="${s}">${s==1?"Normal":s+"×"}</button>`).join("");
  document.querySelectorAll("#speeds button").forEach(b=>b.onclick=()=>setSpeed(+b.dataset.s));
  play(0);
}
let speed=1;
function setSpeed(s){speed=s;v.playbackRate=s;document.querySelectorAll("#speeds button").forEach(b=>b.classList.toggle("on",+b.dataset.s==s))}
function renderList(){
  $("list").innerHTML=videos.map((x,i)=>`<button class="item ${i==idx?"cur":""} ${done.has(i)?"done":""}" onclick="play(${i})">${i+1}. ${esc(x.name)}</button>`).join("");
  $("qCount").textContent=(videos.length-done.size)+" left";
  document.querySelector(".item.cur")?.scrollIntoView({block:"nearest"});
}
function play(i){
  idx=i;const x=videos[i];v.src="/video?path="+encodeURIComponent(x.path);$("vTitle").textContent=x.name;
  v.play().catch(()=>{});setSpeed(speed);renderList();
}
function nextPending(from){for(let k=1;k<=videos.length;k++){const j=(from+k)%videos.length;if(!done.has(j))return j}return -1}
function go(d){let j=idx;for(let k=0;k<videos.length;k++){j=(j+d+videos.length)%videos.length;if(!done.has(j))return play(j)}}
function toggle(){v.paused?v.play():v.pause();flash(v.paused?"⏸":"▶")}
function skip(s){v.currentTime=Math.max(0,Math.min(v.duration||0,v.currentTime+s));flash(s>0?"⏩":"⏪")}
function flash(t){const f=$("flash");f.textContent=t;f.classList.add("on");setTimeout(()=>f.classList.remove("on"),50)}
function fs(){document.fullscreenElement?document.exitFullscreen():$("v").parentElement.requestFullscreen()}
const fmt=t=>isFinite(t)?Math.floor(t/60)+":"+String(Math.floor(t%60)).padStart(2,"0"):"0:00";
v.onclick=toggle;v.ondblclick=fs;
v.onplay=()=>$("playBtn").textContent="⏸";v.onpause=()=>$("playBtn").textContent="▶";
v.ontimeupdate=()=>{if(!seeking)$("seek").value=v.duration?v.currentTime/v.duration*1000:0;$("time").textContent=fmt(v.currentTime)+" / "+fmt(v.duration)};
let seeking=false;$("seek").oninput=()=>{seeking=true;if(v.duration)v.currentTime=$("seek").value/1000*v.duration};$("seek").onchange=()=>seeking=false;
v.onended=askSort;
v.onerror=()=>toast("This browser can't play this file type. You can still sort it.");

/* end-of-video prompt */
function askSort(){
  if(document.fullscreenElement)document.exitFullscreen();
  v.pause();$("sName").textContent=videos[idx].name;
  $("choices").innerHTML=cfg.destinations.map((d,i)=>`<button class="choice" onclick="moveTo(${i})"><div class="ic">📁</div><div class="nm">Move to ${esc(d.name)}</div>${i<9?`<span class="key">${i+1}</span>`:""}</button>`).join("")+
    `<button class="choice keep" onclick="openPicker('destSort')"><div class="ic">➕</div><div class="nm">New destination…</div><span class="key">A</span></button>`+
    `<button class="choice del" onclick="del()"><div class="ic">🗑️</div><div class="nm">Delete</div><span class="key">D</span></button>`+
    `<button class="choice keep" onclick="keep()"><div class="ic">⏭️</div><div class="nm">Leave it here</div><span class="key">K</span></button>`;
  $("sorter").classList.remove("hidden");
  document.querySelector("#choices .choice")?.focus();
}
function closeSort(){$("sorter").classList.add("hidden")}
function replay(){closeSort();v.currentTime=0;v.play()}
async function act(fn,msg){
  if(busy)return;busy=true;v.removeAttribute("src");v.load();
  const r=await fn();busy=false;
  if(r.error){toast("Error: "+r.error);play(idx);return}
  toast(msg(r));done.add(idx);closeSort();advance();
}
function moveTo(i){const d=cfg.destinations[i];act(()=>api("/api/move",{src:videos[idx].path,dest:d.path}),()=>"Moved to “"+d.name+"”")}
function del(){act(()=>api("/api/delete",{src:videos[idx].path}),r=>r.trashed?"Moved to Trash":"Deleted")}
function keep(){closeSort();done.add(idx);advance()}
function advance(){const j=nextPending(idx);if(j<0){v.removeAttribute("src");$("player").classList.add("hidden");$("finished").classList.remove("hidden");return}play(j)}

document.addEventListener("keydown",e=>{
  if($("player").classList.contains("hidden"))return;
  if(!$("picker").classList.contains("hidden")){if(e.key=="Escape")closePicker();return}
  const open=!$("sorter").classList.contains("hidden");
  if(open&&["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Enter"].includes(e.key)){
    e.preventDefault();const bs=[...document.querySelectorAll("#choices .choice")];let i=bs.indexOf(document.activeElement);
    if(e.key=="Enter"){(bs[i]||bs[0]).click();return}
    const cols=Math.max(1,Math.round($("choices").clientWidth/bs[0].offsetWidth)),step={ArrowLeft:-1,ArrowRight:1,ArrowUp:-cols,ArrowDown:cols}[e.key];
    i=i<0?0:Math.max(0,Math.min(bs.length-1,i+step));bs[i].focus();return}
  if(open){const n=parseInt(e.key);if(n>=1&&n<=cfg.destinations.length)moveTo(n-1);else if(e.key=="d")del();else if(e.key=="k")keep();else if(e.key=="a")openPicker('destSort');else if(e.key=="Escape")closeSort();return}
  if(e.target.tagName=="INPUT"&&e.key!=" ")return;
  ({" ":toggle,ArrowLeft:()=>skip(-10),ArrowRight:()=>skip(10),n:()=>go(1),p:()=>go(-1),f:fs,s:askSort}[e.key]||(()=>0))();
  if(e.key==" ")e.preventDefault();
});
init();
</script></body></html>"""

if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    print(f"Video Sorter running at {url}  (Ctrl+C to stop)")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
