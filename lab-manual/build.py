#!/usr/bin/env python3
"""Build the field manual from the Markdown master.

python3 build.py            src/*.md -> LabManual.md, LabManual.html, LabManual.docx, LabManual.pdf
                            (+ LabManual.artifact.html, the fragment used for the shareable web page)

Conventions in src/*.md:
  **user@host — real capture**   followed by a ```console block  -> terminal window with a title bar
  > **NOTE** / **TIP** / **GOTCHA — REAL ISSUE** / **EXAM TIP** / **LAB — ...** / **PRODUCTION** -> typed boxes
  > **SCREENSHOT S-nn:** text   -> replaced by images/S-nn.png when that file exists
Needs pandoc (pip install pypandoc_binary) and, for the PDF, playwright with a Chromium.
"""
import glob, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
try:
    import pypandoc
    PANDOC = pypandoc.get_pandoc_path()
except Exception:
    PANDOC = "pandoc"

SRC, OUT = "src", "LabManual"
parts = sorted(glob.glob(f"{SRC}/*.md"))
text = "\n\n".join(open(p, encoding="utf-8").read().strip() for p in parts) + "\n"

def place_image(m):
    sid, desc = m.group(1), m.group(2).strip()
    img = f"images/{sid}.png"
    return f"![Screenshot {sid}: {desc}]({img})" if os.path.exists(img) else m.group(0)

text = re.sub(r"^> \*\*SCREENSHOT ([A-Z]-\d+):\*\* (.+)$", place_image, text, flags=re.M)
open(f"{OUT}.md", "w", encoding="utf-8").write(text)

def pandoc(*args):
    subprocess.run([PANDOC, f"{OUT}.md", "-f", "gfm+yaml_metadata_block", *args], check=True)

pandoc("-o", f"{OUT}.docx", "--toc", "--toc-depth=1")
pandoc("-t", "html5", "--wrap=none", "--template", "template.html", "--toc", "--toc-depth=2",
       "--syntax-highlighting=none", "--resource-path", ".", "-o", f"{OUT}.artifact.html")

h = open(f"{OUT}.artifact.html", encoding="utf-8").read()

# version strip on the cover: "a · b · c" -> chips
h = re.sub(r'<div class="versions">(.*?)</div>',
           lambda m: '<div class="versions">' + "".join(f"<span>{v.strip()}</span>" for v in m.group(1).split("·")) + "</div>",
           h, count=1, flags=re.S)

# terminal windows: title paragraph + console block
def term(m):
    title, body = m.group(1), m.group(2)
    body = re.sub(r"^(\[[^\]\n]+\]\$ )", r'<span class="prompt">\1</span>', body, flags=re.M)
    return ('<div class="term"><div class="bar"><i></i><i></i><i></i>'
            f'<span>{title}</span></div><pre><code>{body}</code></pre></div>')
h = re.sub(r'<p><strong>([^<]+? — real capture)</strong></p>\s*<pre class="console"><code>(.*?)</code></pre>',
           term, h, flags=re.S)

# typed callout boxes
for key, cls in [("GOTCHA", "gotcha"), ("EXAM TIP", "exam"), ("TIP", "tip"), ("LAB", "lab"),
                 ("PRODUCTION", "prod"), ("SCREENSHOT", "shot"), ("NOTE", "note")]:
    h = re.sub(r"<blockquote>\s*<p><strong>" + key, f'<blockquote class="{cls}">\n<p><strong>' + key, h)

# chapter openers: "Chapter 3 · Title" -> eyebrow + title
h = re.sub(r'(<h1 id="[^"]*">)(Chapter \d+|Appendix [A-Z]) · (.*?)</h1>',
           r'\1<span class="eyebrow">\2</span>\3</h1>', h)
h = h.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
open(f"{OUT}.artifact.html", "w", encoding="utf-8").write(h)

page = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        + h.replace('<header class="cover">', '</head><body>\n<header class="cover">', 1)
        + "\n</body></html>\n")
open(f"{OUT}.html", "w", encoding="utf-8").write(page)

built = [".md", ".html", ".docx", ".artifact.html"]
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:   # playwright version newer than the installed browser
            b = p.chromium.launch(executable_path=os.environ.get("CHROME", "/opt/pw-browsers/chromium"))
        pg = b.new_page()
        pg.goto("file://" + os.path.join(HERE, f"{OUT}.html"), wait_until="networkidle")
        pg.emulate_media(media="print")
        foot = ('<div style="font:8px sans-serif;color:#777;width:100%;padding:0 12mm;display:flex;justify-content:space-between">'
                '<span>The Lab Manual · Migration Edition</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')
        pg.pdf(path=f"{OUT}.pdf", format="A4", print_background=True, display_header_footer=True,
               header_template="<div></div>", footer_template=foot,
               margin={"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"})
        b.close()
    built.append(".pdf")
except Exception as e:
    print("PDF skipped:", e, file=sys.stderr)

print("built:", ", ".join(OUT + x for x in built), "from", len(parts), "parts")
