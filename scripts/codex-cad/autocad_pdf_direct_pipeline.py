from __future__ import annotations

from pathlib import Path
import argparse
import os
import re
import sys
from typing import List, Tuple

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

from pdf_direct_draw_to_autocad import parse_pdf


DEFAULT_OUT_DIR = Path.cwd()


LineSeg = Tuple[Tuple[float, float], Tuple[float, float]]
TextEnt = Tuple[float, float, str, float]
NumText = Tuple[float, float, float, str]
MeasureCandidate = Tuple[float, float, str]
ScalePair = Tuple[float, float, float, str, int]


def lisp_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def extract_numeric_texts(texts: List[TextEnt]) -> List[NumText]:
    out: List[NumText] = []
    for x, y, text, _h in texts:
        clean = text.replace(",", "").strip()
        m = re.search(r"(\d+(?:\.\d+)?)", clean)
        if not m:
            continue
        value = float(m.group(1))
        if value <= 0:
            continue
        out.append((x, y, value, "any"))
    return out


def bbox_from_lines(lines: List[LineSeg]) -> tuple[float, float]:
    pts = [p for line in lines for p in line]
    if not pts:
        return 0.0, 0.0
    maxx = max(x for x, _ in pts)
    maxy = max(y for _, y in pts)
    return maxx, maxy


def ocr_numeric_texts(
    pdf_path: str | Path,
    lines: List[LineSeg],
    out_dir: Path,
    zoom: float = 3.0,
) -> tuple[List[NumText], str]:
    try:
        import fitz
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:
        return [], f"ocr_import_failed:{exc.__class__.__name__}"

    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    if doc.page_count == 0:
        return [], "ocr_empty_pdf"
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img_path = out_dir / "codex_ocr_page.png"
    pix.save(str(img_path))

    engine = RapidOCR()
    result, _ = engine(str(img_path))
    if not result:
        return [], "ocr_no_result"

    _meta_lines, _meta_texts, metadata = parse_pdf(pdf_path, return_metadata=True)
    minx = float(metadata["minx"])
    miny = float(metadata["miny"])
    nums: List[NumText] = []
    for box, text, score in result:
        try:
            score_value = float(score)
        except Exception:
            score_value = 0.0
        if score_value < 0.45:
            continue
        clean = re.sub(r"[^0-9.]", "", str(text))
        if not re.fullmatch(r"\d{2,6}(?:\.\d+)?", clean):
            continue
        value = float(clean)
        # Ignore area labels like 21.527 when dimensions are expected in mm.
        if value < 100:
            continue
        xs = [float(pt[0]) for pt in box]
        ys = [float(pt[1]) for pt in box]
        orientation = "horizontal" if (max(xs) - min(xs)) >= (max(ys) - min(ys)) else "vertical"
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        x = (cx / zoom) - minx
        y = ((float(pix.height) - cy) / zoom) - miny
        nums.append((x, y, value, orientation))
    return nums, f"ocr_candidates={len(nums)}"


