#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transcribe.py — 把本地培训录屏转成「带时间戳的统一逐字稿」

三种互不依赖的输入来源，产出同一种 transcript.md（后续流程完全一致）：
  A. 本地 whisper 后端：faster-whisper / openai-whisper / whisper.cpp
  B. 导入现成的 .srt / .vtt 字幕（可用任意工具预先生成）
  C. 导入纯文本（无时间戳 → 模式 B，纪要里的时间戳一律带 ~ 前缀）
另有路径 D：由 Agent 自带的转写能力直接写出 transcript.md，见 SKILL.md Step 1。

隐私与权限约定（便于平台自动化安全审查）：
  * 不读取任何凭据、环境变量密钥、浏览器数据或用户主目录内容。
  * 不自动 pip install、不自动下载模型权重。缺依赖时只打印安装建议并退出。
  * 唯一可能的联网行为：你显式运行本地 whisper 时，该库首次加载模型会自行下载
    权重。运行前会打印提示；离线环境可改用 --import-srt，不产生任何网络请求。
  * 所有临时文件建在系统临时目录，结束时只删除本脚本自己创建的文件。
  * 唯一外部命令：ffmpeg（抽音轨）、以及你显式指定的 whisper.cpp 可执行文件。

用法：
  python transcribe.py --check
  python transcribe.py --video "/path/to/培训.mp4" --out "<标题>/transcript.md" \
      [--srt "<标题>/transcript.srt"] [--backend auto] [--model small] \
      [--lang zh] [--device cpu] [--whisper-cpp-bin PATH]
  python transcribe.py --import-srt captions.srt --out transcript.md [--video VIDEO]
  python transcribe.py --from-text raw.txt --out transcript.md

退出码：0 成功 / 1 输入缺失 / 2 ffmpeg 不可用 / 3 参数错 /
        4 无可用 ASR 后端（已打印安装建议）/ 5 后端执行失败
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------- 可执行文件探测

FFMPEG_HINTS = [
    os.environ.get("FFMPEG_BIN", ""),
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
    os.path.expandvars(r"%LocalAppData%\Microsoft\WinGet\Links\ffmpeg.exe"),
    "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg",
]
def _ffprobe_from(p):
    d, n = os.path.split(p)
    return os.path.join(d, n.replace("ffmpeg", "ffprobe")) if d else n.replace("ffmpeg", "ffprobe")


FFPROBE_HINTS = [os.environ.get("FFPROBE_BIN", "")] + [_ffprobe_from(p) for p in FFMPEG_HINTS if p]

WHISPER_CPP_NAMES = ("whisper-cli", "whisper-cpp", "main")


def find_bin(name, hints=()):
    p = shutil.which(name)
    if p:
        return p
    for h in hints:
        if h and os.path.isfile(h):
            return h
    return None


def run(cmd, timeout=7200):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def hms(sec, ms=True):
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    if not ms:
        return "%02d:%02d:%02d" % (h, m, s)
    return "%02d:%02d:%02d.%03d" % (h, m, s, round((sec - int(sec)) * 1000) % 1000)


def srt_time(sec):
    return hms(sec).replace(".", ",")


# ---------------------------------------------------------------- 后端探测


def detect_backends():
    """只读探测，不导入重库、不触发下载、不联网。"""
    env_bin = os.environ.get("WHISPER_CPP_BIN", "")
    cpp = env_bin if env_bin and os.path.isfile(env_bin) else None
    if not cpp:
        for n in WHISPER_CPP_NAMES:
            cpp = find_bin(n)
            if cpp:
                break
    return {
        "ffmpeg": find_bin("ffmpeg", FFMPEG_HINTS),
        "ffprobe": find_bin("ffprobe", FFPROBE_HINTS),
        "faster_whisper": bool(importlib.util.find_spec("faster_whisper")),
        "openai_whisper": bool(importlib.util.find_spec("whisper")),
        "whisper_cli": find_bin("whisper"),
        "whisper_cpp": cpp,
        "python_packages": {
            "Pillow": bool(importlib.util.find_spec("PIL")),
            "markdown": bool(importlib.util.find_spec("markdown")),
        },
    }


INSTALL_HINTS = [
    "本机未检测到可用的本地语音转写后端。请选择下面任一方式（都需要你确认后才安装）：",
    "  1) 推荐 faster-whisper（CPU 可跑，中文效果较好）：",
    "       python -m pip install faster-whisper",
    "     然后重跑：python transcribe.py --video <视频> --out transcript.md --backend faster-whisper --model small",
    "  2) openai-whisper：python -m pip install openai-whisper",
    "  3) 离线/已有字幕：python transcribe.py --import-srt captions.srt --out transcript.md",
    "  4) 纯文本逐字稿（无时间戳，模式 B）：python transcribe.py --from-text raw.txt --out transcript.md",
    "  5) 若你的 Agent 自带长音频转写能力，可让它直接写出 transcript.md 并跳过本脚本。",
]


