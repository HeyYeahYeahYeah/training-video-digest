# 转写后端选择与安装指引

`transcribe.py` 只负责把结果整理成统一逐字稿，**不绑定任何一家 ASR**。四条路径产出完全相同的 `transcript.md`，后续截帧与写作流程一模一样。

## 四条路径怎么选

| 路径 | 额外依赖 | 联网 | 时间戳 | 适合 |
|---|---|---|---|---|
| 导入 SRT/VTT | 无 | 否 | 精确（A） | **离线首选**；已有字幕，或用任意外部工具转好 |
| faster-whisper | `faster-whisper` | 首次下模型 | 精确（A） | 中文长视频、CPU 可跑、速度最好 |
| openai-whisper | `openai-whisper` | 首次下模型 | 精确（A） | 已有 PyTorch 环境、追求转录质量 |
| whisper.cpp CLI | 可执行文件 + 本地 ggml/gguf 模型 | 否（模型自备） | 精确（A） | 完全离线、低配机器 |
| 纯文本 | 无 | 否 | 无（B） | 只有文字稿，时间一律标 `~` |
| 客户端自带转写 | 客户端能力 | 视客户端而定 | 精确（A） | 你的 Agent 恰好有长音频转写 |

> 拿不准就用 **SRT 导入**：零依赖、零联网、时间戳精确，最不容易卡住。

## 安装本地 whisper（需先征得使用者同意）

```bash
# 推荐：CPU 上明显快于 openai-whisper，int8 量化后内存占用小
python -m pip install faster-whisper

# 备选
python -m pip install openai-whisper
```

装完先自检，确认脚本能看到后端：

```bash
python scripts/transcribe.py --check
```

再转写：

```bash
python scripts/transcribe.py --video "/path/to/培训.mp4" --out "培训/transcript.md" \
    --backend faster-whisper --model small --lang zh --device cpu
```

### 模型怎么选

| 模型 | 权重约 | 中文效果 | 1 小时音频 CPU 耗时（粗估） |
|---|---|---|---|
| `tiny` | 75 MB | 差，专名常错 | 几分钟 |
| `base` | 145 MB | 一般 | 10～20 分钟 |
| `small` | 0.5 GB | 够用，推荐起步 | 20～40 分钟 |
| `medium` | 1.5 GB | 好 | 1～2 小时 |
| `large-v3` | 3 GB | 最好 | 数小时，建议 GPU |

软件培训里专名多（模块名、型号、变量名），`small` 是性价比起点；**任何 ASR 都会把专名听错**，最终专名一律以截图为准，并在纪要「术语与专名表」里写明校正依据。

### 首次运行会下载模型

`faster-whisper` / `openai-whisper` 加载权重时若本地无缓存，会自行从模型仓库下载。脚本在运行前会打印提示，但**不会替你决定是否联网**。离线环境请：

1. 在联网机器上预下好模型目录，拷到目标机器；
2. `--model` 指向该本地目录（faster-whisper）；
3. 或直接用 `--import-srt`，完全不触发下载。

whisper.cpp 走 `--model` 指向本地 ggml/gguf 文件，脚本不会代下载任何模型。

## 导入现成字幕

```bash
python scripts/transcribe.py --import-srt captions.srt --out "培训/transcript.md"
```

支持标准 SRT 与 WebVTT，会自动去掉序号行、`WEBVTT` 头、`<c>` 等内联标签，按时间排序，同时输出 `transcript.srt` 便于在播放器里核对。

字幕可以来自任何工具：剪映/Pr 导出、 yt-dlp 下载的字幕、会议软件自带的转写、或人工听写。**本技能不关心它是谁生成的。**

## 纯文本兜底

```bash
python scripts/transcribe.py --from-text raw.txt --out "培训/transcript.md"
```

按中文标点拆句写出，文件头声明 `transcript.mode: B`。此时纪要里所有时间点必须写 `~[HH:MM:SS]`，并在第 1 节和第 9 节各声明一次是估算值。

## 客户端自带转写的格式契约

若你的 Agent 自己就能转写长音频，直接写 `transcript.md` 即可，不必跑 `transcribe.py`。格式：

```markdown
# 逐字稿

<!-- transcript.source: agent-builtin-asr -->
<!-- transcript.mode: A -->

[00:00:01.320] 今天我们看新建工程这个部分
[00:00:11.120] 左侧这个下拉里选模板
```

方括号里的时间戳会被 `extract_frames.py` 解析；文件头的 `transcript.mode` 声明优先于自动推断。若转写结果带说话人分离，也可写成 `[起,止,说话人ID]` 段格式，脚本同样支持。

## 隐私提示

- 本地 whisper 全程在本机处理，**音频不上传**。
- SRT 导入与纯文本路径**不产生任何网络请求**。
- 若改用云端转写服务，音频会离开本机；涉及校内培训、未公开软件、内部系统的录屏，请先确认对方的数据处理条款与学院/单位审核要求。

## 常见问题

- **长视频太慢**：先按小时用 ffmpeg 切片，分别转写后按时间偏移合并 SRT，再走 `--import-srt`。
- **静音段太多**：faster-whisper 已启用 `vad_filter`，会自动跳过静音；whisper.cpp 可自行加 VAD 参数。
- **中文被识别成英文/繁体**：显式传 `--lang zh`；仍不理想时换更大的模型。
- **`--check` 显示没有后端**：脚本不会自动安装。把上面安装命令给用户，等确认后再执行。
