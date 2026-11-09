#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vision_review.py — 截帧的「自动去重 + 视觉复核准备」

这是把「人眼/模型看图」变成可交付流程的辅助脚本，本身不认识界面文字。
它做三件事：
  1. --dedup   按时间顺序比较相邻帧像素差，把长时间静止的重复画面标为 keep=false，
               每段静止只保留第一张作状态证据，并回写 manifest.json。
  2. --sheet   把保留帧拼成若干张 contact sheet（默认每页 16 张），
               让 Agent 一次读一页而不是逐张调用，显著降低读图成本。
  3. --ocr     若系统装了 tesseract，额外产出 ocr_notes.md 作为文字线索（可选降级）。
无论哪种模式，都会输出 review_queue.md：帧清单 + 一段与客户端无关的读图提示词。

没有读图能力的环境：仍可用 --dedup + --ocr 得到线索，但界面文字必须标注「待确认」，
绝不允许根据 OCR 碎片猜测按钮名或参数值（见 SKILL.md）。

用法：
  python vision_review.py --frames "_frames" --manifest manifest.json \
      --out "_tmp_中间产物" --dedup --sheet [--ocr] [--ocr-lang chi_sim+eng]

退出码：0 成功 / 1 输入缺失 / 3 参数错
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

REVIEW_PROMPT = """这是中文软件操作培训录屏的一张截帧。请逐字读出画面上所有可辨认的界面文字：
窗口/对话框标题、菜单项、面板名、按钮名、表格列名与各行内容、输入框标签与数值、
参数值、IP / 端口 / 地址、报错或日志文字。然后说明画面正在演示什么操作。
若画面模糊、被遮挡、是人脸/摄像头画面、黑屏、或与前一帧完全相同而无新增信息，
请明确说明「不可辨认 / 无新增信息」，不要猜测任何专名或数值。"""

TESSERACT_HINTS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract", "/usr/local/bin/tesseract", "/opt/homebrew/bin/tesseract",
]

FNAME_RE = re.compile(r"(\d{3})_(\d{6})\.jpg$", re.I)


def load_items(frames_dir, manifest_path):
    """返回 (items, mf)。无 manifest 时 mf 为 None；mf 与 items 里的 c 指向同一批字典。"""
    items = []
    mf = None
    if manifest_path and os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            mf = json.load(f)
        for idx, c in enumerate(mf.get("candidates", []), 1):
            rel = c.get("file") or ""
            name = os.path.basename(rel.replace("\\", "/"))
            path = os.path.join(frames_dir, name) if name else ""
            m = FNAME_RE.search(name)
            ts = c.get("ts")
            if ts is None and m:
                h, mi, s = m.group(2)[:2], m.group(2)[2:4], m.group(2)[4:6]
                ts = int(h) * 3600 + int(mi) * 60 + int(s)
            items.append({
                "file": name, "path": path,
                "ts": ts if ts is not None else idx,
                "i": c.get("i", idx),
                "context": (c.get("context") or "")[:90],
                "c": c,
            })
    else:
        for name in sorted(os.listdir(frames_dir)):
            m = FNAME_RE.search(name)
            if not m:
                continue
            h, mi, s = m.group(2)[:2], m.group(2)[2:4], m.group(2)[4:6]
            items.append({
                "file": name, "path": os.path.join(frames_dir, name),
                "ts": int(h) * 3600 + int(mi) * 60 + int(s),
                "i": int(m.group(1)), "context": "", "c": None,
            })
    items = [x for x in items if x["file"] and os.path.isfile(x["path"])]
    items.sort(key=lambda x: (x["ts"], x["i"]))
    return items, mf


def thumb(path, size=(160, 90)):
    from PIL import Image
    with Image.open(path) as im:
        # tobytes() 比 getdata() 快，且不会触发 Pillow 14 的弃用警告
        return im.convert("L").resize(size).tobytes()


