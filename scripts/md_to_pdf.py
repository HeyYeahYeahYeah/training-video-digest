#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
md_to_pdf.py — 把「带界面截图」的 Markdown 渲染成 A4 PDF（Chrome/Edge headless）。

与通用 md→pdf 方案的关键差异：
    md 里的相对图片路径（_frames/xxx.jpg）会被转成 base64 data URI 再交给 Chrome。
    因为 HTML 必须在系统临时 ASCII 目录构建以规避中文路径问题，
    不内嵌的话所有截图在 PDF 里都会变成裂图。

用法: python md_to_pdf.py <input.md> <output.pdf>
退出码: 0 成功 / 1 输入缺失 / 2 参数错 / 3 无浏览器 / 4 打印失败
"""
import base64
import importlib.util
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse


def ensure_markdown():
    if importlib.util.find_spec("markdown") is None:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "markdown"])
    import markdown  # noqa
    return markdown


def find_browser():
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    for name in ("google-chrome", "chrome", "msedge", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


CSS = """
@page { size: A4; margin: 16mm 15mm 18mm 15mm; }
* { box-sizing: border-box; }
body {
  font-family: "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", sans-serif;
  font-size: 10.8pt; line-height: 1.75; color: #23272b; margin: 0; padding: 0;
}
h1 {
  font-size: 21pt; color: #1c3c2e; margin: 0 0 6px 0;
  border-bottom: 3px solid #2e7d5b; padding-bottom: 10px; letter-spacing: 0.5px;
}
h2 {
  font-size: 14.5pt; color: #2e7d5b; margin: 26px 0 10px 0;
  border-left: 5px solid #2e7d5b; padding-left: 11px; page-break-after: avoid;
}
h3 { font-size: 12pt; color: #245; margin: 18px 0 8px 0; page-break-after: avoid; }
p { margin: 8px 0; text-align: justify; }
strong { color: #1c3c2e; }
blockquote {
  border-left: 4px solid #7cb342; background: #f4f9ef;
  margin: 12px 0; padding: 10px 16px; color: #3a3f36;
  border-radius: 0 4px 4px 0; page-break-inside: avoid;
}
blockquote p { margin: 4px 0; line-height: 1.9; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 9.8pt; page-break-inside: avoid; }
th { background: #2e7d5b; color: #fff; padding: 7px 9px; text-align: left; font-weight: 600; border: 1px solid #2e7d5b; }
td { border: 1px solid #d3dbd5; padding: 6px 9px; vertical-align: top; }
tr:nth-child(even) td { background: #f5f9f7; }
code { background: #eef2ef; padding: 1px 5px; border-radius: 3px; font-family: Consolas, "Courier New", monospace; font-size: 9.5pt; color: #234; }
ul, ol { margin: 8px 0; padding-left: 24px; }
li { margin: 4px 0; }
hr { border: none; border-top: 1px solid #dfe5e1; margin: 22px 0; }
em { color: #6a7268; }
img {
  display: block; max-width: 100%; max-height: 118mm; height: auto;
  margin: 10px auto 4px auto; border: 1px solid #d8e0db; border-radius: 3px;
  page-break-inside: avoid;
}
p > em:only-child { display: block; text-align: center; font-size: 9pt; color: #6a7268; margin-bottom: 12px; }
"""

HTML_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>培训录屏图文纪要</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""

IMG_RE = re.compile(r"!\[(.*?)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", re.S)


def inline_images(text, md_dir):
    """把 md 里的本地图片路径换成 base64 data URI。返回 (新文本, 成功数, 失败列表)。"""
    ok, failed = 0, []

    def repl(m):
        nonlocal ok
        alt, raw = m.group(1), m.group(2)
        if raw.startswith(("data:", "http://", "https://")):
            return m.group(0)
        path = urllib.parse.unquote(raw.strip("<>").replace("\\", "/"))
        if not os.path.isabs(path):
            path = os.path.join(md_dir, path)
        if not os.path.isfile(path):
            failed.append(raw)
            return m.group(0)
        mime = mimetypes.guess_type(path)[0] or "image/jpeg"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        ok += 1
        return "![%s](data:%s;base64,%s)" % (alt, mime, b64)

    return IMG_RE.sub(repl, text), ok, failed


def main():
    if len(sys.argv) < 3:
        print("usage: md_to_pdf.py <input.md> <output.pdf>", file=sys.stderr)
        sys.exit(2)

    md_path = os.path.abspath(sys.argv[1])
    pdf_path = os.path.abspath(sys.argv[2])
    if not os.path.isfile(md_path):
        print("ERR: markdown not found: %s" % md_path, file=sys.stderr)
        sys.exit(1)

    browser = find_browser()
    if not browser:
        print("ERR: no Chrome/Edge/chromium found", file=sys.stderr)
        sys.exit(3)

    markdown = ensure_markdown()
    with open(md_path, encoding="utf-8") as f:
        text = f.read()

    text, n_img, failed = inline_images(text, os.path.dirname(md_path))
    if failed:
        print("WARN: 以下图片未找到，PDF 中会显示为裂图：%s" % ", ".join(failed), file=sys.stderr)
    print("INLINE_IMAGES: %d 张已内嵌" % n_img, file=sys.stderr)

    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
    )
    html = HTML_TMPL.format(css=CSS, body=body)

    workdir = tempfile.mkdtemp(prefix="tvd2pdf_")
    try:
        html_path = os.path.join(workdir, "doc.html")
        tmp_pdf = os.path.join(workdir, "out.pdf")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        file_url = "file:///" + html_path.replace(os.sep, "/").replace(" ", "%20")
        cmd = [
            browser, "--headless", "--disable-gpu", "--no-sandbox",
            "--user-data-dir=%s" % os.path.join(workdir, "profile"),
            "--no-pdf-header-footer",
            "--print-to-pdf=%s" % tmp_pdf,
            "--virtual-time-budget=%d" % (8000 + n_img * 300),
            file_url,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)

        if not os.path.isfile(tmp_pdf) or os.path.getsize(tmp_pdf) == 0:
            print("ERR: pdf not produced\n%s" % (r.stderr[-1200:] if r.stderr else ""), file=sys.stderr)
            sys.exit(4)

        out_dir = os.path.dirname(pdf_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        shutil.copyfile(tmp_pdf, pdf_path)
        print("OK: %s (%d bytes, %d images)" % (pdf_path, os.path.getsize(pdf_path), n_img))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
