#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SubMatch — 扫描媒体目录，按 NFO / 文件名去 Assrt 搜中文字幕，
按 Jellyfin 规则重命名（<视频名>.zh.srt）写回同目录。

只依赖 macOS 自带 Python 3 与 bsdtar，无需 pip 安装任何东西。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

APP_NAME = "SubMatch"
CFG_DIR = os.environ.get("SUBMATCH_CONFIG_DIR") or os.path.expanduser("~/Library/Application Support/SubMatch")
CFG_PATH = os.path.join(CFG_DIR, "config.json")
API_BASE = "https://api.assrt.net/v1"

VIDEO_EXT = {
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".m2ts",
    ".wmv", ".flv", ".webm", ".mpg", ".mpeg", ".rmvb",
}
SUB_EXT = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
SKIP_DIRS = {
    "extras", "featurettes", "sample", "samples", "trailers", "@eadir",
    "behind the scenes", "bdmv", "video_ts", "certificate", "playlist", "clipinf",
}

DISC_MARKERS = ("/bdmv/", "/video_ts/", "/certificate/")


def is_disc_segment(path):
    """蓝光/DVD 目录里的分段文件（00075.m2ts 这类）不是独立影片。"""
    low = path.lower().replace("\\", "/")
    if any(marker in low for marker in DISC_MARKERS):
        return True
    return bool(re.fullmatch(r"\d{5}\.(m2ts|mts|vob)", os.path.basename(low)))

QUALITY_RE = re.compile(
    r"\b(2160p|1080p|720p|480p|4k|uhd|bluray|blu-ray|bdrip|brrip|web-?dl|webrip|web|hdtv|"
    r"h\.?264|h\.?265|hevc|x264|x265|avc|aac|ac3|dts|ddp?5\.1|truehd|atmos|remux|hdr|dovi|"
    r"10bit|8bit|yts(\.\w+)?|galaxyrg|tgx|rarbg|proper|repack|extended|remastered)\b",
    re.I,
)


# ---------------------------------------------------------------- 配置

def load_config():
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 元数据

def read_text(path):
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _tag(text, name):
    m = re.search(r"<%s[^>]*>(.*?)</%s>" % (name, name), text, re.S | re.I)
    if not m:
        return ""
    return re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1), flags=re.S).strip()


def parse_nfo(folder, video=None):
    """读 NFO：剧集优先 tvshow.nfo 拿剧名，单集 nfo 只取 season/episode。"""
    info = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return info
    lower = {n.lower(): n for n in names}

    def read(name):
        try:
            return read_text(os.path.join(folder, name))
        except OSError:
            return ""

    if "tvshow.nfo" in lower:
        txt = read(lower["tvshow.nfo"])
        title = _tag(txt, "title") or _tag(txt, "showtitle")
        if title:
            info["showtitle"] = title
        year = _tag(txt, "year") or _tag(txt, "premiered")[:4]
        if year:
            info["year"] = year

    if "movie.nfo" in lower:
        txt = read(lower["movie.nfo"])
        info.setdefault("title", _tag(txt, "title"))
        info.setdefault("year", _tag(txt, "year"))

    if video:
        vn = os.path.splitext(video)[0].lower() + ".nfo"
        if vn in lower:
            txt = read(lower[vn])
            for key in ("season", "episode"):
                val = _tag(txt, key)
                if val:
                    info[key] = val

    if not info.get("title") and not info.get("showtitle"):
        for name in names:
            if not name.lower().endswith(".nfo"):
                continue
            if video and name.lower() == os.path.splitext(video)[0].lower() + ".nfo":
                continue
            txt = read(name)
            title = _tag(txt, "title")
            if title:
                info["title"] = title
                year = _tag(txt, "year")
                if year:
                    info["year"] = year
                break
    return info