def measure_candidates_from_text(lines: List[LineSeg], x: float, y: float, orientation: str = "any") -> List[MeasureCandidate]:
    candidates: List[MeasureCandidate] = []
    eps = 1e-6
    cad_w, cad_h = bbox_from_lines(lines)
    extent = max(cad_w, cad_h, 1.0)
    near = extent * 0.08
    line_margin = extent * 0.03
    intersect_tol = max(extent * 0.004, 0.5)
    min_dim_line_len = extent * 0.025
    min_ext_line_len = extent * 0.012

    if orientation in ("horizontal", "any"):
        dim_lines = []
        for (x1, y1), (x2, y2) in lines:
            if abs(y2 - y1) > eps:
                continue
            xmin = min(x1, x2)
            xmax = max(x1, x2)
            length = xmax - xmin
            if length < min_dim_line_len:
                continue
            if abs(y - y1) <= near and xmin - line_margin <= x <= xmax + line_margin:
                dim_lines.append((abs(y - y1), xmin, xmax, y1))
        dim_lines.sort(key=lambda t: t[0])
        for dist, xmin, xmax, y_line in dim_lines[:8]:
            verticals = []
            for (vx1, vy1), (vx2, vy2) in lines:
                if abs(vx2 - vx1) > eps:
                    continue
                vymin = min(vy1, vy2)
                vymax = max(vy1, vy2)
                if vymax - vymin < min_ext_line_len:
                    continue
                if vymin - intersect_tol <= y_line <= vymax + intersect_tol:
                    vx = (vx1 + vx2) / 2.0
                    if xmin - line_margin <= vx <= xmax + line_margin:
                        verticals.append(vx)
            if len(verticals) < 2:
                continue
            verticals = sorted(set(round(vx, 6) for vx in verticals))
            lefts = [vx for vx in verticals if vx < x]
            rights = [vx for vx in verticals if vx > x]
            if not lefts or not rights:
                continue
            left = lefts[-1]
            right = rights[0]
            if right > left:
                candidates.append((dist, right - left, "dimension_intersection_horizontal_adjacent"))

    if orientation in ("vertical", "any"):
        dim_lines = []
        for (x1, y1), (x2, y2) in lines:
            if abs(x2 - x1) > eps:
                continue
            ymin = min(y1, y2)
            ymax = max(y1, y2)
            length = ymax - ymin
            if length < min_dim_line_len:
                continue
            if abs(x - x1) <= near and ymin - line_margin <= y <= ymax + line_margin:
                dim_lines.append((abs(x - x1), x1, ymin, ymax))
        dim_lines.sort(key=lambda t: t[0])
        for dist, x_line, ymin, ymax in dim_lines[:8]:
            horizontals = []
            for (hx1, hy1), (hx2, hy2) in lines:
                if abs(hy2 - hy1) > eps:
                    continue
                hxmin = min(hx1, hx2)
                hxmax = max(hx1, hx2)
                if hxmax - hxmin < min_ext_line_len:
                    continue
                if hxmin - intersect_tol <= x_line <= hxmax + intersect_tol:
                    hy = (hy1 + hy2) / 2.0
                    if ymin - line_margin <= hy <= ymax + line_margin:
                        horizontals.append(hy)
            if len(horizontals) < 2:
                continue
            horizontals = sorted(set(round(hy, 6) for hy in horizontals))
            lowers = [hy for hy in horizontals if hy < y]
            uppers = [hy for hy in horizontals if hy > y]
            if not lowers or not uppers:
                continue
            lower = lowers[-1]
            upper = uppers[0]
            if upper > lower:
                candidates.append((dist, upper - lower, "dimension_intersection_vertical_adjacent"))
    return [c for c in candidates if c[1] > eps]


def collect_dimension_intersection_pairs(lines: List[LineSeg], nums: List[NumText]) -> List[ScalePair]:
    pairs: List[ScalePair] = []
    seen: set[tuple[int, float, str]] = set()
    for idx, (x, y, value, orientation) in enumerate(nums):
        for _dist, measured, kind in measure_candidates_from_text(lines, x, y, orientation):
            if measured <= 0:
                continue
            if kind.startswith("dimension_intersection_"):
                key = (idx, round(measured, 6), kind)
                if key in seen:
                    continue
                seen.add(key)
                scale = value / measured
                pairs.append((scale, value, measured, kind, idx))
    return pairs


