#!/usr/bin/env python3
"""Build the SOP from the Markdown master.

python3 build.py                       src/*.md        -> SOP.md, SOP.html, SOP.docx
python3 build.py quickstart-src QUICKSTART      quickstart-src/*.md -> QUICKSTART.md/.html/.docx
(each also writes <name>.artifact.html, the fragment used for the shareable web page)

Screenshots: save captures as images/S-nn.png. Any "SCREENSHOT S-nn" placeholder whose
image exists is replaced by the picture in the built outputs; src/ is never changed.
Needs pandoc (pip install pypandoc_binary gives you one).
"""
import glob, os, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
try:
    import pypandoc
    PANDOC = pypandoc.get_pandoc_path()
except Exception:
    PANDOC = "pandoc"

import sys
SRC = sys.argv[1] if len(sys.argv) > 1 else "src"
OUT = sys.argv[2] if len(sys.argv) > 2 else "SOP"
parts = sorted(glob.glob(f"{SRC}/*.md"))
text = "\n\n".join(open(p, encoding="utf-8").read().strip() for p in parts) + "\n"

def place_image(m):
    sid, desc = m.group(1), m.group(2).strip()
    img = f"images/{sid}.png"
    if os.path.exists(img):
        return f"![{sid}: {desc}]({img})"
    return m.group(0)

# a placeholder is one blockquote line: > **SCREENSHOT S-nn:** description
text = re.sub(r"^> \*\*SCREENSHOT ([A-Z]-\d+):\*\* (.+)$", place_image, text, flags=re.M)
open(f"{OUT}.md", "w", encoding="utf-8").write(text)

def pandoc(*args):
    subprocess.run([PANDOC, f"{OUT}.md", "-f", "gfm+yaml_metadata_block", *args], check=True)

pandoc("-o", f"{OUT}.docx", "--toc", "--toc-depth=2")
pandoc("-t", "html5", "--template", "template.html", "--toc", "--toc-depth=2",
       "--resource-path", ".", "-o", f"{OUT}.artifact.html")

html = open(f"{OUT}.artifact.html", encoding="utf-8").read()
# style the two callout types and wrap tables so they scroll on phones
html = re.sub(r"<blockquote>\s*<p><strong>SCREENSHOT", '<blockquote class="shot">\n<p><strong>SCREENSHOT', html)
html = re.sub(r"<blockquote>\s*<p><strong>PRODUCTION", '<blockquote class="prod">\n<p><strong>PRODUCTION', html)
html = html.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
open(f"{OUT}.artifact.html", "w", encoding="utf-8").write(html)
open(f"{OUT}.html", "w", encoding="utf-8").write(
    '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    + html.replace("<div class=\"wrap\">", "</head><body>\n<div class=\"wrap\">", 1)
    + "\n</body></html>\n")
print("built:", ", ".join(f"{OUT}{x}" for x in [".md", ".html", ".docx", ".artifact.html"]), "from", len(parts), "parts")