def dedup(items, threshold, mf):
    """与「上一张保留帧」比较；静止段首帧保留，其余标 keep=false。

    直接修改传入的 mf 对象（与 items 里的 c 同源），由 main 统一写回，
    避免重新读文件导致改动丢失。
    """
    try:
        from PIL import Image  # noqa
    except ImportError:
        print("WARN: 缺少 Pillow，跳过自动去重（python -m pip install Pillow）", file=sys.stderr)
        return items, 0
    prev = None
    dropped = 0
    for it in items:
        try:
            px = thumb(it["path"])
        except Exception:
            continue
        if prev is not None:
            d = sum(abs(a - b) for a, b in zip(px, prev)) / len(px)
            it["diff"] = round(d, 2)
            if d < threshold:
                it["keep"] = False
                dropped += 1
                if it["c"] is not None:
                    it["c"]["keep"] = False
                    it["c"]["drop_reason"] = (
                        "与上一张保留帧画面静止重复（像素差 %.2f < %.2f），"
                        "该时段保留时间上最早的一张作为状态证据" % (d, threshold)
                    )
            else:
                prev = px
        else:
            it["diff"] = None
            prev = px
        if it.get("keep") is None:
            it["keep"] = True
    if mf is not None:
        mf["dedup_threshold"] = threshold
        mf["dedup_dropped"] = dropped
        mf["curation_note"] = "vision_review.py 按时间顺序做像素差复核，剔除 %d 张静止重复帧。" % dropped
    return items, dropped