def pick_backend(requested, b):
    order = ["faster-whisper", "openai-whisper", "whisper-cli", "whisper.cpp"]
    if requested != "auto":
        if requested not in order:
            print("ERR: unknown backend %r; choose from %s" % (requested, ", ".join(order)),
                  file=sys.stderr)
            sys.exit(3)
        return requested
    if b["faster_whisper"]:
        return "faster-whisper"
    if b["openai_whisper"]:
        return "openai-whisper"
    if b["whisper_cli"]:
        return "whisper-cli"
    if b["whisper_cpp"]:
        return "whisper.cpp"
    return None


# ---------------------------------------------------------------- 统一逐字稿

HEADER_MODE = "<!-- transcript.mode: {mode} -->"


def write_transcript(out_path, segments, source, mode, srt_path=None, video=None, duration=None):
    """segments: [(start_sec, end_sec, text)]; 统一写成 [HH:MM:SS.mmm] 文本 行。"""
    lines = [
        "# 逐字稿",
        "",
        "<!-- transcript.source: %s -->" % source,
        HEADER_MODE.format(mode=mode),
    ]
    if video:
        lines.append("<!-- transcript.video: %s -->" % os.path.basename(video))
    if duration:
        lines.append("<!-- transcript.duration_sec: %s -->" % round(duration, 2))
    lines += [
        "",
        "> 时间戳格式 `[HH:MM:SS.mmm]`；extract_frames.py 可直接解析。",
        "> 模式 B 的逐字稿不含时间戳，纪要中的时间点须以 `~` 前缀标注为估算值。",
        "",
    ]
    for st, en, text in segments:
        clean = " ".join((text or "").split())
        if not clean:
            continue
        if mode == "A":
            lines.append("[%s] %s" % (hms(st), clean))
        else:
            lines.append(clean)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    if srt_path and mode == "A":
        os.makedirs(os.path.dirname(os.path.abspath(srt_path)) or ".", exist_ok=True)
        with open(srt_path, "w", encoding="utf-8") as f:
            for i, (st, en, text) in enumerate([s for s in segments if " ".join((s[2] or "").split())], 1):
                f.write("%d\n%s --> %s\n%s\n\n" % (i, srt_time(st), srt_time(en), " ".join(text.split())))
    return out_path


# ---------------------------------------------------------------- SRT / VTT 导入

TS_SRT = re.compile(r"(\d{1,3}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*"
                    r"(\d{1,3}):(\d{2}):(\d{2})[.,](\d{1,3})")
TS_VTT_SHORT = re.compile(r"(\d{1,3}):(\d{2})[.,](\d{1,3})\s*-->\s*"
                          r"(\d{1,3}):(\d{2})[.,](\d{1,3})")


def _to_sec(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / (10 ** len(ms))


def parse_caption_file(path):
    """返回 [(start, end, text)]；支持 SRT 与 WebVTT。"""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        raw = f.read()
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", raw)
    segs = []
    for block in blocks:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        stamp = next((l for l in lines if "-->" in l), None)
        if not stamp:
            continue
        m = TS_SRT.search(stamp)
        if m:
            g = m.groups()
            st = _to_sec(g[0], g[1], g[2], g[3])
            en = _to_sec(g[4], g[5], g[6], g[7])
        else:
            m = TS_VTT_SHORT.search(stamp)
            if not m:
                continue
            g = m.groups()
            st = _to_sec(0, g[0], g[1], g[2])
            en = _to_sec(0, g[3], g[4], g[5])
        idx = lines.index(stamp)
        text = " ".join(lines[idx + 1:])
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        if text.strip():
            segs.append((st, en, text.strip()))
    segs.sort(key=lambda x: x[0])
    return segs


# ---------------------------------------------------------------- 抽音轨


def extract_audio(video, wav_path, ffmpeg):
    r = run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", video, "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", wav_path,
    ], timeout=1800)
    if r.returncode != 0 or not os.path.isfile(wav_path) or os.path.getsize(wav_path) == 0:
        print("ERR: ffmpeg 抽音轨失败\n%s" % (r.stderr or "")[-600:], file=sys.stderr)
        sys.exit(5)
    return wav_path


# ---------------------------------------------------------------- 各后端转写


