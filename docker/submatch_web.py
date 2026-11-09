#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SubMatch Web — 带 Web 管理界面的定时字幕工具。

环境变量：
  ASSRT_TOKEN / WATCH_DIRS / INTERVAL_MINUTES / PREFER / LANG_TAG /
  OVERWRITE / ONLY_MISSING / MAX_PER_RUN / RUN_ON_START / WEB_PORT
"""

import json
import mimetypes
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import submatch_core as core

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.environ.get("SUBMATCH_CONFIG_DIR", "/config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
WEB_PORT = int(os.environ.get("WEB_PORT", "8099"))
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")

LOCK = threading.Lock()
STATE = {
    "items": [],
    "status": "空闲",
    "progress": {"done": 0, "total": 0},
    "summary": "",
    "last_run": "",
    "running": False,
    "log": [],
}


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with LOCK:
        STATE["log"].append(line)
        STATE["log"] = STATE["log"][-200:]


def env_bool(name, default=False):
    val = os.environ.get(name, "").strip().lower()
    return val in ("1", "true", "yes", "on") if val else default


def default_config():
    dirs = [d.strip() for d in os.environ.get("WATCH_DIRS", "").replace(",", ":").split(":") if d.strip()]
    return {
        "token": os.environ.get("ASSRT_TOKEN", "").strip(),
        "dirs": dirs,
        "interval": max(int(os.environ.get("INTERVAL_MINUTES", "360") or 360), 1),
        "prefer": os.environ.get("PREFER", "zh").strip() or "zh",
        "lang": os.environ.get("LANG_TAG", "zh").strip() or "zh",
        "overwrite": env_bool("OVERWRITE", False),
        "only_missing": env_bool("ONLY_MISSING", True),
        "max_per_run": int(os.environ.get("MAX_PER_RUN", "0") or 0),
        "run_on_start": env_bool("RUN_ON_START", True),
    }


def load_config():
    cfg = default_config()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        cfg.update({k: v for k, v in saved.items() if k in cfg})
    except Exception:
        pass
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


CONFIG = load_config()


def item_row(idx, it):
    sub_file = it.sub_files[0] if it.sub_files else ""
    if it.has_zh:
        result = "✅ 已有中字 · %s" % sub_file
    elif it.has_sub:
        result = "✅ 已有字幕（非中文）· %s" % sub_file
    else:
        result = ""
    return {
        "id": idx,
        "title": it.title,
        "video": it.video,
        "folder": it.folder,
        "has_sub": bool(it.has_sub),
        "has_zh": bool(it.has_zh),
        "sub_file": sub_file,
        "status": it.status,
        "result": result,
    }


def do_scan(include_all=None):
    if STATE["running"]:
        return
    only_missing = CONFIG["only_missing"] if include_all is None else not include_all
    with LOCK:
        STATE["items"] = []
        STATE["status"] = "扫描中…"
        STATE["progress"] = {"done": 0, "total": 0}

    items = []
    for root in CONFIG["dirs"]:
        if not os.path.isdir(root):
            log("❌ 目录不存在：%s" % root)
            continue
        log("扫描 %s …" % root)
        try:
            items.extend(core.scan(root, only_missing=only_missing))
        except Exception as exc:  # noqa: BLE001
            log("❌ 扫描失败 %s：%s" % (root, exc))

    for i, it in enumerate(items):
        it._idx = i
    rows = [item_row(i, it) for i, it in enumerate(items)]
    with LOCK:
        STATE["items"] = rows
        STATE["status"] = "扫描完成：%d 个" % len(rows)
    log("扫描完成：%d 个" % len(rows))
    return items


def do_download(selected_ids):
    if STATE["running"]:
        return
    with LOCK:
        items = STATE.get("_items") or []
        if not items:
            STATE["status"] = "没有可下载的条目"
            return
        targets = items if not selected_ids else [items[i] for i in selected_ids if 0 <= i < len(items)]
        STATE["running"] = True
        STATE["status"] = "下载中…"
        STATE["progress"] = {"done": 0, "total": len(targets)}
        STATE["summary"] = ""

    def worker():
        ok = fail = skip = 0
        assrt = core.Assrt(CONFIG["token"])
        cache = core.PackCache()
        try:
            for n, it in enumerate(targets):
                if it.has_zh and not CONFIG["overwrite"]:
                    skip += 1
                    with LOCK:
                        STATE["items"][it._idx]["result"] = "✅ 已有中字，跳过 · %s" % (it.sub_files[0] if it.sub_files else "")
                    continue
                try:
                    subs = assrt.search(it.query)
                    if not subs:
                        raise core.AssrtError("无结果")
                    ranked = [s for s in subs if core.score_sub(s, it.query, CONFIG["prefer"]) > -900]
                    ranked.sort(key=lambda s: core.score_sub(s, it.query, CONFIG["prefer"]), reverse=True)
                    if not ranked:
                        raise core.AssrtError("无匹配偏好的结果")
                    best = ranked[0]
                    detail = assrt.detail(best.get("id"))
                    packed = core.pick_pack_file(detail.get("filelist"), it.base, CONFIG["prefer"])
                    if packed:
                        data, _ = core.http_download(packed.get("url", ""), CONFIG["token"])
                        sub_name = packed.get("f") or "episode.srt"
                    else:
                        key = str(best.get("id"))
                        entry = cache.get(key)
                        if entry is None:
                            raw, disp = core.http_download(detail.get("url", ""), CONFIG["token"])
                            fname = detail.get("filename") or core.guess_name(detail.get("url", ""), disp)
                            entry = cache.put(key, raw, fname)
                        path = core.pick_cached_file(entry["files"], it.base, CONFIG["prefer"])
                        if not path:
                            raise core.AssrtError("整包里没有匹配这一集的字幕")
                        sub_name = os.path.basename(path)
                        with open(path, "rb") as f:
                            data = f.read()

                    ext = os.path.splitext(sub_name)[1].lower()
                    target = os.path.join(it.folder, "%s.%s%s" % (it.base, CONFIG["lang"], ext))
                    with open(target, "wb") as f:
                        f.write(core.to_utf8(data))

                    it.has_sub = True
                    it.has_zh = True
                    with LOCK:
                        row = STATE["items"][it._idx]
                        row["has_sub"] = True
                        row["has_zh"] = True
                        row["result"] = "✅ 已保存 %s" % os.path.basename(target)
                    log("✅ %s → %s" % (it.video, os.path.basename(target)))
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    with LOCK:
                        STATE["items"][it._idx]["result"] = "❌ %s" % str(exc)[:100]
                    log("❌ %s：%s" % (it.video, exc))
                    fail += 1
                finally:
                    with LOCK:
                        STATE["progress"]["done"] = n + 1
        finally:
            cache.clear()
            with LOCK:
                STATE["running"] = False
                STATE["status"] = "空闲"
                STATE["summary"] = "成功 %d · 失败 %d · 跳过 %d" % (ok, fail, skip)
                STATE["last_run"] = time.strftime("%Y-%m-%d %H:%M")
            log("本轮结束：成功 %d · 失败 %d · 跳过 %d" % (ok, fail, skip))

    threading.Thread(target=worker, daemon=True).start()


def scheduler():
    while True:
        try:
            interval = max(int(CONFIG.get("interval", 360)), 1) * 60
            time.sleep(interval)
            log("定时任务启动")
            items = do_scan()
            STATE["_items"] = items
            if items:
                do_download([])
                while STATE["running"]:
                    time.sleep(2)
        except Exception as exc:  # noqa: BLE001
            log("调度异常：%s" % exc)
            time.sleep(60)


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SubMatch 字幕管理</title>
<style>
:root{--bg:#14161c;--panel:#1d2029;--line:#2b3040;--text:#e8eaf0;--muted:#9aa3b5;--ok:#3ddc84;--bad:#ff6b6b;--warn:#ffb86b;--run:#8ab4f8;--accent:#ff9f0a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
header{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:16px;flex-wrap:wrap}
h1{font-size:18px;margin:0}
.badge{color:var(--muted);font-size:12px}
main{padding:16px 20px;max-width:1400px;margin:0 auto}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
button{background:#2a3040;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px 14px;cursor:pointer;font-size:13px}
button:hover{background:#333b4f}
button.primary{background:var(--accent);color:#1a1200;border-color:transparent;font-weight:600}
button:disabled{opacity:.5;cursor:not-allowed}
input,select,textarea{background:#12141a;border:1px solid var(--line);color:var(--text);border-radius:8px;padding:7px 10px;font-size:13px}
textarea{width:100%;min-height:64px;resize:vertical}
.progress{height:8px;background:#222734;border-radius:4px;overflow:hidden;margin:10px 0}
.progress>div{height:100%;width:0;background:var(--accent);transition:width .3s}
table{width:100%;border-collapse:collapse;background:var(--panel);border-radius:10px;overflow:hidden}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);font-size:13px;vertical-align:top}
th{color:var(--muted);font-weight:500;background:#191c24;position:sticky;top:0}
tr:hover td{background:#222734}
td.num{width:52px;color:var(--muted)}
td.st{width:80px;text-align:center;font-size:16px}
td.res{color:var(--muted)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.run{color:var(--run)}
details{margin-top:18px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
summary{cursor:pointer;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:12px}
label{display:block;color:var(--muted);font-size:12px;margin-bottom:4px}
#log{background:#0f1116;border:1px solid var(--line);border-radius:8px;padding:10px;height:160px;overflow:auto;font:12px/1.5 ui-monospace,Menlo,monospace;color:#a9b3c6;white-space:pre-wrap}
.muted{color:var(--muted)}
</style>
</head>
<body>
<header>
  <h1>SubMatch 字幕管理</h1>
  <span class="badge" id="state">加载中…</span>
  <span class="badge" id="lastrun"></span>
</header>
<main>
  <div class="bar">
    <button id="btnScan" class="primary">扫描</button>
    <button id="btnDownload" class="primary">下载选中</button>
    <button id="btnAll">全选</button>
    <button id="btnNone">清空</button>
    <label class="muted"><input type="checkbox" id="chkAll"> 显示已有字幕</label>
    <span class="badge" id="count"></span>
  </div>
  <div class="progress"><div id="bar"></div></div>
  <div class="badge" id="summary"></div>
  <table>
    <thead><tr><th style="width:36px"></th><th class="num">#</th><th>标题</th><th>文件</th><th style="width:80px">字幕状态</th><th style="width:34%">下载结果</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>

  <details>
    <summary>设置</summary>
    <div class="grid">
      <div><label>Assrt Token</label><input id="token" type="password" style="width:100%"></div>
      <div><label>扫描间隔（分钟）</label><input id="interval" type="number" min="1" style="width:100%"></div>
      <div><label>字幕偏好</label>
        <select id="prefer" style="width:100%">
          <option value="zh">中文优先</option>
          <option value="bi">双语优先</option>
          <option value="any">不限</option>
        </select>
      </div>
      <div><label>语言后缀</label><input id="lang" style="width:100%"></div>
      <div><label>每轮上限（0=不限）</label><input id="maxrun" type="number" min="0" style="width:100%"></div>
      <div><label>覆盖已有中字</label><label class="muted"><input type="checkbox" id="overwrite"> 允许覆盖</label></div>
    </div>
    <div style="margin-top:12px"><label>监控目录（每行一个，容器内路径）</label><textarea id="dirs"></textarea></div>
    <div class="bar" style="margin-top:12px">
      <button id="btnSave" class="primary">保存设置</button>
      <span class="badge">保存后下一轮生效；间隔修改会重置计时</span>
    </div>
  </details>

  <details>
    <summary>运行日志</summary>
    <div id="log"></div>
  </details>
</main>
<script>
let state = null;

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(await r.text());
  return await r.json();
}

function cls(text) {
  if (text.startsWith('✅')) return 'ok';
  if (text.startsWith('❌')) return 'bad';
  if (text.startsWith('✅ 已有')) return 'warn';
  return '';
}

function render() {
  if (!state) return;
  document.getElementById('state').textContent = state.status + (state.running ? '（进行中）' : '');
  document.getElementById('lastrun').textContent = state.last_run ? '上次运行 ' + state.last_run : '';
  const p = state.progress || {done:0,total:0};
  const pct = p.total ? Math.round(p.done / p.total * 100) : 0;
  document.getElementById('bar').style.width = pct + '%';
  document.getElementById('summary').textContent = state.summary ? '结果：' + state.summary : '';
  document.getElementById('count').textContent = '共 ' + state.items.length + ' 个 · 已选 ' + selected().length + ' 个';

  const tbody = document.getElementById('rows');
  tbody.innerHTML = '';
  for (const it of state.items) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td><input type="checkbox" data-id="' + it.id + '"></td>' +
      '<td class="num">' + (it.id + 1) + '</td>' +
      '<td>' + escapeHtml(it.title) + '</td>' +
      '<td>' + escapeHtml(it.video) + '</td>' +
      '<td class="st">' + (it.has_sub ? '✅' : '❌') + '</td>' +
      '<td class="res ' + cls(it.result || '') + '">' + escapeHtml(it.result || '') + '</td>';
    tbody.appendChild(tr);
  }
  restoreSelection();
  document.getElementById('log').textContent = (state.log || []).join('\n');
  document.getElementById('log').scrollTop = 1e9;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function selected() {
  return [...document.querySelectorAll('#rows input[type=checkbox]:checked')].map(x => parseInt(x.dataset.id, 10));
}

let keep = new Set();
function restoreSelection() {
  document.querySelectorAll('#rows input[type=checkbox]').forEach(cb => {
    const id = parseInt(cb.dataset.id, 10);
    if (keep.has(id)) cb.checked = true;
    cb.onchange = () => {
      if (cb.checked) keep.add(id); else keep.delete(id);
      document.getElementById('count').textContent = '共 ' + state.items.length + ' 个 · 已选 ' + selected().length + ' 个';
    };
  });
}

async function refresh() {
  state = await api('/api/state');
  render();
  fillSettings();
}

let settingsLoaded = false;
function fillSettings() {
  if (settingsLoaded || !state) return;
  const c = state.config;
  const tk = document.getElementById('token');
  tk.value = '';
  tk.placeholder = c.has_token ? '已配置（留空则不修改）' : '请填 Assrt Token';
  document.getElementById('interval').value = c.interval;
  document.getElementById('prefer').value = c.prefer;
  document.getElementById('lang').value = c.lang;
  document.getElementById('maxrun').value = c.max_per_run;
  document.getElementById('overwrite').checked = !!c.overwrite;
  document.getElementById('dirs').value = (c.dirs || []).join('\n');
  settingsLoaded = true;
}

document.getElementById('btnScan').onclick = async () => {
  const all = document.getElementById('chkAll').checked;
  await api('/api/scan', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({all})});
  setTimeout(refresh, 500);
};

document.getElementById('btnDownload').onclick = async () => {
  const ids = selected();
  await api('/api/download', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ids})});
  refresh();
};

document.getElementById('btnAll').onclick = () => {
  document.querySelectorAll('#rows input[type=checkbox]').forEach(cb => { cb.checked = true; keep.add(parseInt(cb.dataset.id,10)); });
  document.getElementById('count').textContent = '共 ' + state.items.length + ' 个 · 已选 ' + selected().length + ' 个';
};
document.getElementById('btnNone').onclick = () => {
  keep.clear();
  document.querySelectorAll('#rows input[type=checkbox]').forEach(cb => cb.checked = false);
  document.getElementById('count').textContent = '共 ' + state.items.length + ' 个 · 已选 0 个';
};

document.getElementById('btnSave').onclick = async () => {
  const cfg = {
    token: document.getElementById('token').value.trim() || undefined,
    interval: parseInt(document.getElementById('interval').value, 10) || 360,
    prefer: document.getElementById('prefer').value,
    lang: document.getElementById('lang').value.trim() || 'zh',
    max_per_run: parseInt(document.getElementById('maxrun').value, 10) || 0,
    overwrite: document.getElementById('overwrite').checked,
    dirs: document.getElementById('dirs').value.split('\n').map(s => s.trim()).filter(Boolean),
  };
  await api('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)});
  settingsLoaded = false;
  await refresh();
  alert('设置已保存');
};

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "SubMatchWeb/1.0"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, HTML, "text/html; charset=utf-8")
        elif path == "/api/state":
            with LOCK:
                payload = {
                    "status": STATE["status"],
                    "running": STATE["running"],
                    "progress": STATE["progress"],
                    "summary": STATE["summary"],
                    "last_run": STATE["last_run"],
                    "items": STATE["items"],
                    "log": STATE["log"],
                    "config": {k: v for k, v in CONFIG.items() if k != "token"} | {
                        "token": "",
                        "has_token": bool(CONFIG["token"]),
                    },
                }
            self._send(200, payload)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            data = {}

        if path == "/api/scan":
            if STATE["running"]:
                return self._send(409, {"error": "busy"})
            items = do_scan(include_all=bool(data.get("all")))
            with LOCK:
                STATE["_items"] = items
            return self._send(200, {"ok": True, "count": len(items)})

        if path == "/api/download":
            ids = data.get("ids") or []
            do_download([int(i) for i in ids])
            return self._send(200, {"ok": True})

        if path == "/api/config":
            for key in ("interval", "prefer", "lang", "max_per_run", "overwrite", "dirs"):
                if key in data:
                    CONFIG[key] = data[key]
            token = str(data.get("token") or "").strip()
            if token and token != "***":
                CONFIG["token"] = token
            save_config(CONFIG)
            log("设置已更新")
            return self._send(200, {"ok": True})

        return self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass


def main():
    log("SubMatch Web 启动，端口 %d" % WEB_PORT)
    log("监控目录：%s" % ("、".join(CONFIG["dirs"]) or "（未配置）"))
    threading.Thread(target=scheduler, daemon=True).start()
    if CONFIG.get("run_on_start") and CONFIG["dirs"]:
        items = do_scan()
        with LOCK:
            STATE["_items"] = items
    ThreadingHTTPServer((WEB_HOST, WEB_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
