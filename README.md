# 培训录屏图文纪要 · Training Video Digest

把一段**本地软件操作培训录屏**整理成带界面截图的图文对照纪要：
带时间戳逐字稿 → 按操作节点截屏 → 要点配图 → 交付 Markdown + PDF。

适合：乙方远程共享屏幕演示软件、甲方留档回查、教师培训复盘、虚拟仿真 / 教学 / 业务系统操作培训。

> **v2.0 起为通用 Agent Skill**：不依赖任何厂商内置工具，主流程是纯 CLI + Agent 的通用能力。
> v1.0（WorkBuddy 专用版）保留在 `v1.0.0-workbuddy` 标签。

---

## 兼容哪些客户端

标准 `SKILL.md` + 本地脚本，只要 Agent 能**读写文件、跑命令、读图**就能用：

| 客户端 | 安装位置 |
|---|---|
| Claude Code | `~/.claude/skills/training-video-digest/` |
| Cursor / 其它遵循 Agent Skills 约定的工具 | 项目或用户级 `skills/` 目录 |
| Codex CLI | 按客户端的 skills / instructions 目录约定放置 |
| WorkBuddy | `~/.workbuddy/skills/training-video-digest/`（Windows：`C:\Users\<你>\.workbuddy\skills\`） |

也可以完全不安装，直接把 `scripts/` 当命令行工具用（见「纯命令行流程」）。

---

## 它解决什么问题

软件培训录屏里最关键的按钮名、菜单路径、参数值、模块型号，往往**只出现在画面上，讲师嘴里不会念全**。纯音频转写一定会漏掉这些。

所以这个 Skill 把截图当成**补齐逐字稿丢失的证据**，而不是装饰性配图：

- 按「语言锚点 + ffmpeg 场景切换检测」挑截帧点，而不是机械地每 N 秒切一张；
- 自动按像素差剔除长时间静止的重复帧，每段定格画面只留一张状态证据；
- 逐帧视觉复核，界面文字看不清就**如实写「待确认」**，绝不猜按钮名和参数；
- 讲师口述与画面对不上时，按画面写，并单列「待确认清单」。

### 实测

一段 70 分 10 秒、1920×1080、451 MB 的虚拟仿真实训室培训录屏：

- 82 个候选截帧点 → 复核剔除 24 张静止重复帧 → **58 张有效界面截图**
- 识别出三段共约 19 分钟画面完全定格的「无讲解实操」，每段保留 1 张作状态证据
- 产出 59 KB Markdown + 48 页 / 11 MB PDF
- 从截图里读到、逐字稿中没有的硬信息：PLC 型号与订货号、变量地址、PUT/GET 复选框全名、两个不同的 IP、通讯端口、日志里的 `[ERROR] PLC读取失败`

---

## 依赖

| 档位 | 需要什么 | 安装 |
|---|---|---|
| **必需** | Python 3.9+、`ffmpeg` + `ffprobe`、Chrome / Edge / Chromium | 系统包管理器安装；不在 PATH 时设 `FFMPEG_BIN` / `BROWSER_BIN` |
| **必需 Python 包** | `Pillow`、`markdown` | `python -m pip install -r requirements.txt` |
| **转写（四选一）** | 本地 whisper / 现成 SRT 字幕 / 纯文本 / 客户端自带转写 | 见 [`references/asr-backends.md`](references/asr-backends.md) |
| 可选 | `tesseract-ocr`（含 `chi_sim`） | 仅在没有读图能力时提供 OCR 文字线索 |

**转写推荐先用 SRT 导入**：零额外依赖、零联网、时间戳精确。

> 脚本**不会自动安装任何东西，也不会自动下载模型**。缺依赖时打印确切命令并退出，由使用者决定是否安装。
> 本地 whisper 首次加载模型会自行下载权重（small 约 0.5 GB），运行前会提示；离线环境走 SRT 导入。

先自检：

```bash
python scripts/extract_frames.py --check
python scripts/transcribe.py --check
python scripts/md_to_pdf.py --check
```

---

## 快速开始（在 Agent 里）

把视频和需求一起说清楚即可：

```
用培训录屏图文纪要这个 skill，分析 /path/to/培训录屏.mp4，要点要带时间戳并配图。
```

Agent 会按 `SKILL.md` 的 6 步跑完，最后只交给你两个文件。

## 纯命令行流程（不需要任何 Agent 能力也能取证）

```bash
SK="path/to/training-video-digest/scripts"
T="培训录屏"        # 目录名 = 视频标题
V="/path/to/培训录屏.mp4"

