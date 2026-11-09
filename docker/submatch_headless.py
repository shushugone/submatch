#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SubMatch Headless — 定时扫描媒体目录，自动补齐中文字幕。

环境变量：
  ASSRT_TOKEN       必填，assrt.net 的 API Token
  WATCH_DIRS        必填，冒号或逗号分隔的目录，如 /media/movie:/media/tv
  INTERVAL_MINUTES  扫描间隔（分钟），默认 360
  RUN_ON_START      启动后先跑一次，默认 1
  PREFER            字幕偏好：zh（中文优先）/ bi（双语优先）/ any（不限），默认 zh
  LANG_TAG          生成文件的语言后缀，默认 zh
  OVERWRITE         1 = 覆盖已有中文字幕，默认 0
  MAX_PER_RUN       每轮最多处理多少部，0 = 不限，默认 0
  DRY_RUN           1 = 只搜索不下载，默认 0
  SUBMATCH_CONFIG_DIR  状态目录，默认 /config
"""

import json
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import submatch_core as core

STOP = False


def log(msg):
    print("[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def on_signal(signum, _frame):
    global STOP
    STOP = True
    log("收到信号 %s，准备退出…" % signum)


def env_bool(name, default=False):
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def env_int(name, default):
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def parse_dirs():
    raw = os.environ.get("WATCH_DIRS", "").strip()
    if not raw:
        return []
    parts = []
    for chunk in raw.replace(",", ":").split(":"):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts


CONFIG = {
    "token": os.environ.get("ASSRT_TOKEN", "").strip(),
    "dirs": parse_dirs(),
    "interval": max(env_int("INTERVAL_MINUTES", 360), 1),
    "prefer": os.environ.get("PREFER", "zh").strip() or "zh",
    "lang": os.environ.get("LANG_TAG", "zh").strip() or "zh",
    "overwrite": env_bool("OVERWRITE", False),
    "run_on_start": env_bool("RUN_ON_START", True),
    "max_per_run": env_int("MAX_PER_RUN", 0),
    "dry_run": env_bool("DRY_RUN", False),
}


def run_once():
    token = CONFIG["token"]
    if not token:
        log("❌ 未设置 ASSRT_TOKEN，跳过本轮")
        return

    assrt = core.Assrt(token)
    cache = core.PackCache()
    total_ok = total_fail = total_skip = 0

    for root in CONFIG["dirs"]:
        if STOP:
            break
        if not os.path.isdir(root):
            log("❌ 目录不存在或不可访问：%s" % root)
            continue

        log("扫描 %s …" % root)
        try:
            items = core.scan(root, only_missing=True, stop=None)
        except Exception as exc:  # noqa: BLE001
            log("❌ 扫描失败 %s：%s" % (root, exc))
            continue

        if CONFIG["max_per_run"] > 0:
            items = items[: CONFIG["max_per_run"]]

        if not items:
            log("✅ %s 没有待处理视频" % root)
            continue

        log("发现 %d 个待处理视频" % len(items))

        for it in items:
            if STOP:
                break
            if it.has_zh and not CONFIG["overwrite"]:
                total_skip += 1
                continue
            try:
                subs = assrt.search(it.query)
                if not subs:
                    log("❌ 无结果：%s" % it.video)
                    total_fail += 1
                    continue

                ranked = [s for s in subs if core.score_sub(s, it.query, CONFIG["prefer"]) > -900]
                ranked.sort(key=lambda s: core.score_sub(s, it.query, CONFIG["prefer"]), reverse=True)
                if not ranked:
                    log("❌ 无匹配偏好的结果：%s" % it.video)
                    total_fail += 1
                    continue

                best = ranked[0]
                detail = assrt.detail(best.get("id"))
                packed = core.pick_pack_file(detail.get("filelist"), it.base, CONFIG["prefer"])

                if packed:
                    data, _ = core.http_download(packed.get("url", ""), token)
                    sub_name = packed.get("f") or "episode.srt"
                else:
                    key = str(best.get("id"))
                    entry = cache.get(key)
                    if entry is None:
                        raw, disp = core.http_download(detail.get("url", ""), token)
                        fname = detail.get("filename") or core.guess_name(detail.get("url", ""), disp)
                        entry = cache.put(key, raw, fname)
                        log("整包已缓存：%s（%d 个字幕文件）" % (fname, len(entry["files"])))
                    path = core.pick_cached_file(entry["files"], it.base, CONFIG["prefer"])
                    if not path:
                        raise core.AssrtError("整包里没有匹配这一集的字幕")
                    sub_name = os.path.basename(path)
                    with open(path, "rb") as f:
                        data = f.read()

                ext = os.path.splitext(sub_name)[1].lower()
                target = os.path.join(it.folder, "%s.%s%s" % (it.base, CONFIG["lang"], ext))

                if CONFIG["dry_run"]:
                    log("🧪 试运行：%s → %s" % (it.video, os.path.basename(target)))
                    total_ok += 1
                    continue

                with open(target, "wb") as f:
                    f.write(core.to_utf8(data))
                log("✅ %s → %s" % (it.video, os.path.basename(target)))
                total_ok += 1

            except Exception as exc:  # noqa: BLE001
                log("❌ %s：%s" % (it.video, exc))
                total_fail += 1

    cache.clear()
    log("本轮结束：成功 %d · 失败 %d · 跳过 %d" % (total_ok, total_fail, total_skip))


def main():
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    log("SubMatch Headless 启动")
    log("监控目录：%s" % ("、".join(CONFIG["dirs"]) or "（未配置）"))
    log("间隔：%d 分钟 · 偏好：%s · 后缀：%s · 覆盖：%s · 试运行：%s" % (
        CONFIG["interval"], CONFIG["prefer"], CONFIG["lang"],
        "是" if CONFIG["overwrite"] else "否", "是" if CONFIG["dry_run"] else "否"))
    log("Token：%s" % ("已配置" if CONFIG["token"] else "未配置"))

    if not CONFIG["dirs"]:
        log("❌ 没有配置 WATCH_DIRS，退出")
        return 2

    if CONFIG["run_on_start"]:
        run_once()

    while not STOP:
        log("休眠 %d 分钟…" % CONFIG["interval"])
        for _ in range(CONFIG["interval"] * 60):
            if STOP:
                break
            time.sleep(1)
        if STOP:
            break
        run_once()

    log("已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
