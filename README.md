# 培训录屏图文纪要 · Training Video Digest

把一段**本地软件操作培训录屏**整理成带界面截图的图文对照纪要：逐字稿 → 按操作节点截屏 → 要点带时间戳并配图 → 交付 Markdown + PDF。

适合：乙方远程共享屏幕演示软件、甲方需要留档回查、教师培训复盘、虚拟仿真 / 教学 / 业务系统操作培训。

---

## ⚠️ 先读：这是一个 WorkBuddy Skill

本仓库是 [WorkBuddy](https://www.workbuddy.cn/) 的 Skill（技能包），**不是一个开箱即用的独立 CLI 程序**。完整流程依赖 WorkBuddy 提供的能力：

| 环节 | 依赖 |
|---|---|
| 长视频转写（含中文、说话人分离、段级时间戳） | WorkBuddy 内置 `AudioTranscribe`，经 `ToolSearch` + `DeferExecuteTool` 调用 |
| 截图内容识别（读界面上的按钮名 / 参数值） | WorkBuddy 内置 `ViewImage` 视觉理解 |
| 交付 | WorkBuddy 内置 `present_files` |
| 截帧 / 场景检测 | 本机 `ffmpeg` + `ffprobe` |
| PDF 渲染 | Chrome / Edge headless |
| Python | 3.x，`markdown` / `Pillow` / `pypdf` |

**也就是说**：脱离 WorkBuddy，直接 `python scripts/xxx.py` 是跑不完整条流程的——你至少需要一个能产出带时间戳逐字稿的 ASR，以及一个能看懂截图的视觉模型。

不过 `scripts/` 下的两个脚本本身是**自包含、可独立复用**的，见下文「单独用这两个脚本」。

---

## 它解决什么问题

软件培训录屏里最关键的按钮名、菜单路径、参数值、模块型号，往往**只出现在画面上，讲师嘴里不会念全**。纯音频转写一定会漏掉这些。

所以这个 Skill 把截图当成**补齐逐字稿丢失的证据**，而不是装饰性配图：

- 按「语言锚点 + ffmpeg 场景切换检测」挑截帧点，而不是机械地每 N 秒切一张；
- 逐张视觉复核，剔除静止重复帧 / 空白画布 / 加载中 / 过渡模糊；
- 界面文字看不清就**如实写「待确认」**，绝不猜按钮名和参数；
- 讲师口述与画面对不上时，按画面写，并单列「待确认清单」。

### 实测

一段 70 分 10 秒、1920×1080、451 MB 的虚拟仿真实训室培训录屏：

- 82 个候选截帧点 → 像素差复核剔除 24 张静止重复帧 → **58 张有效界面截图**
- 识别出三段共约 19 分钟画面完全定格的「无讲解实操」，每段保留 1 张作状态证据
- 产出 59 KB Markdown + 48 页 / 11 MB PDF
- 从截图里读到的、逐字稿中没有的硬信息：PLC 型号与订货号、变量地址、PUT/GET 复选框全名、两个不同的 IP、通讯端口、日志里的 `[ERROR] PLC读取失败`

---

## 安装

把整个目录放到 WorkBuddy 的用户级 skills 目录：

```
~/.workbuddy/skills/training-video-digest/     # macOS / Linux
C:\Users\<你>\.workbuddy\skills\training-video-digest\   # Windows
```

无需 `pip install`，内置转写也不需要任何 API key。

## 使用

在 WorkBuddy 里直接把本地视频发给它，例如：

```
@skill:"培训录屏图文纪要 Training Video Digest" 分析这段培训视频。E:/培训录屏.mp4
```

或者自然语言描述：「帮我把这个录屏整理成带截图的图文纪要，要点要带时间戳」。

## 产出结构

一个视频一个文件夹，**文件夹名 = 视频标题**，交付文件同名：

```
<视频标题>/
├── <视频标题>.md       ← 交付：图文对照纪要
├── <视频标题>.pdf      ← 交付：同内容 PDF
├── _frames/            ← 界面截图，md 用相对路径引用
├── transcript.md       ← 完整逐字稿存档
├── manifest.json       ← 截图清单（序号 / 时间戳 / 理由 / 是否保留）
└── _tmp_中间产物/      ← 场景检测日志等
```

Markdown 固定含 9 节，其中「关键操作路径汇总表」「术语表」「待确认清单」是必写项——站在甲方/教师视角，重点是「能不能照着做」和「哪些得回去问乙方」。

---

## 工作流一览

| 步骤 | 做什么 |
|---|---|
| 0 | 环境自检 + 视频体检（时长 / 分辨率 / 音轨 / ffmpeg） |
| 1 | 转写，判定时间戳模式：A 精确段级 / B 按字符比例估算（一律带 `~`） |
| 2 | 保存逐字稿，生成截图候选（语言锚点 + 场景切换点合并去重） |
| 3 | 精确截帧，按时间排序做像素差复核，剔除静止重复帧 |
| 4 | **逐张视觉复核**，读取界面上讲师没念的文字 |
| 5 | 按模板撰写纪要，口述与画面冲突处按画面写并标注待确认 |
| 6 | 校验图号连续、引用文件存在、时间戳不超时长，生成 PDF |
| 7 | 只交付 md + pdf 两个文件 |

完整规则、模板和踩坑记录见 [`SKILL.md`](SKILL.md) 与 [`references/output-template.md`](references/output-template.md)。

---

## 单独用这两个脚本

### `scripts/extract_frames.py`

```bash
# 视频体检
python extract_frames.py --probe "video.mp4"

# 从逐字稿生成截图候选清单
python extract_frames.py --candidates --video video.mp4 \
    --transcript transcript.md --out manifest.json

# 按清单精确截帧
python extract_frames.py --extract --video video.mp4 \
    --manifest manifest.json --frames _frames/
```

支持 `[00:12:34]`、`00:12:34 --> ...`、SRT/VTT、以及 `[起, 止, 说话人]` 段格式。先缩到 320px 再做场景检测，长视频快很多。ffmpeg 不在 PATH 时把本机路径补进 `FFMPEG_HINTS`。

### `scripts/md_to_pdf.py`

```bash
python md_to_pdf.py input.md output.pdf
```

关键点：把 md 里的相对图片路径**转成 base64 data URI 内嵌**后再交给 Chrome，且 HTML 在系统临时 ASCII 目录构建（规避中文路径导致打印失败）。直接用通用 md→pdf 工具会让 PDF 里的截图全部变成裂图。

---

## 仓库文件

```
SKILL.md                      技能主文档：触发条件、工作流、验收标准、踩坑记录
workbuddy.json                WorkBuddy 技能元数据（中英名称与描述）
references/output-template.md 纪要 Markdown 模板与写作规范
scripts/extract_frames.py     截帧 / 场景检测 / 候选生成
scripts/md_to_pdf.py          Markdown → A4 PDF（图片内嵌）
```

---

## 已知限制

- **强依赖 WorkBuddy 内置能力**，详见开头说明。若想脱离 WorkBuddy 使用，需要自行接一个带时间戳的 ASR（如 whisper + srt）与视觉模型；`extract_frames.py` 已支持 srt/vtt 格式的逐字稿输入。
- 内置转写上限约 5 小时 / 1 GB，超长视频要先按小时切片。
- 时间戳模式 B（无段级时间戳时）是**估算值**，语速不均可差几十秒，文档里一律带 `~` 前缀并说明。
- 截图数量按时长自适应（约每 75 秒 1 张、上限 96 张），真正该砍的是废帧，不是有信息量的界面。
- PDF 体积超过约 30 MB 通常意味着没压缩或废帧太多。

---

## 版本与许可

v1.0.0。

仓库暂未指定开源许可证，默认保留所有权利；如需以 MIT / Apache-2.0 等方式开放使用，请自行添加 `LICENSE` 文件。