def clean_name(filename):
    """从文件名推标题 / 年份 / SxxEyy。"""
    base = os.path.splitext(filename)[0]
    base = base.replace("_", " ").replace(".", " ")
    season = episode = None
    m = re.search(r"[Ss](\d{1,2})\s*[Ee](\d{1,3})", base)
    if m:
        season, episode = int(m.group(1)), int(m.group(2))
    year = ""
    ym = re.search(r"\b(19\d{2}|20\d{2})\b", base)
    if ym:
        year = ym.group(1)
    title = base
    if ym:
        title = base[: ym.start()]
    title = re.sub(r"\b[Ss]\d{1,2}\s*[Ee]\d{1,3}\b", " ", title)
    title = re.sub(r"\b\d{1,2}x\d{2,3}\b", " ", title)
    title = QUALITY_RE.sub(" ", title)
    title = re.sub(r"[\[\](){}]", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -")
    return {"title": title, "year": year, "season": season, "episode": episode}


def folder_title(folder):
    folder = folder.rstrip("/")
    name = os.path.basename(folder)
    if re.match(r"^(season\s*\d+|specials?|s\d{1,2})$", name, re.I):
        name = os.path.basename(os.path.dirname(folder))
    name = re.sub(r"\s*\((19|20)\d{2}\)\s*$", "", name)
    name = re.sub(r"\s*\[[^\]]*\]\s*", " ", name)
    name = name.replace(".", " ").replace("_", " ")
    return re.sub(r"\s+", " ", name).strip()


def build_query(folder, filename):
    info = parse_nfo(folder, filename)
    fallback = clean_name(filename)
    season = fallback["season"] or info.get("season")
    episode = fallback["episode"] or info.get("episode")
    if season and episode:
        try:
            title = info.get("showtitle") or folder_title(folder) or fallback["title"]
            return "%s S%02dE%02d" % (title, int(season), int(episode)), title
        except (TypeError, ValueError):
            pass
    title = info.get("title") or folder_title(folder) or fallback["title"]
    year = info.get("year") or fallback["year"]
    return ("%s %s" % (title, year)).strip(), title


# ---------------------------------------------------------------- 字幕探测

ZH_TOKENS = {
    "zh", "chs", "cht", "chi", "zho", "zh-hans", "zh-cn", "zh-tw", "zh-hant",
    "cn", "sc", "tc", "简", "繁", "中", "简体", "繁体", "双语", "中文",
}


def subtitle_files(folder, video):
    base = os.path.splitext(video)[0].lower()
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        low = name.lower()
        if low.startswith(base + ".") and os.path.splitext(low)[1] in SUB_EXT:
            out.append(name)
    return out


def _has_chinese_text(path, limit=400 * 1024):
    try:
        if os.path.getsize(path) > 8 * 1024 * 1024:
            return False
        with open(path, "rb") as f:
            data = f.read(limit)
    except OSError:
        return False
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return False
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk >= 20


def is_chinese_subtitle(folder, name):
    stem = os.path.splitext(name.lower())[0]
    parts = re.split(r"[.\s_\-\[\]()]+", stem)
    if any(p in ZH_TOKENS for p in parts):
        return True
    return _has_chinese_text(os.path.join(folder, name))


def has_subtitle(folder, video):
    return bool(subtitle_files(folder, video))


def subtitle_state(folder, video):
    """返回 (有无字幕, 有无中文字幕, 字幕文件列表)。"""
    files = subtitle_files(folder, video)
    if not files:
        return False, False, []
    zh = any(is_chinese_subtitle(folder, n) for n in files)
    return True, zh, files


def is_sample(name):
    low = name.lower()
    return "sample" in low and os.path.splitext(low)[1] in VIDEO_EXT


# ---------------------------------------------------------------- Assrt

CURL = "/usr/bin/curl"


def curl_get(url, timeout=60, retries=3):
    """用系统 curl 发请求。

    Assrt 的 TLS 指纹会拒绝 Python/OpenSSL 的握手（SSL: UNEXPECTED_EOF），
    但 curl 正常，所以统一走 curl 当传输层。
    """
    last = ""
    for i in range(retries):
        proc = subprocess.run(
            [CURL, "-sS", "-L", "--max-time", str(timeout),
             "-H", "User-Agent: SubMatch/1.0",
             "-H", "Referer: https://assrt.net/",
             "-w", "\n%{http_code}", url],
            capture_output=True,
        )
        if proc.returncode == 0:
            body, _, code = proc.stdout.rpartition(b"\n")
            code = code.decode("ascii", "ignore").strip()
            if code.startswith("2"):
                return body
            last = "HTTP %s：%s" % (code, body[:160].decode("utf-8", "replace"))
            if code in ("400", "401", "403", "404"):
                break
        else:
            last = proc.stderr.decode("utf-8", "replace").strip()[:160] or "curl 退出码 %d" % proc.returncode
        time.sleep(1.5 * (i + 1))
    raise AssrtError(last or "请求失败")


class AssrtError(Exception):
    pass


class Assrt:
    def __init__(self, token, min_interval=3.2):
        self.token = (token or "").strip()
        self.min_interval = min_interval
        self._last = 0.0

    def _get(self, path, **params):
        if not self.token:
            raise AssrtError("没有填 Assrt Token")
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        params["token"] = self.token
        query = "&".join("%s=%s" % (k, urllib.parse.quote(str(v), safe="")) for k, v in params.items())
        url = API_BASE + path + "?" + query
        try:
            payload = json.loads(curl_get(url, timeout=30).decode("utf-8", "replace"))
        except AssrtError as e:
            brief = {k: v for k, v in params.items() if k != "token"}
            raise AssrtError("%s %s → %s" % (path, brief, e))
        except Exception as e:  # noqa: BLE001
            raise AssrtError("返回内容无法解析：%s" % e)
        finally:
            self._last = time.time()
        if payload.get("status") != 0:
            raise AssrtError("Assrt 返回错误：%s" % payload.get("status"))
        return payload

    def search(self, query, count=15):
        data = self._get("/sub/search", q=query, cnt=count, pos=0)
        raw = (data.get("sub") or {}).get("subs") or []
        subs = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            sub = normalize_item(item)
            if sub["id"]:
                subs.append(sub)
        return subs

    def detail(self, sub_id):
        try:
            sub_id = int(sub_id)
        except (TypeError, ValueError):
            raise AssrtError("搜索结果缺少 id，跳过")
        data = self._get("/sub/detail", id=sub_id)
        subs = (data.get("sub") or {}).get("subs") or []
        if not subs:
            raise AssrtError("详情为空")
        return subs[0]


def normalize_item(item):
    """Assrt 有新旧两种返回结构，统一成一套字段。"""
    lang = item.get("lang")
    if isinstance(lang, dict):
        desc = lang.get("desc") or ""
    else:
        desc = item.get("m_lang") or ""
    name = item.get("video_chinese_name") or item.get("sub_name") or item.get("native_name") or ""
    return {
        "id": item.get("fileid") or item.get("id"),
        "native_name": name,
        "videoname": item.get("videoname") or item.get("m_videoname") or "",
        "subtype": item.get("m_subtype") or item.get("subtype") or "",
        "vote_score": item.get("vote_score") or item.get("score") or item.get("down_count") or 0,
        "desc": desc,
    }


def score_sub_name(name, prefer="zh", hint=""):
    """按字幕文件名判断语言类型与集数匹配度。"""
    n = (name or "").lower()
    score = 0
    if hint:
        m = re.search(r"[Ss](\d{1,2})\s*[Ee](\d{1,3})", hint)
        if m:
            tag = "s%02de%02d" % (int(m.group(1)), int(m.group(2)))
            short = "e%02d" % int(m.group(2))
            if tag in n:
                score += 10
            elif short in n:
                score += 4
            else:
                return -999
    bi = ("&eng" in n) or ("eng" in n) or ("双语" in n) or ("bilingual" in n)
    zh_mark = any(t in n for t in ("chs", "cht", "ch.", "ch&", ".ch", "简", "繁", "中"))
    if prefer == "bi":
        if not (bi and zh_mark):
            return -999
        score += 8
        if "chs" in n or "简" in n:
            score += 1
    elif prefer == "zh":
        if bi:
            score -= 2
        if "chs" in n or "简" in n:
            score += 4
        elif "cht" in n or "繁" in n:
            score += 2
        elif "ch" in n or "中" in n:
            score += 1
    else:
        if bi or "ch" in n:
            score += 1
    if n.endswith(".srt"):
        score += 2
    elif n.endswith((".ass", ".ssa")):
        score += 1
    return score


def pick_pack_file(filelist, hint, prefer="zh"):
    """季包里按 SxxEyy + 语言偏好挑出最合适的那一条直链。"""
    if not isinstance(filelist, list) or not filelist:
        return None
    best, best_score = None, -999
    for f in filelist:
        score = score_sub_name(f.get("f") or "", prefer, hint)
        if score > best_score:
            best, best_score = f, score
    return best if best_score > -900 else None


def lang_flags(desc):
    text = (desc or "").replace("&nbsp;", " ")
    return {
        "zh": any(w in text for w in ("简", "繁", "中")),
        "bi": "双语" in text,
        "en": "英" in text,
    }


def score_sub(sub, query, prefer="zh"):
    flags = lang_flags(sub.get("desc") or sub.get("lang") or "")
    score = 0
    if prefer == "zh":
        if not flags["zh"]:
            return -999
        score += 6 if not flags["bi"] else 4
    elif prefer == "bi":
        if not flags["bi"]:
            return -999
        score += 8
    else:
        if flags["zh"]:
            score += 2
    subtype = str(sub.get("subtype", "")).lower()
    if "srt" in subtype or "subrip" in subtype:
        score += 2
    elif "ass" in subtype or "ssa" in subtype:
        score += 1
    try:
        score += min(int(sub.get("vote_score") or 0) // 20, 5)
    except (TypeError, ValueError):
        pass
    q_words = [w.lower() for w in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", query) if len(w) > 2]
    hay = " ".join(str(sub.get(k, "")) for k in ("native_name", "videoname", "desc")).lower()
    score += sum(1 for w in q_words if w in hay)
    return score


def http_download(url, token):
    candidates = [url]
    if token and "token=" not in url:
        sep = "&" if "?" in url else "?"
        candidates.append(url + sep + "token=" + urllib.parse.quote(token))
    last = None
    for u in candidates:
        try:
            return curl_get(u, timeout=120, retries=2), ""
        except AssrtError as e:  # noqa: BLE001
            last = e
    raise AssrtError("下载失败：%s" % last)


def guess_name(url, disposition):
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disposition or "", re.I)
    if m:
        return urllib.parse.unquote(m.group(1))
    return os.path.basename(urllib.parse.urlparse(url).path) or "subtitle.bin"


def extract_subtitles(data, filename):
    """返回 [(名字, 字节)]，支持 srt/ass 直出，zip 与 rar/7z（bsdtar）。"""
    ext = os.path.splitext(filename)[1].lower()
    if ext in SUB_EXT:
        return [(filename, data)]

    tmp = tempfile.mkdtemp(prefix="submatch-")
    path = os.path.join(tmp, filename or "archive.bin")
    with open(path, "wb") as f:
        f.write(data)

    try:
        if ext == ".zip":
            with zipfile.ZipFile(path) as zf:
                zf.extractall(tmp)
        else:
            bsdtar = shutil.which("bsdtar") or "/usr/bin/bsdtar"
            proc = subprocess.run(
                [bsdtar, "-xf", path, "-C", tmp],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode != 0:
                raise AssrtError("解包失败：%s" % (proc.stderr.strip()[:200] or "未知错误"))
        found = []
        for root, _dirs, files in os.walk(tmp):
            for name in files:
                if os.path.splitext(name)[1].lower() in SUB_EXT:
                    full = os.path.join(root, name)
                    with open(full, "rb") as f:
                        found.append((name, f.read()))
        if not found:
            raise AssrtError("压缩包里没有字幕文件")
        return found
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def extract_files(data, filename, dest):
    """把字幕/压缩包解到 dest，返回字幕文件路径列表（不删目录）。"""
    os.makedirs(dest, exist_ok=True)
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in SUB_EXT:
        path = os.path.join(dest, os.path.basename(filename) or "subtitle.srt")
        with open(path, "wb") as f:
            f.write(data)
        return [path]

    path = os.path.join(dest, os.path.basename(filename) or "archive.bin")
    with open(path, "wb") as f:
        f.write(data)
    if ext == ".zip":
        with zipfile.ZipFile(path) as zf:
            zf.extractall(dest)
    else:
        bsdtar = shutil.which("bsdtar") or "/usr/bin/bsdtar"
        proc = subprocess.run([bsdtar, "-xf", path, "-C", dest], capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise AssrtError("解包失败：%s" % (proc.stderr.strip()[:160] or "未知错误"))
    found = []
    for root, _dirs, files in os.walk(dest):
        for name in files:
            if os.path.splitext(name)[1].lower() in SUB_EXT:
                found.append(os.path.join(root, name))
    if not found:
        raise AssrtError("压缩包里没有字幕文件")
    return found


def pick_cached_file(paths, hint, prefer="zh"):
    """在已解压的整包文件里按 SxxEyy + 语言偏好选。"""
    if not paths:
        return None
    best, best_score = None, -999
    for path in paths:
        score = score_sub_name(os.path.basename(path), prefer, hint)
        if score > best_score:
            best, best_score = path, score
    if best_score > -900:
        return best
    return max(paths, key=lambda p: os.path.getsize(p))


class PackCache:
    """同一部剧的整包只下载解压一次，后续各集复用。"""

    def __init__(self):
        self.items = {}

    def get(self, key):
        return self.items.get(key)

    def put(self, key, data, filename):
        tmp = tempfile.mkdtemp(prefix="submatch-pack-")
        files = extract_files(data, filename, tmp)
        self.items[key] = {"dir": tmp, "files": files}
        return self.items[key]

    def clear(self):
        for item in self.items.values():
            shutil.rmtree(item["dir"], ignore_errors=True)
        self.items.clear()


def pick_subtitle(files, prefer="zh"):
    def key(item):
        return (score_sub_name(item[0], prefer), len(item[1]))
    return max(files, key=key)


def to_utf8(data):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16"):
        try:
            return data.decode(enc).encode("utf-8")
        except UnicodeDecodeError:
            continue
    return data


# ---------------------------------------------------------------- 扫描

class Item:
    def __init__(self, folder, video):
        self.folder = folder
        self.video = video
        self.base = os.path.splitext(video)[0]
        self.query, self.title = build_query(folder, video)
        self.has_sub, self.has_zh, self.sub_files = subtitle_state(folder, video)
        if self.has_zh:
            self.status = "已有中字"
        elif self.has_sub:
            self.status = "已有字幕"
        else:
            self.status = "待处理"
        self.note = self.sub_files[0] if self.sub_files else ""
        self.done = False

    @property
    def path(self):
        return os.path.join(self.folder, self.video)


def scan(root, only_missing=True, progress=None, stop=None):
    items = []
    dirs = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if stop is not None and stop.is_set():
            return items
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d.lower() not in SKIP_DIRS
            and not os.path.islink(os.path.join(dirpath, d))
        ]
        dirs += 1
        if progress and dirs % 4 == 0:
            progress(dirs, len(items))
        videos = [f for f in filenames if os.path.splitext(f)[1].lower() in VIDEO_EXT and not f.startswith(".")]
        if not videos:
            continue
        for video in videos:
            if is_sample(video) or is_disc_segment(os.path.join(dirpath, video)):
                continue
            if only_missing and has_subtitle(dirpath, video):
                continue
            items.append(Item(dirpath, video))
    return items


# ---------------------------------------------------------------- GUI