def transcribe_faster(video, wav, model_name, lang, device, ffmpeg):
    print("NOTE: 若模型权重不在本地缓存，faster-whisper 首次加载会自行从模型仓库下载"
          "（small 约 0.5 GB）。离线环境请预先下载并用 --model 指向本地目录，"
          "或改用 --import-srt 完全避免联网。", file=sys.stderr)
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        print("ERR: import faster_whisper 失败: %s" % e, file=sys.stderr)
        sys.exit(5)
    compute = "int8" if device == "cpu" else "float16"
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute)
        kwargs = {"vad_filter": True}
        if lang and lang != "auto":
            kwargs["language"] = lang
        seg_iter, _info = model.transcribe(wav, **kwargs)
        segs = [(float(s.start), float(s.end), s.text) for s in seg_iter]
    except Exception as e:
        print("ERR: faster-whisper 执行失败: %s" % e, file=sys.stderr)
        sys.exit(5)
    return segs, "faster-whisper/%s/%s" % (model_name, lang)


def transcribe_openai(video, wav, model_name, lang, device, ffmpeg):
    print("NOTE: 若模型权重不在本地缓存，openai-whisper 首次加载会自行下载"
          "（small 约 0.5 GB）。离线环境请改用 --import-srt。", file=sys.stderr)
    try:
        import whisper
    except Exception as e:
        print("ERR: import whisper 失败: %s" % e, file=sys.stderr)
        sys.exit(5)
    try:
        model = whisper.load_model(model_name, device=device)
        kwargs = {"verbose": False}
        if lang and lang != "auto":
            kwargs["language"] = lang
        r = model.transcribe(wav, **kwargs)
        segs = [(float(s["start"]), float(s["end"]), s["text"]) for s in r.get("segments", [])]
    except Exception as e:
        print("ERR: openai-whisper 执行失败: %s" % e, file=sys.stderr)
        sys.exit(5)
    return segs, "openai-whisper/%s/%s" % (model_name, lang)


def transcribe_cli(video, wav, model_name, lang, cli_bin, tmpdir):
    out_prefix = os.path.join(tmpdir, "whisper_cli")
    cmd = [cli_bin, wav, "--model", model_name, "--output_format", "srt",
           "--output_dir", tmpdir]
    if lang and lang != "auto":
        cmd += ["--language", lang]
    r = run(cmd, timeout=7200)
    srt = os.path.join(tmpdir, os.path.basename(wav).rsplit(".", 1)[0] + ".srt")
    if not os.path.isfile(srt):
        cands = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.endswith(".srt")]
        srt = cands[0] if cands else None
    if not srt:
        print("ERR: whisper CLI 执行失败或未产出 srt\n%s" % (r.stderr or "")[-800:], file=sys.stderr)
        sys.exit(5)
    return parse_caption_file(srt), "whisper-cli/%s/%s" % (model_name, lang)


def transcribe_cpp(video, wav, model_path, lang, cpp_bin, tmpdir):
    if not model_path or not os.path.isfile(model_path):
        print("ERR: whisper.cpp 需要 --model 指向本地 ggml/gguf 模型文件（脚本不会代下载）",
              file=sys.stderr)
        sys.exit(3)
    out_prefix = os.path.join(tmpdir, "cpp")
    cmd = [cpp_bin, "-m", model_path, "-f", wav, "-osrt", "-of", out_prefix]
    if lang and lang != "auto":
        cmd += ["-l", lang]
    r = run(cmd, timeout=7200)
    srt = out_prefix + ".srt"
    if not os.path.isfile(srt):
        print("ERR: whisper.cpp 执行失败\n%s" % (r.stderr or "")[-800:], file=sys.stderr)
        sys.exit(5)
    return parse_caption_file(srt), "whisper.cpp/%s" % os.path.basename(model_path)


# ---------------------------------------------------------------- 清理