def pick_median_scale(pairs: List[ScalePair], tolerance: float = 0.03) -> tuple[float, str, List[ScalePair]] | None:
    if len(pairs) < 3:
        return None

    best_group: List[ScalePair] = []
    for scale, *_rest in pairs:
        group = [p for p in pairs if abs(p[0] - scale) / max(scale, 1e-9) <= tolerance]
        by_label: dict[int, ScalePair] = {}
        for item in sorted(group, key=lambda p: abs(p[0] - scale)):
            by_label.setdefault(item[4], item)
        distinct_group = list(by_label.values())
        if len(distinct_group) > len(best_group):
            best_group = distinct_group

    if len(best_group) < 3:
        return None
    best_group.sort(key=lambda p: p[0])
    scale = best_group[len(best_group) // 2][0]
    used = ";".join(f"{value:g}/{measured:.3f}/{kind}" for _, value, measured, kind, _idx in best_group)
    return scale, f"dimension_intersection_median,count={len(best_group)},used={used}", best_group


def write_measurement_debug(
    path: Path,
    nums: List[NumText],
    pairs: List[ScalePair],
    selected: List[ScalePair],
) -> None:
    selected_keys = {(idx, round(measured, 6), kind) for _scale, _value, measured, kind, idx in selected}
    rows = [
        "rule=dimension_line_intersections_only",
        "minimum_valid_labels=3",
        "dimensions=" + describe_dimensions(nums),
        "measurements:",
    ]
    for item in sorted(pairs, key=lambda p: (p[4], p[0])):
        scale, value, measured, kind, idx = item
        x, y, _value, orientation = nums[idx]
        marker = "selected" if (idx, round(measured, 6), kind) in selected_keys else "candidate"
        rows.append(
            f"{marker}\tidx={idx}\tvalue={value:g}\torientation={orientation}\t"
            f"pos=({x:.3f},{y:.3f})\tmeasured={measured:.6f}\tscale={scale:.12f}\tkind={kind}"
        )
    path.write_text("\n".join(rows), encoding="utf-8")


def calc_global_scale(
    lines: List[LineSeg],
    texts: List[TextEnt],
    measurements_path: Path,
    pdf_path: str | Path | None = None,
    use_ocr: bool = False,
    out_dir: Path | None = None,
) -> tuple[float, str, List[NumText]]:
    nums = extract_numeric_texts(texts)
    source = "pdf_text"
    ocr_reason = ""
    if not nums and use_ocr and pdf_path is not None:
        nums, ocr_reason = ocr_numeric_texts(pdf_path, lines, out_dir or measurements_path.parent)
        source = "ocr"
    if not nums:
        write_measurement_debug(measurements_path, nums, [], [])
        return 1.0, ocr_reason or "no_numeric_text_found", nums

    pairs = collect_dimension_intersection_pairs(lines, nums)
    picked = pick_median_scale(pairs)
    if picked is not None:
        scale, reason, selected = picked
        write_measurement_debug(measurements_path, nums, pairs, selected)
        return scale, f"source={source},{reason}", nums
    write_measurement_debug(measurements_path, nums, pairs, [])
    return 1.0, f"source={source},need_at_least_3_dimension_intersections", nums


def describe_dimensions(nums: List[NumText]) -> str:
    if not nums:
        return "none"
    return ";".join(f"{value:g}@({x:.3f},{y:.3f})/{orientation}" for x, y, value, orientation in nums)


def generate_lsp(lines: List[LineSeg], texts: List[TextEnt], scale_factor: float) -> str:
    out = []
    out.append("(defun codex-make-layer (name color)\n")
    out.append("  (if (not (tblsearch \"LAYER\" name))\n")
    out.append("    (entmake (list (cons 0 \"LAYER\") (cons 100 \"AcDbSymbolTableRecord\") (cons 100 \"AcDbLayerTableRecord\") (cons 2 name) (cons 70 0) (cons 62 color) (cons 6 \"CONTINUOUS\")))\n")
    out.append("  )\n")
    out.append(")\n")
    out.append("(defun codex-delete-layer-entities (layer / ss idx ent)\n")
    out.append("  (setq ss (ssget \"_X\" (list (cons 8 layer))))\n")
    out.append("  (if ss\n")
    out.append("    (progn\n")
    out.append("      (setq idx 0)\n")
    out.append("      (repeat (sslength ss)\n")
    out.append("        (setq ent (ssname ss idx))\n")
    out.append("        (entdel ent)\n")
    out.append("        (setq idx (1+ idx))\n")
    out.append("      )\n")
    out.append("    )\n")
    out.append("  )\n")
    out.append(")\n")
    out.append("(defun codex-line (x1 y1 x2 y2)\n")
    out.append("  (entmake (list (cons 0 \"LINE\") (cons 8 \"PDF_DIRECT_WALL\") (cons 10 (list x1 y1 0.0)) (cons 11 (list x2 y2 0.0))))\n")
    out.append(")\n")
    out.append("(defun codex-text (x y h s)\n")
    out.append("  (entmake (list (cons 0 \"TEXT\") (cons 8 \"PDF_DIRECT_TEXT\") (cons 10 (list x y 0.0)) (cons 40 h) (cons 1 s) (cons 50 0.0)))\n")
    out.append(")\n")
    out.append("(defun codex-scale-layers (factor / ss)\n")
    out.append("  (setq ss (ssget \"_X\" (list (cons 8 \"PDF_DIRECT_WALL,PDF_DIRECT_TEXT\"))))\n")
    out.append("  (if (and ss (> factor 0.0))\n")
    out.append("    (command \"_.SCALE\" ss \"\" \"0,0\" factor)\n")
    out.append("  )\n")
    out.append(")\n")
    out.append("(defun c:CODEXAUTODRAWPDF (/ oldlayer)\n")
    out.append("  (setq oldlayer (getvar \"CLAYER\"))\n")
    out.append("  (codex-make-layer \"PDF_DIRECT_WALL\" 7)\n")
    out.append("  (codex-make-layer \"PDF_DIRECT_TEXT\" 2)\n")
    out.append("  (command \"_.UNDO\" \"_BE\")\n")
    out.append("  (codex-delete-layer-entities \"PDF_DIRECT_WALL\")\n")
    out.append("  (codex-delete-layer-entities \"PDF_DIRECT_TEXT\")\n")
    out.append("  (codex-delete-layer-entities \"CODEX_DIRECT_TEST\")\n")
    for (x1, y1), (x2, y2) in lines:
        out.append(f"  (codex-line {x1:.6f} {y1:.6f} {x2:.6f} {y2:.6f})\n")
    for x, y, text, h in texts:
        clean = text.replace("\r", " ").replace("\n", " ").strip()
        if clean:
            out.append(f"  (codex-text {x:.6f} {y:.6f} {h:.6f} {lisp_str(clean)})\n")
    if abs(scale_factor - 1.0) > 1e-9:
        out.append(f"  (codex-scale-layers {scale_factor:.12f})\n")
    out.append("  (setvar \"CLAYER\" oldlayer)\n")
    out.append("  (command \"_.UNDO\" \"_E\")\n")
    out.append("  (command \"_.ZOOM\" \"_E\")\n")
    out.append("  (princ \"\\nCodex auto direct draw finished.\")\n")
    out.append("  (princ)\n")
    out.append(")\n")
    out.append("(c:CODEXAUTODRAWPDF)\n")
    return "".join(out)


def generate_vbs(lsp_path: Path) -> str:
    lsp_unix = str(lsp_path).replace("\\", "/")
    load_cmd = f'(load "{lsp_unix}")'.replace('"', '""')
    clipboard_cmd = f'(load "{lsp_unix}")'.replace("'", "''")
    ps_clipboard = f"powershell -NoProfile -Command Set-Clipboard -Value '{clipboard_cmd}'".replace('"', '""')
    return (
        "On Error Resume Next\n"
        "Set acad = GetObject(, \"AutoCAD.Application\")\n"
        "If Err.Number <> 0 Then\n"
        "  WScript.Echo \"AutoCAD is not reachable: \" & Err.Description\n"
        "  WScript.Quit 1\n"
        "End If\n"
        "On Error GoTo 0\n"
        "Set doc = acad.ActiveDocument\n"
        "Err.Clear\n"
        "On Error Resume Next\n"
        f"doc.SendCommand \"{load_cmd}\" & vbCr\n"
        "If Err.Number <> 0 Then\n"
        "  Err.Clear\n"
        "  Set shell = CreateObject(\"WScript.Shell\")\n"
        f"  shell.Run \"{ps_clipboard}\", 0, True\n"
        "  WScript.Sleep 500\n"
        "  shell.AppActivate acad.Caption\n"
        "  WScript.Sleep 500\n"
        "  shell.SendKeys \"^v\"\n"
        "  WScript.Sleep 200\n"
        "  shell.SendKeys \"{ENTER}\"\n"
        "  WScript.Echo \"Sent CODEXAUTODRAWPDF to AutoCAD via clipboard fallback.\"\n"
        "Else\n"
        "  WScript.Echo \"Sent CODEXAUTODRAWPDF to AutoCAD.\"\n"
        "End If\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto direct draw PDF vectors into AutoCAD, with optional scale-from-dimension.")
    parser.add_argument("--pdf", type=str, required=True, help="PDF path to parse and draw")
    parser.add_argument("--out-dir", type=str, default=str(DEFAULT_OUT_DIR), help="Directory for generated LSP/VBS/debug output")
    parser.add_argument("--scale-from-dimension", action="store_true", help="Auto scale by recognized dimension text")
    parser.add_argument("--ocr", action="store_true", help="Use OCR fallback when PDF has no extractable dimension text")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    lsp_path = out_dir / "codex_auto_direct_draw.lsp"
    run_vbs_path = out_dir / "run_codex_auto_direct_draw.vbs"
    plan_txt_path = out_dir / "codex_auto_direct_plan.txt"
    measurements_txt_path = out_dir / "codex_dimension_measurements.txt"

    lines, texts = parse_pdf(args.pdf)
    scale_factor = 1.0
    scale_reason = "disabled"
    dimensions: List[NumText] = []
    if args.scale_from_dimension:
        scale_factor, scale_reason, dimensions = calc_global_scale(
            lines,
            texts,
            measurements_txt_path,
            args.pdf,
            args.ocr,
            out_dir,
        )
    lsp = generate_lsp(lines, texts, scale_factor)
    lsp_path.write_text(lsp, encoding="utf-8-sig")
    run_vbs_path.write_text(generate_vbs(lsp_path), encoding="utf-16")

    plan_txt_path.write_text(
        "\n".join(
            [
                f"pdf={args.pdf}",
                ("mode=scaled_by_dimension" if args.scale_from_dimension else "mode=proportional_pdf_units"),
                f"scale={scale_factor:.12f}",
                f"scale_reason={scale_reason}",
                f"dimensions={describe_dimensions(dimensions)}",
                f"lines={len(lines)}",
                f"texts={len(texts)}",
                f"measurements={measurements_txt_path}",
                f"lsp={lsp_path}",
                f"runner={run_vbs_path}",
            ]
        ),
        encoding="utf-8",
    )
    print(plan_txt_path)
    print("ready")


if __name__ == "__main__":
    main()
