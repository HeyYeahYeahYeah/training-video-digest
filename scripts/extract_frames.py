#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_frames.py — 培训录屏截图引擎（环境自检 / 操作节点候选 / 精确截帧）

四种模式，互斥：
  --check
      只读探测依赖（ffmpeg / ffprobe / Pillow），不写文件、不联网、不安装。
  --probe <video>
      视频体检，输出 JSON。
  --candidates --video <v> --transcript <t.md> --out <manifest.json>
      语言锚点 + ffmpeg 场景检测 → 合并去重 → 候选清单。
  --extract --video <v> --manifest <manifest.json> --frames <dir>
      按清单精确截帧 + 压缩，回写 manifest 的 file 字段。

逐字稿格式：接受 [HH:MM:SS]、SRT/VTT 行、以及 [起,止,说话人] 段格式；
也可由 transcribe.py 产出（文件头带 <!-- transcript.mode: A|B -->，本脚本优先采信）。

可选参数：--min-gap 12 --max-frames 0 --threshold 0.25 --no-scene
退出码：0 成功 / 1 输入缺失 / 2 ffmpeg 或视频不可用 / 3 参数错

权限约定：不联网、不自动安装、不改动机上任何既有文件；
临时原图建在本脚本创建的 _raw 子目录，结束时只删除自己刚写的文件。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

# ---------------------------------------------------------------- 基础工具

FFMPEG_HINTS = [
    # ffmpeg 不在 PATH 时，可设置 FFMPEG_BIN 环境变量，或把路径按下面格式补进列表
    os.environ.get("FFMPEG_BIN", ""),
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
    os.path.expandvars(r"%LocalAppData%\Microsoft\WinGet\Links\ffmpeg.exe"),
    "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg",
]
def _ffprobe_from(p):
    """只替换文件名，不动路径里的 ffmpeg 目录名。"""
    d, n = os.path.split(p)
    return os.path.join(d, n.replace("ffmpeg", "ffprobe")) if d else n.replace("ffmpeg", "ffprobe")


FFPROBE_HINTS = [os.environ.get("FFPROBE_BIN", "")] + [_ffprobe_from(p) for p in FFMPEG_HINTS if p]


def find_bin(name, hints):
    p = shutil.which(name)
    if p:
        return p
    for h in hints:
        if h and os.path.isfile(h):
            return h
    return None


def run(cmd, timeout=1800):
    """跑外部命令，返回 CompletedProcess；编码问题一律吞掉。"""
    return subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def hhmmss(sec):
    sec = max(0, int(sec))
    return "%02d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)


def compact_hhmmss(sec):
    return hhmmss(sec).replace(":", "")


# ---------------------------------------------------------------- 时间戳解析

TS_PATTERNS = [
    re.compile(r"\[\s*(\d{1,2}):(\d{2}):(\d{2})(?:[.,]\d+)?\s*\]"),   # [00:12:34]
    re.compile(r"\[\s*(\d{1,2}):(\d{2})\s*\]"),                          # [12:34]
    re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,]\d+\s*-->\s*"),            # srt/vtt
    re.compile(r"^\s*(\d{1,2}):(\d{2}):(\d{2})\s"),                      # 行首时间戳
    re.compile(r"^\s*(\d{1,2}):(\d{2})\s"),                               # 行首 mm:ss
]


# 某些 ASR 服务/工具会输出段级格式：
#   [0:1.320,0:11.120,0]         分:秒.毫秒, 结束, 说话人
#   [1:23:45.120,1:23:50.000,0] 时:分:秒.毫秒, 结束, 说话人
# 必须优先于 TS_PATTERNS 判定，否则会被误判为「无时间戳」走估算模式。
ATR_SEG_RE = re.compile(
    r"\[\s*(\d{1,3}):(\d{1,2})(?::(\d{1,2}))?[.,](\d{1,3})\s*,"
    r"\s*[\d:.,]+\s*,\s*(\d+)\s*\]"
)


def parse_atr(line):
    """返回 (起始秒 float, 说话人 id int)；不匹配返回 (None, None)。"""
    m = ATR_SEG_RE.search(line)
    if not m:
        return None, None
    a, b, c, ms, sp = m.groups()
    frac = int(ms) / (10 ** len(ms))
    if c is not None:
        sec = int(a) * 3600 + int(b) * 60 + int(c) + frac
    else:
        sec = int(a) * 60 + int(b) + frac
    return sec, int(sp)


def parse_ts(line):
    atr, _ = parse_atr(line)
    if atr is not None:
        return atr
    for i, pat in enumerate(TS_PATTERNS):
        m = pat.search(line)
        if m:
            g = [int(x) for x in m.groups()]
            return (g[0] * 3600 + g[1] * 60 + g[2]) if len(g) == 3 else (g[0] * 60 + g[1])
    return None


