#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SubMatch 图形界面（依赖 submatch_core）。"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from submatch_core import (
    APP_NAME, CFG_DIR, CFG_PATH, API_BASE, VIDEO_EXT, SUB_EXT, SKIP_DIRS,
    Item, PackCache, Assrt, AssrtError, load_config, save_config,
    scan, build_query, score_sub, score_sub_name, pick_pack_file,
    pick_cached_file, pick_subtitle, http_download, guess_name,
    extract_subtitles, to_utf8,
)

class App:
    def __init__(self, root):
        self.root = root
        self.items = []
        self.queue = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()
        self.cfg = load_config()

        root.title("SubMatch — Assrt 中文字幕")
        self._build_menu()
        root.geometry("1080x700")
        root.minsize(900, 560)

        pad = {"padx": 8, "pady": 4}

        top = ttk.Frame(root)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="媒体目录").grid(row=0, column=0, sticky="w")
        self.root_var = tk.StringVar(value=self.cfg.get("root", ""))
        ttk.Entry(top, textvariable=self.root_var, width=70).grid(row=0, column=1, sticky="we", padx=6)
        ttk.Button(top, text="选择…", command=self.pick_root).grid(row=0, column=2)

        ttk.Label(top, text="Assrt Token").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.token_var = tk.StringVar(value=self.cfg.get("token", ""))
        ttk.Entry(top, textvariable=self.token_var, width=70, show="•").grid(row=1, column=1, sticky="we", padx=6, pady=(6, 0))
        ttk.Button(top, text="保存", command=self.save_settings).grid(row=1, column=2, pady=(6, 0))
        top.columnconfigure(1, weight=1)

        opts = ttk.Frame(root)
        opts.pack(fill="x", **pad)
        self.lang_var = tk.StringVar(value=self.cfg.get("lang", "zh"))
        ttk.Label(opts, text="语言后缀").pack(side="left")
        ttk.Combobox(opts, textvariable=self.lang_var, values=["zh", "zh-Hans", "zh-CN", "chs"], width=8, state="readonly").pack(side="left", padx=(4, 16))
        ttk.Label(opts, text="字幕偏好").pack(side="left", padx=(10, 0))
        self.prefer_var = tk.StringVar(value=self.cfg.get("prefer", "中文优先"))
        ttk.Combobox(opts, textvariable=self.prefer_var, values=["中文优先", "双语优先", "不限"], width=9, state="readonly").pack(side="left", padx=(4, 14))
        self.only_missing = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="只列缺字幕的", variable=self.only_missing).pack(side="left")
        self.overwrite = tk.BooleanVar(value=self.cfg.get("overwrite", False))
        ttk.Checkbutton(opts, text="覆盖已有中字", variable=self.overwrite).pack(side="left", padx=(10, 0))
        self.dry_run = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="试运行", variable=self.dry_run).pack(side="left", padx=10)

        btns = ttk.Frame(root)
        btns.pack(fill="x", **pad)
        self.scan_btn = ttk.Button(btns, text="扫描", command=self.start_scan)
        self.scan_btn.pack(side="left")
        self.run_btn = ttk.Button(btns, text="开始下载", command=self.start_download, state="disabled")
        self.run_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(btns, text="停止", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Button(btns, text="全选", command=self.select_all).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="清空选择", command=self.clear_selection).pack(side="left", padx=(4, 0))
        self.count_var = tk.StringVar(value="")
        ttk.Label(btns, textvariable=self.count_var).pack(side="left", padx=16)

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100, value=0)
        self.progress.pack(fill="x", padx=8, pady=(0, 4))

        cols = ("idx", "title", "file", "sub", "status")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", height=14, selectmode="extended")
        for c, t, w in (("idx", "序号", 56), ("title", "标题", 220), ("file", "文件", 380),
                        ("sub", "字幕状态", 90), ("status", "下载结果", 260)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.tag_configure("ok", foreground="#2ecc71")
        self.tree.tag_configure("fail", foreground="#ff6b6b")
        self.tree.tag_configure("skip", foreground="#ffb86b")
        self.tree.tag_configure("run", foreground="#8ab4f8")
        self.tree.tag_configure("todo", foreground="#cccccc")
        self.tree.pack(fill="both", expand=True, padx=8)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.update_selection_label())

        self.log = scrolledtext.ScrolledText(root, height=9, state="disabled")
        self.log.pack(fill="both", expand=False, padx=8, pady=8)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(150, self.drain)

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        apple = tk.Menu(menubar, name="apple")
        apple.add_command(label="关于 SubMatch", command=self.show_about)
        apple.add_separator()
        apple.add_command(label="退出 SubMatch", command=self.on_close, accelerator="Command+Q")
        menubar.add_cascade(menu=apple)
        self.root.config(menu=menubar)
        try:
            self.root.createcommand("tk::mac::Quit", self.on_close)
        except tk.TclError:
            pass

    def show_about(self):
        messagebox.showinfo("SubMatch", "SubMatch 1.1\nAssrt 中文字幕批量下载\n\n媒体目录扫描 → 搜索 → 命名写回")

    def on_close(self):
        """关窗即退出：先停后台线程，再结束进程。"""
        self.stop_flag.set()
        try:
            self.root.destroy()
        finally:
            os._exit(0)

    # ---------- 基础设施

    def log_line(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S ") + text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def drain(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.log_line(payload)
                elif kind == "items":
                    self.set_items(payload)
                elif kind == "row":
                    idx, status, tag = payload
                    iid = str(idx)
                    if self.tree.exists(iid):
                        self.tree.set(iid, "status", status)
                        if tag == "ok":
                            self.tree.set(iid, "sub", "✅")
                        self.tree.item(iid, tags=(tag,))
                elif kind == "count":
                    self.count_var.set(payload)
                elif kind == "progress":
                    done, total = payload
                    self.progress.configure(mode="determinate", maximum=max(total, 1), value=done)
                    self.count_var.set("进度 %d/%d" % (done, total))
                elif kind == "done":
                    self.finish(payload)
        except queue.Empty:
            pass
        try:
            self.root.after(150, self.drain)
        except tk.TclError:
            pass

    def pick_root(self):
        path = filedialog.askdirectory(title="选择媒体根目录（可以是挂载好的 SMB 卷）")
        if path:
            self.root_var.set(path)
            self.save_settings(quiet=True)

    def save_settings(self, quiet=False):
        self.cfg.update({
            "root": self.root_var.get().strip(),
            "token": self.token_var.get().strip(),
            "lang": self.lang_var.get(),
            "prefer": self.prefer_var.get(),
            "overwrite": bool(self.overwrite.get()),
        })
        save_config(self.cfg)
        if not quiet:
            self.log_line("设置已保存")

    def set_items(self, items):
        self.items = items
        self.tree.delete(*self.tree.get_children())
        for i, it in enumerate(items):
            text, tag = self.row_view(it)
            mark = "✅" if it.has_sub else "❌"
            self.tree.insert("", "end", iid=str(i), values=(i + 1, it.title, it.video, mark, text), tags=(tag,))
        self.count_var.set("共 %d 个 · 已选 0 个" % len(items))
        self.run_btn.configure(state="normal" if items else "disabled")

    @staticmethod
    def row_view(it):
        if it.status == "已有中字":
            return ("✅ 已有中字 · %s" % (it.sub_files[0] if it.sub_files else ""), "skip")
        if it.status == "已有字幕":
            return ("✅ 已有字幕 · %s" % (it.note or ""), "todo")
        return ("❌ 待处理" if it.status == "待处理" else it.status, "todo")

    def update_selection_label(self):
        self.count_var.set("共 %d 个 · 已选 %d 个" % (len(self.items), len(self.tree.selection())))

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())
        self.update_selection_label()

    def clear_selection(self):
        self.tree.selection_remove(self.tree.selection())
        self.update_selection_label()

    # ---------- 扫描

    def start_scan(self):
        root_dir = self.root_var.get().strip()
        if not root_dir or not os.path.isdir(root_dir):
            messagebox.showerror("路径无效", "请选择一个存在的目录（SMB 请先在 Finder 里连上）")
            return
        self.save_settings(quiet=True)
        self.scan_btn.configure(state="disabled")
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.stop_flag.clear()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.log_line("开始扫描：%s" % root_dir)
        only_missing = self.only_missing.get()

        def progress(dirs, found):
            self.queue.put(("count", "扫描中… 已检查 %d 个目录，发现 %d 部" % (dirs, found)))

        def work():
            try:
                items = scan(root_dir, only_missing, progress=progress, stop=self.stop_flag)
                self.queue.put(("items", items))
                self.queue.put(("log", "扫描完成，%d 个" % len(items)))
            except Exception as e:  # noqa: BLE001
                self.queue.put(("log", "扫描出错：%s" % e))
            finally:
                self.queue.put(("done", "scan"))

        threading.Thread(target=work, daemon=True).start()

    # ---------- 下载

    def start_download(self):
        if not self.items:
            return
        token = self.token_var.get().strip()
        if not token and not self.dry_run.get():
            messagebox.showerror("缺少 Token", "请先填 Assrt Token（assrt.net 用户面板里拿）")
            return
        self.save_settings(quiet=True)

        sel = [int(i) for i in self.tree.selection()]
        pairs = [(i, self.items[i]) for i in sel] if sel else list(enumerate(self.items))
        if not pairs:
            return

        self.stop_flag.clear()
        self.run_btn.configure(state="disabled")
        self.scan_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        lang = self.lang_var.get().strip() or "zh"
        dry = self.dry_run.get()
        prefer = {"中文优先": "zh", "双语优先": "bi", "不限": "any"}.get(self.prefer_var.get(), "zh")
        overwrite = self.overwrite.get()
        assrt = Assrt(token)
        cache = PackCache()

        def work():
            ok = fail = skip = 0
            total = len(pairs)
            self.queue.put(("progress", (0, total)))
            self.queue.put(("log", "开始下载：%d 个%s" % (total, "（选中）" if sel else "")))

            for n, (idx, it) in enumerate(pairs):
                if self.stop_flag.is_set():
                    self.queue.put(("log", "已停止"))
                    break
                if it.done:
                    self.queue.put(("progress", (n + 1, total)))
                    continue

                if it.has_zh and not overwrite:
                    it.status = "已有中字"
                    text = "✅ 已有中字，跳过 · %s" % (it.sub_files[0] if it.sub_files else "")
                    self.queue.put(("row", (idx, text, "skip")))
                    skip += 1
                    self.queue.put(("progress", (n + 1, total)))
                    continue

                try:
                    self.queue.put(("row", (idx, "搜索中…", "run")))
                    self.queue.put(("log", "搜索：%s" % it.query))
                    subs = assrt.search(it.query)
                    if not subs:
                        it.status = "无结果"
                        self.queue.put(("row", (idx, "❌ 无结果", "fail")))
                        fail += 1
                        self.queue.put(("progress", (n + 1, total)))
                        continue

                    ranked = [x for x in subs if score_sub(x, it.query, prefer) > -900]
                    ranked.sort(key=lambda s: score_sub(s, it.query, prefer), reverse=True)
                    if not ranked:
                        it.status = "无中文"
                        self.queue.put(("row", (idx, "❌ 无%s结果" % self.prefer_var.get(), "fail")))
                        fail += 1
                        self.queue.put(("progress", (n + 1, total)))
                        continue

                    best = ranked[0]
                    detail = assrt.detail(best.get("id"))
                    name = str(detail.get("native_name") or detail.get("filename") or "").split("/")[0][:40]

                    if dry:
                        it.status = "试运行"
                        self.queue.put(("row", (idx, "试运行 · %s" % name, "run")))
                        ok += 1
                        self.queue.put(("progress", (n + 1, total)))
                        continue

                    self.queue.put(("row", (idx, "下载中 · %s" % name, "run")))
                    packed = pick_pack_file(detail.get("filelist"), it.base, prefer)
                    if packed:
                        sub_data, _ = http_download(packed.get("url", ""), token)
                        sub_name = packed.get("f") or "episode.srt"
                    else:
                        key = str(best.get("id"))
                        entry = cache.get(key)
                        if entry is None:
                            data, disp = http_download(detail.get("url", ""), token)
                            fname = detail.get("filename") or guess_name(detail.get("url", ""), disp)
                            entry = cache.put(key, data, fname)
                            self.queue.put(("log", "整包已缓存：%s（%d 个字幕文件）" % (fname, len(entry["files"]))))
                        path = pick_cached_file(entry["files"], it.base, prefer)
                        if not path:
                            raise AssrtError("整包里没有匹配这一集的字幕")
                        sub_name = os.path.basename(path)
                        with open(path, "rb") as f:
                            sub_data = f.read()

                    ext = os.path.splitext(sub_name)[1].lower()
                    target = os.path.join(it.folder, "%s.%s%s" % (it.base, lang, ext))
                    with open(target, "wb") as f:
                        f.write(to_utf8(sub_data))

                    it.done = True
                    it.status = "完成"
                    self.queue.put(("row", (idx, "✅ 已保存 %s" % os.path.basename(target), "ok")))
                    self.queue.put(("log", "✅ %s → %s" % (it.video, os.path.basename(target))))
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    it.status = "失败"
                    self.queue.put(("row", (idx, "❌ 失败：%s" % str(e)[:80], "fail")))
                    self.queue.put(("log", "❌ %s：%s" % (it.video, e)))
                    fail += 1
                self.queue.put(("progress", (n + 1, total)))

            cache.clear()
            self.queue.put(("done", "download:%d:%d:%d" % (ok, fail, skip, )))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_flag.set()
        self.log_line("正在停止…")

    def finish(self, tag):
        if tag == "scan":
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.scan_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            if self.items:
                self.run_btn.configure(state="normal")
        elif tag.startswith("download:"):
            _, ok, fail, skip = tag.split(":")
            self.scan_btn.configure(state="normal")
            self.run_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            summary = "成功 %s · 失败 %s · 跳过 %s" % (ok, fail, skip)
            self.log_line("结束：" + summary)
            self.count_var.set("已结束 · " + summary)


def main():
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.3)
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
