from __future__ import annotations

import math
from pathlib import Path
import sys
import argparse
import os

def add_dependency_paths() -> None:
    candidates: list[Path] = []
    env_paths = os.environ.get("CODEX_CAD_VENDOR_PATH", "")
    for item in env_paths.split(os.pathsep):
        if item:
            candidates.append(Path(item))
    roots = [Path(__file__).resolve().parent, Path.cwd()]
    for root in roots:
        for parent in [root, *root.parents]:
            candidates.append(parent / ".vendor")
            candidates.append(parent / "third_party")
    for path in reversed(candidates):
        if path.exists():
            value = str(path)
            if value not in sys.path:
                sys.path.insert(0, value)


add_dependency_paths()

try:
    from pypdf import PdfReader
    from pypdf.generic import ContentStream
except Exception:
    from PyPDF2 import PdfReader
    from PyPDF2.generic import ContentStream


DEFAULT_OUT = Path("draw_pdf_direct.vbs")


def mmul(m1, m2):
    a, b, c, d, e, f = m1
    g, h, i, j, k, l = m2
    return (
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * l + e,
        b * k + d * l + f,
    )


def tpoint(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def bezier(p0, p1, p2, p3, steps=12):
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def vb_str(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def rotate_point_for_page(x, y, rotation, width, height):
    rotation = int(rotation or 0) % 360
    if rotation == 90:
        return y, width - x
    if rotation == 180:
        return width - x, height - y
    if rotation == 270:
        return height - y, x
    return x, y


def parse_pdf(src: str | Path, apply_page_rotation: bool = True, return_metadata: bool = False):
    pdf_path = Path(src)
    reader = PdfReader(str(pdf_path))
    page = reader.pages[0]
    rotation = page.get("/Rotate", 0) if apply_page_rotation else 0
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    cs = ContentStream(page.get_contents(), reader)

    ctm = (1, 0, 0, 1, 0, 0)
    stack = []
    path = []
    cur = None
    start = None
    in_text = False
    text_matrix = (1, 0, 0, 1, 0, 0)
    font_size = 12.0
    entities = []
    texts = []

    def operand_to_text(obj) -> str:
        try:
            s = str(obj)
        except Exception:
            return ""
        # Strip PDF literal wrappers occasionally returned by parser.
        if s.startswith("(") and s.endswith(")"):
            s = s[1:-1]
        return s.strip()

    def flush_path(stroke=True):
        nonlocal path
        if stroke:
            entities.extend(path)
        path = []

    for operands, op_raw in cs.operations:
        op = op_raw.decode("latin1") if isinstance(op_raw, bytes) else op_raw
        nums = [float(x) if isinstance(x, (int, float)) else x for x in operands]

        if op == "q":
            stack.append((ctm, cur, start, list(path), in_text, text_matrix, font_size))
        elif op == "Q":
            ctm, cur, start, path, in_text, text_matrix, font_size = stack.pop()
        elif op == "cm":
            ctm = mmul(ctm, tuple(nums))
        elif op == "m":
            cur = tpoint(ctm, nums[0], nums[1])
            start = cur
        elif op == "l":
            p = tpoint(ctm, nums[0], nums[1])
            if cur is not None and math.dist(cur, p) >= 0.01:
                path.append((cur, p))
            cur = p
        elif op == "re":
            x, y, w, h = nums
            pts = [tpoint(ctm, x, y), tpoint(ctm, x + w, y), tpoint(ctm, x + w, y + h), tpoint(ctm, x, y + h)]
            path.extend([(pts[0], pts[1]), (pts[1], pts[2]), (pts[2], pts[3]), (pts[3], pts[0])])
            cur = pts[0]
            start = pts[0]
        elif op == "c" and cur is not None:
            p1 = tpoint(ctm, nums[0], nums[1])
            p2 = tpoint(ctm, nums[2], nums[3])
            p3 = tpoint(ctm, nums[4], nums[5])
            last = cur
            for p in bezier(cur, p1, p2, p3):
                path.append((last, p))
                last = p
            cur = p3
        elif op == "h":
            if cur is not None and start is not None:
                path.append((cur, start))
                cur = start
        elif op in ("S", "s"):
            if op == "s" and cur is not None and start is not None:
                path.append((cur, start))
            flush_path(True)
        elif op in ("f", "f*", "n"):
            flush_path(op.startswith("f"))
        elif op == "BT":
            in_text = True
            text_matrix = (1, 0, 0, 1, 0, 0)
        elif op == "ET":
            in_text = False
        elif op == "Tm":
            text_matrix = tuple(nums)
        elif op == "Tf":
            font_size = float(nums[1])
        elif op == "Tj" and in_text:
            text = operand_to_text(operands[0])
            if text:
                tm = mmul(ctm, text_matrix)
                x, y = tpoint(tm, 0, 0)
                sx = math.hypot(tm[0], tm[1]) or 1
                texts.append((x, y, text, font_size * sx))
        elif op == "TJ" and in_text:
            chunks = []
            seq = operands[0] if operands else []
            for item in seq:
                # numbers are kerning offsets, strings are text
                if isinstance(item, (int, float)):
                    continue
                t = operand_to_text(item)
                if t:
                    chunks.append(t)
            text = "".join(chunks).strip()
            if text:
                tm = mmul(ctm, text_matrix)
                x, y = tpoint(tm, 0, 0)
                sx = math.hypot(tm[0], tm[1]) or 1
                texts.append((x, y, text, font_size * sx))

    if rotation:
        entities = [
            (
                rotate_point_for_page(a[0], a[1], rotation, width, height),
                rotate_point_for_page(b[0], b[1], rotation, width, height),
            )
            for a, b in entities
        ]
        texts = [
            (*rotate_point_for_page(x, y, rotation, width, height), text, h)
            for x, y, text, h in texts
        ]

    all_pts = [p for line in entities for p in line] + [(x, y) for x, y, _, _ in texts]
    minx = min(x for x, _ in all_pts)
    miny = min(y for _, y in all_pts)
    norm_lines = [((a[0] - minx, a[1] - miny), (b[0] - minx, b[1] - miny)) for a, b in entities]
    norm_texts = [(x - minx, y - miny, text, h) for x, y, text, h in texts]
    if return_metadata:
        metadata = {
            "minx": minx,
            "miny": miny,
            "width": width,
            "height": height,
            "rotation": int(rotation or 0) % 360,
        }
        return norm_lines, norm_texts, metadata
    return norm_lines, norm_texts


def write_vbs(lines, texts, out_path: str | Path = DEFAULT_OUT):
    out = []
    out.append('On Error Resume Next\n')
    out.append('Set acad = GetObject(, "AutoCAD.Application")\n')
    out.append('If Err.Number <> 0 Then\n')
    out.append('  Err.Clear\n')
    out.append('  Set acad = CreateObject("AutoCAD.Application")\n')
    out.append('End If\n')
    out.append('On Error GoTo 0\n')
    out.append('acad.Visible = True\n')
    out.append('If acad.Documents.Count = 0 Then acad.Documents.Add\n')
    out.append('Set doc = acad.ActiveDocument\n')
    out.append('Set ms = doc.ModelSpace\n')
    out.append('On Error Resume Next\n')
    out.append('Set layerWall = doc.Layers.Add("PDF_DIRECT_WALL")\n')
    out.append('Set layerText = doc.Layers.Add("PDF_DIRECT_TEXT")\n')
    out.append('On Error GoTo 0\n')
    out.append('doc.ActiveLayer = doc.Layers.Item("PDF_DIRECT_WALL")\n')
    out.append('doc.Utility.Prompt vbCrLf & "Codex is drawing PDF plan directly into ModelSpace..." & vbCrLf\n')

    out.append('Sub AddLn(x1, y1, x2, y2)\n')
    out.append('  p1 = Array(CDbl(x1), CDbl(y1), 0.0)\n')
    out.append('  p2 = Array(CDbl(x2), CDbl(y2), 0.0)\n')
    out.append('  Set e = ms.AddLine(p1, p2)\n')
    out.append('  e.Layer = "PDF_DIRECT_WALL"\n')
    out.append('End Sub\n')
    out.append('Sub AddTxt(x, y, h, s)\n')
    out.append('  p = Array(CDbl(x), CDbl(y), 0.0)\n')
    out.append('  Set e = ms.AddText(s, p, CDbl(h))\n')
    out.append('  e.Layer = "PDF_DIRECT_TEXT"\n')
    out.append('End Sub\n')

    for (x1, y1), (x2, y2) in lines:
        out.append(f"AddLn {x1:.6f}, {y1:.6f}, {x2:.6f}, {y2:.6f}\n")
    for x, y, text, h in texts:
        out.append(f"AddTxt {x:.6f}, {y:.6f}, {h:.6f}, {vb_str(text)}\n")

    out.append('doc.Application.ZoomExtents\n')
    out.append('doc.Utility.Prompt vbCrLf & "Codex direct draw finished."\n')
    Path(out_path).write_text("".join(out), encoding="utf-16")


def main():
    parser = argparse.ArgumentParser(description="Parse a PDF vector plan and emit a direct AutoCAD VBS drawer.")
    parser.add_argument("--pdf", required=True, help="PDF path to parse")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="VBS output path")
    args = parser.parse_args()

    lines, texts = parse_pdf(args.pdf)
    write_vbs(lines, texts, args.out)
    print(f"wrote {Path(args.out).resolve()}")
    print(f"lines={len(lines)} text={len(texts)}")


if __name__ == "__main__":
    main()
