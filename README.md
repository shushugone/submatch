# SubMatch

给本地 / NAS 影视库**自动补中文字幕**的小工具。

- **macOS 版**：原生窗口，选目录 → 扫描 → 勾选 → 下载，字幕直接写到视频旁边
- **Docker 版**：跑在 NAS 上，带 Web 管理界面和定时任务，适合挂机长期维护片库

字幕源是 [Assrt（伪射手网）](https://assrt.net)，需要免费注册并获取 API Token。

---

## 功能

| 能力 | 说明 |
|---|---|
| 元数据解析 | 优先读 Kodi 格式 `.nfo`（title / year / showtitle / season / episode），没有 NFO 就从文件名解析 |
| 中文字幕搜索 | 调 Assrt API，按片名或 `SxxEyy` 搜索 |
| 语言偏好 | `中文优先` / `双语优先` / `不限`，**按字幕包内每个文件名**打分，不会在 chs / cht / chs&eng / eng 里乱挑 |
| 季包处理 | 有单集直链就只下这一集；没有就整包只下一次、缓存后按集复用 |
| 命名 | 按 Jellyfin 规则写成 `<视频名>.zh.srt`（后缀可改） |
| 已有字幕检测 | 文件名标记 + 内容汉字嗅探，默认跳过，不覆盖手调过的字幕 |
| 编码转换 | 自动识别 UTF-8 / GBK / Big5 / UTF-16，统一转 UTF-8 |
| 原盘过滤 | 自动跳过 `BDMV/`、`VIDEO_TS/` 里的 `00075.m2ts` 分段文件 |
| 定时任务 | Docker 版按分钟级间隔自动扫描，可配多个监控目录 |
| Web 管理 | Docker 版浏览器操作：列表、勾选下载、进度、日志、改配置 |

---

## 安装

### macOS

1. 从 [Releases](../../releases) 下载 `SubMatch-macOS-x.y.z.zip`
2. 解压，把 `SubMatch.app` 拖进 `/Applications`
3. 首次打开若提示「未验证的开发者」：右键 → 打开

要求：macOS 11+，Python 3（系统自带即可），Tk 8.6 以上（启动脚本会自动挑解释器；系统自带 Tk 8.5 会显示空白窗口，脚本已规避）。

也可以从源码构建：

```bash
git clone https://github.com/shushugone/submatch.git
cd submatch
./scripts/build-mac-app.sh     # 生成 dist/SubMatch.app
```

### Docker（NAS / Linux）

```yaml
services:
  submatch:
    build: .
    image: submatch:1.0
    container_name: submatch
    restart: unless-stopped
    user: "1000:1001"
    ports:
      - "8099:8099"
    environment:
      TZ: Asia/Shanghai
      ASSRT_TOKEN: "你的 32 位 Token"
      WATCH_DIRS: "/media/movie:/media/tv"
      INTERVAL_MINUTES: "360"
      PREFER: "zh"
      LANG_TAG: "zh"
      OVERWRITE: "0"
    volumes:
      - /path/to/media:/media
      - /path/to/config:/config
```

启动后打开 `http://<NAS_IP>:8099`。

也可以只构建镜像后跑（无 Web，纯定时）：

```bash
docker build -t submatch:1.0 .
docker run -d --name submatch --restart unless-stopped \
  -e ASSRT_TOKEN=xxx -e WATCH_DIRS=/media \
  -v /path/to/media:/media -v /path/to/config:/config submatch:1.0
```

---

## 使用

### 1. 拿 Assrt Token

登录 [assrt.net](https://assrt.net) → 用户面板 → **API 密钥（Token）**，32 位字符。

免费额度 **20 次/分钟**，本工具内置 3.2 秒间隔。

### 2. macOS 版

1. 先在 Finder 里连上 NAS（`smb://192.168.x.x`），挂载到 `/Volumes/...`
2. 打开 SubMatch，选媒体目录
3. 填 Token → 保存
4. 点「扫描」，列表里 ❌ 是缺字幕的，✅ 是已有
5. 勾选要处理的（⌘ 点加选、Shift 连选），点「开始下载」

列说明：

| 列 | 含义 |
|---|---|
| 字幕状态 | ✅ 视频同目录已有同名外挂字幕 / ❌ 没有 |
| 下载结果 | ✅ 已保存 / ❌ 失败原因 / ✅ 已有中字，跳过 |

### 3. Docker 版

浏览器打开 `http://<NAS_IP>:8099`，操作同 Mac 版；「设置」里可以改 Token、目录、间隔、偏好，保存到 `/config/config.json`。

---

## 配置项（Docker）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ASSRT_TOKEN` | 必填 | Assrt API Token |
| `WATCH_DIRS` | 必填 | 冒号分隔的容器内路径，如 `/media/movie:/media/tv` |
| `INTERVAL_MINUTES` | `360` | 自动扫描间隔（分钟） |
| `RUN_ON_START` | `1` | 启动先跑一轮 |
| `PREFER` | `zh` | `zh` 中文优先 / `bi` 双语优先 / `any` 不限 |
| `LANG_TAG` | `zh` | 生成文件的语言后缀，Jellyfin 认 `zh` |
| `OVERWRITE` | `0` | `1` = 覆盖已有中文字幕 |
| `ONLY_MISSING` | `1` | `1` = 默认只列缺字幕的 |
| `MAX_PER_RUN` | `0` | 每轮最多处理几部，0 = 不限 |
| `WEB_PORT` | `8099` | Web 端口 |
| `WEB_HOST` | `0.0.0.0` | Web 监听地址（本机预览可设 `127.0.0.1`） |

---

## 工作原理

```
扫描目录
  → 读 .nfo / 解析文件名，生成搜索词（片名 + 年份 / 剧名 + SxxEyy）
  → Assrt 搜索，按结果语种标签初筛
  → 取详情：有 filelist 就按 SxxEyy 挑单集直链
             没有就下整包 → 解压到临时目录 → 按集缓存复用
  → 包内按文件名打分（中文/双语/简繁/srt）挑出最合适的一个
  → 转 UTF-8，写成 <视频名>.<lang>.srt
```

临时文件在 `/tmp/submatch-pack-*`，一次任务结束自动清理。

---

## 常见问题

**搜不到 / 无结果？**
片名差异太大时会这样。把视频所在目录或 `.nfo` 里的 `<title>` 改成更通用的名字（如中文正式译名）再试。冷门片确实可能没人做字幕。

**为什么跳过某些视频？**
- 已有中文字幕（默认跳过，勾「覆盖已有中字」可重下）
- 蓝光原盘的 `BDMV/STREAM/00075.m2ts` 分段文件不是独立影片，会被过滤

**字幕文件名不对？**
默认后缀 `zh`，可在设置里改成 `zh-Hans` / `zh-CN` / `chs`。Jellyfin 只认 ISO 语言码。

**Docker 写入权限报错？**
compose 里的 `user: "1000:1001"` 要和你 NAS 上媒体目录的属主一致，可用 `ls -ln` 查看。

---

## 项目结构

```
core/submatch_core.py     核心逻辑（扫描、搜索、选文件、写入）
mac/submatch.py           macOS GUI（Tk）
mac/Info.plist            应用信息
docker/submatch_web.py    Web 服务 + 定时调度
docker/submatch_headless.py  纯命令行定时版
scripts/                  同步核心、构建 app、打 release 包
```

改完 `core/` 后执行 `./scripts/sync-core.sh`，把核心同步到 `mac/` 和 `docker/`。

打包发布：

```bash
./scripts/build-release.sh 1.0.0
# dist/SubMatch-macOS-1.0.0.zip
# dist/submatch-docker-1.0.0.zip
```

---

## 免责声明

- 本工具只做「搜索并下载字幕文件」，不提供、不托管任何字幕内容
- 字幕版权归原作者所有，请遵守 [Assrt](https://assrt.net) 的使用条款，仅用于个人学习与已获授权的场景
- 请勿用于商业用途或二次分发

## License

[MIT](LICENSE)