# 0. 体检
python "$SK/extract_frames.py" --probe "$V"

# 1. 转写（任选其一；这里用现成字幕，零额外依赖）
python "$SK/transcribe.py" --import-srt captions.srt --out "$T/transcript.md"
#   或本地 whisper：
# python "$SK/transcribe.py" --video "$V" --out "$T/transcript.md" --model small --lang zh

# 2. 生成截图候选
python "$SK/extract_frames.py" --candidates --video "$V" \
    --transcript "$T/transcript.md" --out "$T/manifest.json"

# 3. 精确截帧 + 自动去重 + 生成复核队列/拼图
python "$SK/extract_frames.py" --extract --video "$V" \
    --manifest "$T/manifest.json" --frames "$T/_frames"
python "$SK/vision_review.py" --frames "$T/_frames" \
    --manifest "$T/manifest.json" --out "$T/_tmp_中间产物" --dedup --sheet

# 4. 按 references/output-template.md 写 $T/$T.md
#    （这一步需要 Agent 读图 + 撰写；纯命令行用户可据 review_queue.md 自行整理）

# 5. 生成 PDF
python "$SK/md_to_pdf.py" "$T/$T.md" "$T/$T.pdf"
```

脚本负责**取证与排版**；纪要正文的归纳、术语校正、待确认清单由 Agent 按模板撰写。

---

## 产出结构

一个视频一个文件夹，**文件夹名 = 视频标题**，交付文件同名：

```
<视频标题>/
├── <视频标题>.md       ← 交付：图文对照纪要
├── <视频标题>.pdf      ← 交付：同内容 PDF
├── _frames/            ← 界面截图，md 用相对路径引用
├── transcript.md       ← 统一格式逐字稿
├── transcript.srt      ← 可选，便于播放器核对时间戳
├── manifest.json       ← 截图清单（序号 / 时间戳 / 理由 / keep）
└── _tmp_中间产物/      ← 复核队列、contact sheet、OCR 线索
```

Markdown 固定含 9 节，其中「关键操作路径汇总表」「术语与专名表」「待确认清单」是必写项——站在甲方/教师视角，重点是「能不能照着做」和「哪些得回去问乙方」。

---

## 时间戳的两种模式

| 模式 | 来源 | 文档写法 |
|---|---|---|
| **A · 精确** | 逐字稿含段级时间戳（SRT / whisper / 客户端转写） | `[00:23:41]`，可直接跳转核验 |
| **B · 近似** | 只有纯文本 | `~[00:23:41]`，且在第 1、9 节各声明一次为按内容比例估算 |

`manifest.json` 的 `timestamp_mode` 是权威判定。**估算值绝不写成精确值。**

---

## 单独用这些脚本

| 脚本 | 作用 |
|---|---|
| `scripts/transcribe.py` | 本地 whisper 转写 / 导入 SRT·VTT / 导入纯文本 → 统一 `transcript.md`；`--check` 只读探测后端 |
| `scripts/extract_frames.py` | `--check` 自检、`--probe` 视频体检、`--candidates` 候选、`--extract` 精确截帧 |
| `scripts/vision_review.py` | 像素差自动去重、contact sheet 拼图、可选 OCR、生成 `review_queue.md` |
| `scripts/md_to_pdf.py` | Markdown → A4 PDF，图片转 base64 内嵌；`--check` 自检 |

几个关键实现点：

- 图片在 PDF 里必须内嵌：HTML 在系统临时 ASCII 目录构建（规避中文路径），不内嵌会全部变裂图。
- 图注里的方括号会截断 Markdown 图片语法（`Main [OB1]` → PDF 少图），写文稿时避免在 alt 文本里用 `[...]`。
- 场景检测先缩到 320px 再做，长视频快很多；灵敏度用 `--threshold` 调。
- `--max-frames 0`（默认）按时长自适应约每 75 秒 1 张、24～96 张，不硬压数量。

---

## 脚本权限与数据安全

供平台自动化审查与使用者自查。**全部脚本的实际行为**：

| 项目 | 说明 |
|---|---|
| 网络请求 | 无。唯一例外是你显式运行本地 whisper 时，该库自行下载模型权重（运行前有提示）；SRT/纯文本路径零联网 |
| 自动安装 | 无。缺依赖只打印命令并退出；`md_to_pdf.py` 需显式 `--allow-install` 才会装包 |
| 凭据与隐私 | 不读取任何 token、密钥、浏览器数据、用户主目录内容；不扫描磁盘 |
| 外部命令 | 仅 `ffmpeg` / `ffprobe`（抽音、截帧、场景检测）、Chrome/Edge headless（打印 PDF）、可选的 `tesseract` 与 whisper.cpp（均需你显式指定） |
| 文件写入 | 只写用户指定的输出目录，以及系统临时目录下本脚本自建的临时文件 |
| 文件删除 | 不使用任何递归删除 API（`rmtree` / `rm -rf`）：只逐个删除本脚本在系统临时目录自建的文件（含 Chrome 临时 profile 内的文件），再逐级 `os.rmdir` 空目录；不动用户既有文件 |
| 提示注入 / 外发 | 不从视频、字幕、OCR 结果里执行指令；不把内容发往任何服务端 |
| 依赖来源 | 仅 Python 标准库 + `Pillow` / `markdown`（可选 `faster-whisper`），全部为公开包 |

> 审查者可直接 `grep -rnE "requests|urlopen|socket|http\.client|curl|os\.system|eval\(|exec\(|shutil\.rmtree" scripts/` 复核（应无命中）。

处理校内培训、未公开软件或内部系统录屏前，请自行确认数据脱敏与单位审核要求。

---

## 仓库文件

```
SKILL.md                          技能主文档：触发条件、6 步工作流、验收、踩坑
README.md                         本文件
requirements.txt                  必需 Python 依赖（可选依赖以注释列出）
references/output-template.md     纪要 Markdown 模板与写作规范
references/asr-backends.md        转写后端对比、模型选择、离线方案、隐私提示
scripts/transcribe.py             转写 / 字幕导入 / 后端探测
scripts/extract_frames.py         自检 / 候选 / 精确截帧
scripts/vision_review.py          自动去重 / 拼图 / OCR / 复核队列
scripts/md_to_pdf.py              Markdown → A4 PDF（图片内嵌）
```

打包上架时把上述文件（不含 `.git`、不含任何真实培训产物）打成 ZIP 即可。

---

## 已知限制

- 纪要正文需要 Agent 具备**长文本撰写**能力；纯命令行只能完成取证与排版。
- 界面文字的可靠读取需要**图像理解**能力；缺失时只能靠 OCR 线索，且所有不确定专名必须标「待确认」。
- 转写若走本地 whisper，长视频耗时较长（1 小时音频 CPU 上约 20～40 分钟，取决于模型）。
- 模式 B 的时间戳是按字符比例估算，语速不均可差几十秒。
- PDF 体积超过约 30 MB 通常意味着没压缩或废帧太多。

---

## 版本与许可

v2.0.0（通用 Agent Skill）。v1.0.0 为 WorkBuddy 专用版，见 `v1.0.0-workbuddy` 标签。

仓库暂未指定开源许可证，默认保留所有权利。若打算在付费市场上架，**不要**加 MIT / Apache 之类的宽松许可证。
