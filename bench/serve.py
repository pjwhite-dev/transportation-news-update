"""Local blind-judging page for the owner.

    python -m bench.serve            # then open http://127.0.0.1:8770/

Shows each benchmark day's fact sheet and four anonymous summaries; the owner ranks them
1 (best) to 4 (worst). Results are written to bench/data/user/judgments.json after every day, so
the session can be stopped and resumed. Model names and links are hidden to keep it blind.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bench import common
from bench.judge import blind_key, candidates_for

JUDGMENTS = common.DATA / "user" / "judgments.json"
LINK = re.compile(r"\[([^\]]{1,200})\]\((https?://[^\s)]+)\)")
BARE = re.compile(r"\s*\[https?://[^\]\s]+\]")


def display(text: str) -> str:
    return BARE.sub("", LINK.sub(lambda m: m.group(1), text)).strip()


def tasks() -> list[dict]:
    out = []
    for day in common.BENCH_DAYS:
        cands = candidates_for(day)
        if len(cands) != 4:
            continue
        key = blind_key(day, sorted(cands))
        out.append(
            {
                "day": day,
                "sheet": [
                    {"id": s["id"], "section": s["section"], "headline": s["headline"], "source": s["source"],
                     "summary": s["summary"]}
                    for s in common.fact_sheet(day)
                ],
                "summaries": [{"label": label, "text": display(cands[key[label]])} for label in "ABCD"],
            }
        )
    return out


PAGE = r"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blind judging</title>
<style>
:root{--ink:#0E1726;--soft:#46546B;--line:#DDE3EC;--night:#0A1424;--amber:#F2B33D;--steel:#3B5B8C}
body{font:16px/1.55 system-ui,'Segoe UI',Arial,sans-serif;color:var(--ink);margin:0;background:#F6F8FB}
header{background:var(--night);color:#fff;padding:18px 24px}
header h1{margin:0 0 4px;font:600 1.3rem Georgia,serif}header p{margin:0;color:#C5D2E6;font-size:.92rem}
main{max-width:1000px;margin:0 auto;padding:22px 20px 80px}
.bar{height:8px;background:#E3E8EF;border-radius:4px;margin:0 0 18px}.bar i{display:block;height:8px;background:var(--amber);border-radius:4px}
details{background:#fff;border:1px solid var(--line);padding:10px 16px;margin:0 0 16px}
summary{cursor:pointer;font-weight:600}
.sheet li{margin:6px 0;font-size:.92rem}.sheet small{color:var(--soft)}
.card{background:#fff;border:1px solid var(--line);padding:16px 18px;margin:0 0 14px}
.card h3{margin:0 0 8px;font:600 .8rem system-ui;letter-spacing:.14em;color:var(--steel);display:flex;justify-content:space-between;align-items:center}
.card p{margin:0 0 10px;white-space:pre-wrap}
select{font:600 1rem system-ui;padding:6px 10px}
button{font:600 1rem system-ui;padding:11px 22px;border:0;background:var(--night);color:#fff;cursor:pointer;border-radius:6px}
.msg{color:#B3261E;margin-left:12px;font-size:.92rem}.done{text-align:center;padding:60px 0}
</style>
<header><h1>Blind judging</h1><p>Rank the four summaries of each day, 1 = best to 4 = worst. Model names are hidden.</p></header>
<main id="app">Loading&hellip;</main>
<script>
let tasks=[],saved={},i=0;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(){
  tasks=await (await fetch('/api/tasks')).json();
  saved=await (await fetch('/api/progress')).json();
  i=tasks.findIndex(t=>!saved[t.day]); if(i<0)i=tasks.length;
  draw();
}
function draw(){
  const app=document.getElementById('app');
  const done=Object.keys(saved).length;
  const bar=`<div class="bar"><i style="width:${100*done/tasks.length}%"></i></div>`;
  if(i>=tasks.length){app.innerHTML=bar+`<div class="done"><h2>All done, thank you.</h2><p>Your rankings are saved. You can close this tab and tell Claude.</p></div>`;return;}
  const t=tasks[i];
  const prior=saved[t.day]&&saved[t.day].ranks||{};
  app.innerHTML=bar+`<p><b>Day ${i+1} of ${tasks.length}</b> &middot; ${esc(t.day)}</p>
  <details><summary>Fact sheet: the ${t.sheet.length} stories all four summaries were written from</summary><ol class="sheet">`+
  t.sheet.map(s=>`<li><b>${esc(s.headline)}</b> <small>${esc(s.section)} &middot; ${esc(s.source)}</small><br><small>${esc(s.summary)}</small></li>`).join('')+`</ol></details>`+
  t.summaries.map(s=>`<div class="card"><h3>SUMMARY ${s.label}<span>Rank <select data-l="${s.label}"><option value="">&ndash;</option>${[1,2,3,4].map(n=>`<option ${prior[s.label]==n?'selected':''}>${n}</option>`).join('')}</select></span></h3><p>${esc(s.text)}</p></div>`).join('')+
  `<p><label>Optional note <input id="note" size="50" value="${esc((saved[t.day]&&saved[t.day].note)||'')}"></label></p>
  <button id="save">Save and next</button><button id="back" style="background:#fff;color:#0A1424;border:1px solid #C3CEDD;margin-left:10px" ${i==0?'disabled':''}>Back</button><span class="msg" id="msg"></span>`;
  document.getElementById('save').onclick=save;
  document.getElementById('back').onclick=()=>{i=Math.max(0,i-1);draw();};
}
async function save(){
  const t=tasks[i],ranks={};
  document.querySelectorAll('select[data-l]').forEach(s=>ranks[s.dataset.l]=s.value);
  const vals=Object.values(ranks);
  if(vals.some(v=>!v)||new Set(vals).size!==4){document.getElementById('msg').textContent='Give each summary a different rank from 1 to 4.';return;}
  const note=document.getElementById('note').value;
  await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({day:t.day,ranks,note})});
  saved[t.day]={ranks,note};i++;draw();window.scrollTo(0,0);
}
load();
</script>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, kind: str = "application/json", status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/tasks":
            self._send(json.dumps(tasks()).encode())
        elif self.path == "/api/progress":
            self._send(json.dumps(common.read_json(JUDGMENTS, {})).encode())
        elif self.path in ("/", "/index.html"):
            self._send(PAGE.encode(), "text/html")
        else:
            self._send(b"{}", status=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/save":
            return self._send(b"{}", status=404)
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        ranks = payload.get("ranks", {})
        if payload.get("day") not in common.BENCH_DAYS or sorted(ranks.values()) != ["1", "2", "3", "4"]:
            return self._send(b'{"error":"invalid"}', status=400)
        saved = common.read_json(JUDGMENTS, {})
        saved[payload["day"]] = {"ranks": ranks, "note": str(payload.get("note", ""))[:500]}
        common.write_json(JUDGMENTS, saved)
        self._send(b'{"ok":true}')

    def log_message(self, *args) -> None:  # quiet
        pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8770), Handler)
    print("Judging page: http://127.0.0.1:8770/  (Ctrl+C to stop)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