def make_sheets(kept, out_dir, per_sheet, cell_w):
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(out_dir, exist_ok=True)
    label_h = 26
    sheets = []
    for page in range(0, len(kept), per_sheet):
        chunk = kept[page:page + per_sheet]
        cols = 4
        rows = (len(chunk) + cols - 1) // cols
        cw = cell_w
        # 先按第一张的宽高比定 cell 高
        with Image.open(chunk[0]["path"]) as im0:
            ratio = im0.height / im0.width
        ch = int(cw * ratio) + label_h
        canvas = Image.new("RGB", (cols * cw, rows * ch), "#ffffff")
        draw = ImageDraw.Draw(canvas)
        for k, it in enumerate(chunk):
            with Image.open(it["path"]) as im:
                im = im.convert("RGB").resize((cw, ch - label_h), Image.LANCZOS)
            x = (k % cols) * cw
            y = (k // cols) * ch
            canvas.paste(im, (x, y + label_h))
            draw.rectangle([x, y, x + cw, y + label_h], fill="#1c3c2e")
            draw.text((x + 6, y + 6), "%s  %s" % (it["file"], it.get("hhmmss", "")),
                      fill="#ffffff")
        p = os.path.join(out_dir, "sheet_%02d.jpg" % (page // per_sheet + 1))
        canvas.save(p, "JPEG", quality=85, optimize=True)
        sheets.append(p)
    return sheets


def find_tesseract():
    p = shutil.which("tesseract")
    if p:
        return p
    for h in TESSERACT_HINTS:
        if os.path.isfile(h):
            return h
    return None


def run_ocr(kept, out_dir, lang, tess):
    lines = ["# OCR 文字线索（仅供参考，不能作为结论依据）", "",
             "> tesseract 输出含错字与断行。**按钮名 / 参数值 / 型号必须以原图为准**，",
             "> OCR 读不清时一律写「待确认」。此文件不替代逐帧视觉复核。", ""]
    for it in kept:
        try:
            r = subprocess.run([tess, it["path"], "stdout", "-l", lang],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=90)
            txt = (r.stdout or "").strip()
        except Exception as e:
            txt = "(OCR 失败: %s)" % e
        lines += ["## %s" % it["file"], "", txt or "(无可辨识文字)", ""]
    p = os.path.join(out_dir, "ocr_notes.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return p


def write_queue(kept, out_dir, sheets, ocr_path, total, dropped):
    lines = [
        "# 视觉复核队列", "",
        "- 截帧总数：%d；自动剔除静止重复帧：%d；待复核：**%d 张**" % (total, dropped, len(kept)),
        "",
        "## 怎么用", "",
        "把下面列出的 contact sheet 逐页交给你的 Agent 的图像读取能力，",
        "按提示词逐帧回答；把读到的界面文字写进纪要对应小节。",
        "没有读图能力时，只能参考 `ocr_notes.md`，且所有看不清的专名必须写「待确认」。",
        "",
        "## 读图提示词（逐帧复用）", "", "```text", REVIEW_PROMPT, "```", "",
        "## contact sheet", "",
    ]
    for s in sheets:
        lines.append("- `%s`" % os.path.basename(s))
    lines += [""]
    if ocr_path:
        lines += ["OCR 线索：`%s`" % os.path.basename(ocr_path), ""]
    lines += [
        "## 帧清单", "",
        "| # | 文件 | 时间戳 | 逐字稿上下文 |", "|---|---|---|---|",
    ]
    for n, it in enumerate(kept, 1):
        ctx = it["context"].replace("|", "\\|").replace("\n", " ")
        lines.append("| %d | `%s` | %s | %s |" % (n, it["file"], it.get("hhmmss", ""), ctx))
    lines += ["", "> 复核后把无效帧在 manifest.json 里改为 `keep:false`，并从文稿中移除对应引用。", ""]
    p = os.path.join(out_dir, "review_queue.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return p


def main():
    ap = argparse.ArgumentParser(description="截帧自动去重与视觉复核准备")
    ap.add_argument("--frames", required=True, help="截帧目录，如 _frames/")
    ap.add_argument("--manifest", help="manifest.json；提供时回写 keep/drop_reason")
    ap.add_argument("--out", default="_tmp_中间产物", help="复核产物目录")
    ap.add_argument("--dedup", action="store_true", help="按像素差剔除静止重复帧")
    ap.add_argument("--dedup-threshold", type=float, default=1.0)
    ap.add_argument("--sheet", action="store_true", help="生成 contact sheet 拼图")
    ap.add_argument("--per-sheet", type=int, default=16)
    ap.add_argument("--cell-width", type=int, default=400)
    ap.add_argument("--ocr", action="store_true", help="可选：tesseract OCR 文字线索")
    ap.add_argument("--ocr-lang", default="chi_sim+eng")
    a = ap.parse_args()

    if not os.path.isdir(a.frames):
        print("ERR: 截帧目录不存在: %s" % a.frames, file=sys.stderr)
        sys.exit(1)
    os.makedirs(a.out, exist_ok=True)

    items, mf = load_items(a.frames, a.manifest)
    if not items:
        print("ERR: 没有找到截帧（需 NNN_HHMMSS.jpg 命名）", file=sys.stderr)
        sys.exit(1)
    for it in items:
        sec = int(it["ts"])
        it["hhmmss"] = "%02d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)

    dropped = 0
    if a.dedup:
        items, dropped = dedup(items, a.dedup_threshold, mf)
        if mf is not None and a.manifest:
            with open(a.manifest, "w", encoding="utf-8") as f:
                json.dump(mf, f, ensure_ascii=False, indent=2)
    kept = [x for x in items if x.get("keep", True)]

    sheets = make_sheets(kept, a.out, a.per_sheet, a.cell_width) if a.sheet else []
    ocr_path = None
    if a.ocr:
        tess = find_tesseract()
        if tess:
            ocr_path = run_ocr(kept, a.out, a.ocr_lang, tess)
        else:
            print("WARN: 未找到 tesseract，跳过 OCR（不影响主流程）", file=sys.stderr)

    q = write_queue(kept, a.out, sheets, ocr_path, len(items), dropped)
    print(json.dumps({
        "frames_total": len(items),
        "dedup_dropped": dropped,
        "kept": len(kept),
        "sheets": [os.path.basename(s) for s in sheets],
        "ocr": os.path.basename(ocr_path) if ocr_path else None,
        "review_queue": os.path.abspath(q),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
