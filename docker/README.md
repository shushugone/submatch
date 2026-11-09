# SubMatch（Docker + Web 管理界面）

定时扫描媒体目录，自动补中文字幕；浏览器里可以看列表、勾选下载、改参数。

访问：`http://<NAS_IP>:8099`

## 文件

```
Dockerfile              构建镜像
docker-compose.yml      飞牛上直接用的编排文件
submatch_core.py        核心逻辑（与 Mac 版同源）
submatch_web.py         Web 服务 + 定时调度
submatch_headless.py    纯命令行版（可选，不需要界面时用）
```

## 部署（飞牛）

1. 把整个 `docker` 文件夹传到 NAS，例如 `/vol1/1000/docker/submatch`
2. **Docker → Compose → 新建项目**
   - 项目名：`submatch`
   - 路径：`/vol1/1000/docker/submatch`
   - Compose 文件：`docker-compose.yml`
3. 改 `docker-compose.yml` 三处：
   - `ASSRT_TOKEN`：你的 32 位 token
   - `WATCH_DIRS`：容器内路径
   - `volumes` 左侧：NAS 真实路径
4. 构建并启动，浏览器打开 `http://192.168.2.2:8099`

构建要拉基础镜像 + apt 装 curl，首次约 2–5 分钟。

## 路径映射

容器内只有一个 `/media` 根：

| NAS | 容器 |
|---|---|
| `/vol3/1000/Data/Nas_tool` | `/media` |
| `.../movie`、`.../tv`、`.../link` | `/media/movie`、`/media/tv`、`/media/link` |

```yaml
WATCH_DIRS: "/media/movie:/media/tv"
volumes:
  - /vol3/1000/Data/Nas_tool:/media
```

## 界面功能

| 区域 | 说明 |
|---|---|
| 扫描 | 按当前目录重新扫描；勾「显示已有字幕」会把已有字幕的片也列出来 |
| 下载选中 | 勾选若干行后只下载这些；不选＝全部 |
| 全选 / 清空 | 批量选择 |
| 进度条 | 下载时按「已完成/总数」推进 |
| 字幕状态 | ✅ 同目录已有同名外挂字幕 / ❌ 没有 |
| 下载结果 | ✅ 已保存 xxx.srt / ❌ 失败原因 / ✅ 已有中字，跳过 |
| 设置 | 改 token、间隔、偏好、后缀、目录、覆盖开关，保存后生效 |

## 参数（也可在界面里改）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ASSRT_TOKEN` | 必填 | assrt.net 的 API Token |
| `WATCH_DIRS` | 必填 | 冒号分隔的目录 |
| `INTERVAL_MINUTES` | 360 | 自动扫描间隔（分钟） |
| `RUN_ON_START` | 1 | 启动先跑一轮 |
| `PREFER` | zh | `zh` 中文优先 / `bi` 双语优先 / `any` 不限 |
| `LANG_TAG` | zh | 生成文件的语言后缀 |
| `OVERWRITE` | 0 | 1=覆盖已有中文字幕 |
| `ONLY_MISSING` | 1 | 1=默认只列缺字幕的 |
| `MAX_PER_RUN` | 0 | 每轮上限，0=不限 |
| `WEB_PORT` | 8099 | Web 端口 |

界面里保存的设置会写到 `/config/config.json`，重启后仍生效，并覆盖同名环境变量。

## 日志

飞牛 Docker → 容器 `submatch` → 日志，或界面底部「运行日志」。正常长这样：

```
[16:31:36] SubMatch Web 启动，端口 8099
[16:31:36] 扫描 /media …
[16:31:36] 扫描完成：1 个
[16:31:49] ✅ Buddy (2026) - 1080p.mp4 → Buddy (2026) - 1080p.zh.srt
[16:31:49] 本轮结束：成功 1 · 失败 0 · 跳过 0
```

## 注意

- 定时任务按 `INTERVAL_MINUTES` 循环，间隔改动从下次保存后重新计时
- 字幕写进视频同目录，不动视频文件
- 默认跳过已有中文字幕，重复跑不会覆盖手调过的字幕
- 无登录鉴权，仅建议在局域网内使用