def cleanup_tmp(tmpdir):
    """只删本脚本自己在 tmpdir 里创建的文件，不动任何其它目录。"""
    try:
        for name in os.listdir(tmpdir):
            p = os.path.join(tmpdir, name)
            if os.path.isfile(p) or os.path.islink(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        os.rmdir(tmpdir)
    except OSError:
        pass


# ---------------------------------------------------------------- CLI


def main():
    ap = argparse.ArgumentParser(description="培训录屏转写：本地 whisper / 导入字幕 / 纯文本")
    ap.add_argument("--check", action="store_true", help="只探测环境，不安装、不联网、不写文件")
    ap.add_argument("--video", help="本地视频文件")
    ap.add_argument("--out", help="输出 transcript.md 路径")
    ap.add_argument("--srt", help="同时输出 .srt 字幕（默认与 --out 同名）")
    ap.add_argument("--backend", default="auto",
                    help="auto | faster-whisper | openai-whisper | whisper-cli | whisper.cpp")
    ap.add_argument("--model", default="small", help="模型名或本地模型路径")
    ap.add_argument("--lang", default="zh", help="语言代码，auto 为自动检测")
    ap.add_argument("--device", default="cpu", help="cpu / cuda")
    ap.add_argument("--whisper-cpp-bin", default="", help="whisper.cpp 可执行文件路径")
    ap.add_argument("--import-srt", help="导入现成 SRT/VTT，不做任何转写")
    ap.add_argument("--from-text", help="导入纯文本逐字稿（模式 B）")
    a = ap.parse_args()

    b = detect_backends()
    if a.check:
        print(json.dumps(b, ensure_ascii=False, indent=2))
        return

    if not a.out:
        print("ERR: 需要 --out 指定 transcript.md 路径", file=sys.stderr)
        sys.exit(3)

    # 路径 B：导入字幕
    if a.import_srt:
        if not os.path.isfile(a.import_srt):
            print("ERR: 字幕文件不存在: %s" % a.import_srt, file=sys.stderr)
            sys.exit(1)
        segs = parse_caption_file(a.import_srt)
        if not segs:
            print("ERR: 字幕里没解析出任何时间段，检查是否为标准 SRT/VTT", file=sys.stderr)
            sys.exit(1)
        write_transcript(a.out, segs, "imported-caption/%s" % os.path.basename(a.import_srt),
                         "A", a.srt, a.video)
        print("OK: 导入 %d 段 → %s（模式 A，精确时间戳）" % (len(segs), a.out))
        return

    # 路径 C：纯文本
    if a.from_text:
        if not os.path.isfile(a.from_text):
            print("ERR: 文本文件不存在: %s" % a.from_text, file=sys.stderr)
            sys.exit(1)
        with open(a.from_text, encoding="utf-8-sig", errors="replace") as f:
            raw = f.read()
        parts = [s.strip() for s in re.split(r"(?<=[。！？；!?;])|\n+", raw) if s.strip()]
        write_transcript(a.out, [(0, 0, s) for s in parts],
                         "plain-text/%s" % os.path.basename(a.from_text), "B", None, a.video)
        print("OK: 导入 %d 句 → %s（模式 B，无时间戳）" % (len(parts), a.out))
        return

    # 路径 A：本地 whisper
    if not a.video:
        print("ERR: 需要 --video；或改用 --import-srt / --from-text", file=sys.stderr)
        sys.exit(3)
    if not os.path.isfile(a.video):
        print("ERR: 视频文件不存在: %s" % a.video, file=sys.stderr)
        sys.exit(1)
    if not b["ffmpeg"]:
        print("ERR: 找不到 ffmpeg。请安装后加入 PATH，或设置 FFMPEG_BIN 环境变量。",
              file=sys.stderr)
        sys.exit(2)
    backend = pick_backend(a.backend, b)
    if not backend:
        for line in INSTALL_HINTS:
            print(line, file=sys.stderr)
        sys.exit(4)

    tmpdir = tempfile.mkdtemp(prefix="tvd_asr_")
    try:
        wav = os.path.join(tmpdir, "audio.wav")
        extract_audio(a.video, wav, b["ffmpeg"])
        if backend == "faster-whisper":
            segs, source = transcribe_faster(a.video, wav, a.model, a.lang, a.device, b["ffmpeg"])
        elif backend == "openai-whisper":
            segs, source = transcribe_openai(a.video, wav, a.model, a.lang, a.device, b["ffmpeg"])
        elif backend == "whisper-cli":
            segs, source = transcribe_cli(a.video, wav, a.model, a.lang, b["whisper_cli"], tmpdir)
        else:
            cpp_bin = a.whisper_cpp_bin or b["whisper_cpp"]
            segs, source = transcribe_cpp(a.video, wav, a.model, a.lang, cpp_bin, tmpdir)
    finally:
        cleanup_tmp(tmpdir)

    segs = sorted([s for s in segs if " ".join((s[2] or "").split())], key=lambda x: x[0])
    if not segs:
        print("ERR: 转写结果为空", file=sys.stderr)
        sys.exit(5)
    srt_path = a.srt or os.path.splitext(a.out)[0] + ".srt"
    write_transcript(a.out, segs, source, "A", srt_path, a.video, segs[-1][1])
    print("OK: %d 段 → %s（模式 A，精确时间戳），字幕 %s" % (len(segs), a.out, srt_path))


if __name__ == "__main__":
    main()