# 操作指示词：命中即认为此处讲了「需要看界面」的动作
ANCHOR_WORDS = [
    "点击", "单击", "双击", "打开", "输入", "填写", "设置", "选择", "勾选",
    "下拉", "菜单", "按钮", "参数", "界面", "窗口", "面板", "模块", "配置",
    "保存", "运行", "仿真", "导入", "导出", "新建", "添加", "拖拽", "切换",
    "这里", "这个", "如图所示", "大家看", "看一下", "注意", "下一步", "左边",
    "右边", "上方", "下方", "弹窗", "对话框", "工具栏", "属性",
]
ANCHOR_RE = re.compile("|".join(map(re.escape, ANCHOR_WORDS)))


# ---------------------------------------------------------------- 视频体检


def probe(video, ffmpeg, ffprobe):
    if not os.path.isfile(video):
        print("ERR: video not found: %s" % video, file=sys.stderr)
        sys.exit(1)
    if not ffprobe:
        print("ERR: ffprobe not found. Install ffmpeg or check FFMPEG_HINTS.", file=sys.stderr)
        sys.exit(2)
    r = run([
        ffprobe, "-v", "error", "-show_entries",
        "format=duration,size,format_name:stream=index,codec_type,codec_name,width,height,r_frame_rate",
        "-of", "json", video,
    ], timeout=120)
    if r.returncode != 0:
        print("ERR: ffprobe failed\n%s" % (r.stderr or "")[-800:], file=sys.stderr)
        sys.exit(2)
    info = json.loads(r.stdout)
    dur = float(info.get("format", {}).get("duration") or 0)
    streams = info.get("streams", [])
    vids = [s for s in streams if s.get("codec_type") == "video"]
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    out = {
        "video": os.path.abspath(video),
        "size_bytes": int(info.get("format", {}).get("size") or 0),
        "duration_sec": round(dur, 2),
        "duration_hms": hhmmss(dur),
        "has_video": bool(vids),
        "has_audio": bool(auds),
        "width": vids[0].get("width") if vids else None,
        "height": vids[0].get("height") if vids else None,
        "video_codec": vids[0].get("codec_name") if vids else None,
        "audio_codec": auds[0].get("codec_name") if auds else None,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        # 仅作提示：超过时建议按小时切片，避免单次处理过慢；不是硬性上限。
        "suggest_split": dur > 5 * 3600 or
                         int(info.get("format", {}).get("size") or 0) > 1024 ** 3,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


# ---------------------------------------------------------------- 场景检测


def scene_times(video, ffmpeg, threshold):
    """ffmpeg 场景切换检测。先缩到 320 宽再判，长视频能快很多。"""
    vf = "scale=320:-2,select='gt(scene,%s)',showinfo" % threshold
    r = run([
        ffmpeg, "-hide_banner", "-nostdin", "-an", "-sn",
        "-i", video, "-vf", vf, "-f", "null", "-",
    ], timeout=3600)
    times = [float(x) for x in re.findall(r"pts_time:(\d+(?:\.\d+)?)", r.stderr or "")]
    return sorted(set(times)), (r.returncode == 0)


# ---------------------------------------------------------------- 候选生成


def build_candidates(video, transcript, out, ffmpeg, ffprobe,
                     min_gap, max_frames, threshold, use_scene):
    info = probe(video, ffmpeg, ffprobe)   # 会打印一次 JSON，便于日志
    dur = info["duration_sec"]
    if max_frames <= 0:
        # 按时长自适应：约每 75 秒 1 张，短片不少于 24 张，长片封顶 96 张
        max_frames = min(96, max(24, round(dur / 75)))
    if dur <= 0:
        print("ERR: invalid duration", file=sys.stderr)
        sys.exit(2)

    with open(transcript, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    lines = raw.splitlines(True)
    full = raw
    n_chars = max(1, len(full))
    # transcribe.py 会在文件头声明模式；优先采信，避免字幕行数少时被误判成 B。
    declared = re.search(r"<!--\s*transcript\.mode:\s*([AB])\s*-->", raw)
    source_m = re.search(r"<!--\s*transcript\.source:\s*([^>]+?)\s*-->", raw)

    # 切成语义片段：有时间戳的行直接用；无时间戳的行按中文标点拆句，
    # 再按「累计字符偏移 / 全文字符数 × 时长」估算时间。
    # 这样即使转写返回的是一整段无换行文本，锚点也能在时间轴上铺开而不会挤成一团。
    SENT_SPLIT = re.compile(r"(?<=[。！？；!?;])")
    segments = []       # (text, ts, exact)
    offset = 0
    ts_seen = 0
    speakers = {}
    for line in lines:
        stripped = line.strip()
        _, sp = parse_atr(stripped)
        if sp is not None:
            speakers[sp] = speakers.get(sp, 0) + 1
        ts = parse_ts(line)
        if ts is not None:
            ts_seen += 1
            segments.append((stripped, ts, True))
        elif stripped:
            for sent in SENT_SPLIT.split(stripped):
                sent = sent.strip()
                if not sent:
                    continue
                pos = full.find(sent, max(0, offset - 20))
                mid = (pos + len(sent) / 2) if pos >= 0 else (offset + len(sent) / 2)
                segments.append((sent, (mid / n_chars) * dur, False))
        offset += len(line)

    if declared:
        # 声明 A 但一个时间戳都没解析出来 → 数据不一致，按更保守的 B 处理
        timestamp_mode = "A" if (declared.group(1) == "A" and ts_seen > 0) else "B"
    else:
        timestamp_mode = "A" if ts_seen >= max(3, len(lines) * 0.1) else "B"

    lang_hits = []      # (ts, reason, context, exact?)
    for text, ts, exact in segments:
        if ANCHOR_RE.search(text):
            words = sorted(set(ANCHOR_RE.findall(text)), key=len, reverse=True)[:3]
            lang_hits.append((
                min(ts, dur - 1),
                "命中操作指示词：" + "、".join(words),
                text[:120],
                exact,
            ))

    scenes = []
    scene_ok = True
    if use_scene and info["has_video"]:
        scenes, scene_ok = scene_times(video, ffmpeg, threshold)

    # 语言锚点优先。只有 ±2s 内确有画面切换才吸附到切换后 1.5s（避开转场模糊帧）；
    # 且同一个场景点只服务一个锚点，否则多个不同时刻的讲解会被挤成同一个截帧点。
    # 早期版本用 ±8s 且不去重场景点，短视频/场景点稀疏时会把全部锚点吸成一张，已修正。
    SNAP_WINDOW = 2.0
    snapped = set()
    merged = []
    for ts, reason, ctx, exact in lang_hits:
        near = [s for s in scenes if abs(s - ts) <= SNAP_WINDOW]
        use_ts, source = ts, "language"
        if near:
            scene = min(near, key=lambda s: abs(s - ts))
            key = round(scene, 1)
            if key not in snapped:
                snapped.add(key)
                use_ts, source = scene + 1.5, "language+scene"
        merged.append({
            "ts": round(min(use_ts, dur - 1), 2),
            "source": source,
            "reason": reason,
            "context": ctx,
            "exact": exact,
        })

    # 语言锚点不够时，用场景切换点补足（界面跳转通常就有信息量）
    if len(merged) < max_frames:
        used = [m["ts"] for m in merged]
        for s in scenes:
            if all(abs(s - u) >= min_gap for u in used):
                merged.append({
                    "ts": round(min(s + 1.5, dur - 1), 2),
                    "source": "scene",
                    "reason": "画面发生切换（界面跳转/弹窗）",
                    "context": "",
                    "exact": True,
                })
                used.append(s + 1.5)
            if len(merged) >= max_frames * 2:
                break

    # min-gap 去重
    merged.sort(key=lambda x: x["ts"])
    dedup = []
    for m in merged:
        if not dedup or m["ts"] - dedup[-1]["ts"] >= min_gap:
            dedup.append(m)

    # 均匀降采样封顶
    if len(dedup) > max_frames:
        step = (len(dedup) - 1) / (max_frames - 1) if max_frames > 1 else 0
        idx = sorted({round(i * step) for i in range(max_frames)})
        dedup = [dedup[i] for i in idx]

    for i, m in enumerate(dedup, 1):
        m["i"] = i
        m["hhmmss"] = hhmmss(m["ts"])
        m["keep"] = True
        m["file"] = None

    manifest = {
        "video": info["video"],
        "duration_sec": dur,
        "timestamp_mode": timestamp_mode,
        "transcript_format": (
            "段级格式 [起,止,说话人]，含说话人分离" if speakers else
            ("带时间戳逐字稿（transcribe.py 或字幕导入）" if timestamp_mode == "A"
             else "纯文本逐字稿（无时间戳）")
        ),
        "transcript_source": source_m.group(1) if source_m else "unspecified",
        "speakers": speakers,
        "timestamp_note": (
            "逐字稿含段级时间戳，文档时间戳可直接跳转" if timestamp_mode == "A" else
            "逐字稿无时间戳，位置按内容比例估算，文档中一律以 ~ 前缀标注为近似值"
        ),
        "scene_detect_ok": scene_ok,
        "scene_points": len(scenes),
        "language_hits": len(lang_hits),
        "min_gap": min_gap,
        "max_frames": max_frames,
        "candidates": dedup,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("\nCANDIDATES: %d 条 | 模式 %s | 场景点 %d | 写入 %s" %
          (len(dedup), timestamp_mode, len(scenes), out), file=sys.stderr)


# ---------------------------------------------------------------- 精确截帧


def extract(video, manifest_path, frames_dir, ffmpeg):
    with open(manifest_path, encoding="utf-8") as f:
        mf = json.load(f)
    raw_dir = os.path.join(frames_dir, "_raw")
    os.makedirs(raw_dir, exist_ok=True)
    # 只记录本脚本本次写出的原图，结束时逐个删除；不递归删除任何目录。
    raw_files = []
    frames_rel = os.path.basename(os.path.normpath(frames_dir)) or "_frames"

    kept = [c for c in mf["candidates"] if c.get("keep", True)]
    done = 0
    for c in kept:
        name = "%03d_%s.jpg" % (c["i"], compact_hhmmss(c["ts"]))
        final = os.path.join(frames_dir, name)
        raw = os.path.join(raw_dir, name)
        r = run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", "%.3f" % c["ts"], "-i", video,
            "-frames:v", "1", "-q:v", "2", raw,
        ], timeout=120)
        if r.returncode != 0 or not os.path.isfile(raw) or os.path.getsize(raw) == 0:
            print("WARN: 截帧失败 ts=%s %s" % (c["ts"], (r.stderr or "")[-200:]), file=sys.stderr)
            c["file"] = None
            continue
        raw_files.append(raw)
        try:
            from PIL import Image
            with Image.open(raw) as im:
                im = im.convert("RGB")
                if im.width > 1600:
                    h = round(im.height * 1600 / im.width)
                    im = im.resize((1600, h), Image.LANCZOS)
                im.save(final, "JPEG", quality=82, optimize=True)
        except Exception:
            shutil.copyfile(raw, final)
        c["file"] = frames_rel + "/" + name
        c["bytes"] = os.path.getsize(final)
        done += 1

    for p in raw_files:
        try:
            os.remove(p)
        except OSError:
            pass
    try:
        os.rmdir(raw_dir)   # 仅当目录已空才成功；不会动任何其它文件
    except OSError:
        pass
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(mf, f, ensure_ascii=False, indent=2)
    print("EXTRACTED: %d / %d 张 → %s" % (done, len(kept), frames_dir), file=sys.stderr)


# ---------------------------------------------------------------- CLI


def main():
    ap = argparse.ArgumentParser(description="培训录屏截图引擎")
    ap.add_argument("--check", action="store_true",
                    help="只读探测依赖，不联网、不安装、不写文件")
    ap.add_argument("--probe", metavar="VIDEO", help="环境自检 + 视频体检")
    ap.add_argument("--candidates", action="store_true", help="生成操作节点候选清单")
    ap.add_argument("--extract", action="store_true", help="按清单精确截帧")
    ap.add_argument("--video")
    ap.add_argument("--transcript")
    ap.add_argument("--out")
    ap.add_argument("--manifest")
    ap.add_argument("--frames")
    ap.add_argument("--min-gap", type=float, default=12)
    ap.add_argument("--max-frames", type=int, default=0,
                    help="截图上限；0=按视频时长自适应（约每 75 秒 1 张，24~96 张）")
    ap.add_argument("--threshold", type=float, default=0.25)
    ap.add_argument("--no-scene", action="store_true", help="跳过场景检测（只用语言锚点）")
    a = ap.parse_args()

    ffmpeg = find_bin("ffmpeg", FFMPEG_HINTS)
    ffprobe = find_bin("ffprobe", FFPROBE_HINTS)

    if a.check:
        import importlib.util
        print(json.dumps({
            "ffmpeg": ffmpeg,
            "ffprobe": ffprobe,
            "ffmpeg_ready": bool(ffmpeg and ffprobe),
            "Pillow": bool(importlib.util.find_spec("PIL")),
            "markdown": bool(importlib.util.find_spec("markdown")),
            "python": sys.version.split()[0],
        }, ensure_ascii=False, indent=2))
        return
    if not ffmpeg:
        print("ERR: ffmpeg not found. Install ffmpeg, put it on PATH, "
              "or set the FFMPEG_BIN environment variable.", file=sys.stderr)
        sys.exit(2)

    if a.probe:
        probe(a.probe, ffmpeg, ffprobe)
    elif a.candidates:
        if not (a.video and a.transcript and a.out):
            print("ERR: --candidates needs --video --transcript --out", file=sys.stderr)
            sys.exit(3)
        build_candidates(a.video, a.transcript, a.out, ffmpeg, ffprobe,
                         a.min_gap, a.max_frames, a.threshold, not a.no_scene)
    elif a.extract:
        if not (a.video and a.manifest and a.frames):
            print("ERR: --extract needs --video --manifest --frames", file=sys.stderr)
            sys.exit(3)
        extract(a.video, a.manifest, a.frames, ffmpeg)
    else:
        ap.print_help()
        sys.exit(3)


if __name__ == "__main__":
    main()
