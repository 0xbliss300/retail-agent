# 程序化排版 QA：估算文本高度检测溢出、越界与文本框重叠
import math
import sys

from pptx import Presentation

path = sys.argv[1] if len(sys.argv) > 1 else "AI_Agent_进化之路.pptx"
prs = Presentation(path)
EMU = 914400.0
WIDE = set("—–·→≈×「」《》）(")


def char_w(ch, pt):
    if ord(ch) > 0x2E7F or ch in WIDE:
        return pt / 72 * 1.02
    if ch == " ":
        return pt / 72 * 0.35
    return pt / 72 * 0.55


issues = []
for si, slide in enumerate(prs.slides, 1):
    boxes = []
    for sh in slide.shapes:
        if not sh.has_text_frame:
            continue
        txt = sh.text_frame.text
        if not txt.strip():
            continue
        x, y = sh.left / EMU, sh.top / EMU
        w, h = sh.width / EMU, sh.height / EMU
        boxes.append((x, y, w, h, txt[:16].replace("\n", "|")))
        if x < -0.01 or y < -0.01 or x + w > 13.34 or y + h > 7.51:
            issues.append(f"S{si} 越界 ({x:.2f},{y:.2f},{w:.2f},{h:.2f}) {txt[:16]!r}")
        total = 0.0
        for para in sh.text_frame.paragraphs:
            ptxt = "".join(r.text for r in para.runs)
            if not ptxt:
                total += 0.1
                continue
            sizes = [r.font.size.pt for r in para.runs if r.font.size]
            pt = max(sizes) if sizes else 12
            est_w = sum(char_w(c, pt) for c in ptxt)
            lines = max(1, math.ceil(est_w / max(w - 0.05, 0.3)))
            total += lines * pt / 72 * 1.32
        if total > h + 0.08:
            issues.append(f"S{si} 溢出 估{total:.2f} > 框{h:.2f} {txt[:20]!r}")
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            if ox > 0.05 and oy > 0.05:
                issues.append(f"S{si} 重叠 {a[4]!r} × {b[4]!r} ({ox:.2f}x{oy:.2f})")

print("\n".join(issues) if issues else "QA CLEAN")
print(f"slides={len(prs.slides.__iter__.__self__._sldIdLst)}")
