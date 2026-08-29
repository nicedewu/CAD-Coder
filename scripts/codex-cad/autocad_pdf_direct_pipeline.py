from __future__ import annotations

from pathlib import Path
import argparse
import os
import re
import shutil
import subprocess
import sys
from uuid import uuid4
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
            candidates.append(parent / ".vendor-ocr")
            candidates.append(parent / ".vendor")
            candidates.append(parent / "third_party")
    for path in reversed(candidates):
        if path.exists():
            value = str(path)
            if value not in sys.path:
                sys.path.insert(0, value)


add_dependency_paths()

from pdf_direct_draw_to_autocad import parse_pdf, rotate_point_for_page


DEFAULT_OUT_DIR = Path.cwd()
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_TIMEOUT_SECONDS = 120
LINE_DEDUP_PRECISION = 6


LineSeg = Tuple[Tuple[float, float], Tuple[float, float]]
TextEnt = Tuple[float, float, str, float]
NumText = Tuple[float, float, float, str]
SpanKey = Tuple[str, float, float, float]
MeasureCandidate = Tuple[float, float, str, SpanKey]
ScalePair = Tuple[float, float, float, str, int, SpanKey]
FilledPathBBox = Tuple[float, float, float, float, tuple[float, ...] | None]


class DimensionCalibrationError(RuntimeError):
    """Raised when requested dimension-based calibration is not trustworthy."""


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


def filter_autocad_texts(texts: List[TextEnt]) -> List[TextEnt]:
    """Keep only text that can be safely embedded in an AutoLISP string."""
    safe: List[TextEnt] = []
    for x, y, text, height in texts:
        clean = text.replace("\r", " ").replace("\n", " ").strip()
        if clean and all(32 <= ord(char) <= 126 for char in clean):
            safe.append((x, y, clean, height))
    return safe


def normalize_ocr_number(text: str) -> str | None:
    """Keep only a single plausible millimetre dimension token."""
    clean = str(text).strip().replace(",", "")
    if clean.count(".") > 1:
        return None
    if not re.fullmatch(r"\d{2,6}(?:\.\d+)?", clean):
        return None
    return clean


def bbox_from_lines(lines: List[LineSeg]) -> tuple[float, float]:
    pts = [p for line in lines for p in line]
    if not pts:
        return 0.0, 0.0
    maxx = max(x for x, _ in pts)
    maxy = max(y for _, y in pts)
    return maxx, maxy


def find_pdftoppm() -> Path | None:
    configured = os.environ.get("CODEX_CAD_PDFTOPPM", "").strip()
    if configured:
        candidate = Path(configured)
        if candidate.exists():
            return candidate
    on_path = shutil.which("pdftoppm")
    if on_path:
        return Path(on_path)
    bundled = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
    return bundled if bundled.exists() else None


def render_pdf_page(
    pdf_path: str | Path,
    out_dir: Path,
    page_number: int,
    zoom: float,
    metadata: dict,
) -> tuple[Path, int, int, str]:
    """Render one page with PyMuPDF when available, otherwise bundled Poppler."""
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
        if doc.page_count == 0:
            raise DimensionCalibrationError("PDF has no pages")
        if page_number < 1 or page_number > doc.page_count:
            raise DimensionCalibrationError(f"page {page_number} is outside PDF page count {doc.page_count}")
        page = doc[page_number - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image_path = out_dir / "codex_ocr_page.png"
        pix.save(str(image_path))
        return image_path, int(pix.width), int(pix.height), "pymupdf"
    except ImportError:
        pass

    executable = find_pdftoppm()
    if executable is None:
        raise DimensionCalibrationError("no PDF renderer found; install PyMuPDF or configure CODEX_CAD_PDFTOPPM")
    output_base = out_dir / "codex_ocr_page"
    dpi = max(144, round(72.0 * zoom))
    try:
        subprocess.run(
            [
                str(executable),
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-r",
                str(dpi),
                "-png",
                "-singlefile",
                str(pdf_path),
                str(output_base),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DimensionCalibrationError(f"PDF render failed: {exc.__class__.__name__}") from exc
    image_path = output_base.with_suffix(".png")
    if not image_path.exists():
        raise DimensionCalibrationError("PDF renderer returned no PNG output")
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            width, height = image.size
    except Exception as exc:
        raise DimensionCalibrationError(f"cannot inspect rendered page: {exc.__class__.__name__}") from exc
    return image_path, int(width), int(height), f"pdftoppm@{dpi}dpi"


def is_green_fill(color: tuple[float, ...] | None) -> bool:
    return bool(color and len(color) >= 3 and color[1] >= 0.70 and color[0] <= 0.30 and color[2] <= 0.30)


def numeric_fill_regions(metadata: dict) -> List[FilledPathBBox]:
    """Return small fill paths likely to be outlined annotation glyphs."""
    raw = metadata.get("filled_path_bboxes", [])
    if not raw:
        return []
    extent = max(float(metadata.get("width", 0.0)), float(metadata.get("height", 0.0)), 1.0)
    green: List[FilledPathBBox] = []
    small: List[FilledPathBBox] = []
    seen: set[tuple[float, float, float, float, str]] = set()
    for item in raw:
        if len(item) != 5:
            continue
        minx, miny, maxx, maxy, color = item
        width = abs(float(maxx) - float(minx))
        height = abs(float(maxy) - float(miny))
        if width <= 0.0 or height <= 0.0:
            continue
        if width > extent * 0.025 or height > extent * 0.025:
            continue
        if width < extent * 0.00005 or height < extent * 0.00005:
            continue
        normalized_color = tuple(float(value) for value in color) if color else None
        key = (round(float(minx), 5), round(float(miny), 5), round(float(maxx), 5), round(float(maxy), 5), str(normalized_color))
        if key in seen:
            continue
        seen.add(key)
        region = (float(minx), float(miny), float(maxx), float(maxy), normalized_color)
        small.append(region)
        if is_green_fill(normalized_color):
            green.append(region)
    return green or small


def prepare_ocr_image(
    source_path: Path,
    image_width: int,
    image_height: int,
    metadata: dict,
    out_dir: Path,
) -> tuple[Path, str, int]:
    """Mask the page to small vector fill regions so walls do not compete with OCR."""
    regions = numeric_fill_regions(metadata)
    if not regions:
        return source_path, "full_page", 0
    if int(metadata.get("rotation", 0)) % 360 != 0:
        return source_path, "full_page_rotated", len(regions)
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return source_path, "full_page_no_pillow", len(regions)

    page_width = float(metadata["width"])
    page_height = float(metadata["height"])
    minx = float(metadata["minx"])
    miny = float(metadata["miny"])
    scale_x = image_width / max(page_width, 1e-9)
    scale_y = image_height / max(page_height, 1e-9)
    padding = max(3, round(min(scale_x, scale_y) * 2.0))
    with Image.open(source_path) as source:
        source_rgb = source.convert("RGB")
        mask = Image.new("L", source_rgb.size, 0)
        draw = ImageDraw.Draw(mask)
        for region_minx, region_miny, region_maxx, region_maxy, _color in regions:
            left = round((region_minx + minx) * scale_x) - padding
            right = round((region_maxx + minx) * scale_x) + padding
            top = round(image_height - (region_maxy + miny) * scale_y) - padding
            bottom = round(image_height - (region_miny + miny) * scale_y) + padding
            left = max(0, min(image_width, left))
            right = max(0, min(image_width, right))
            top = max(0, min(image_height, top))
            bottom = max(0, min(image_height, bottom))
            if right > left and bottom > top:
                draw.rectangle((left, top, right, bottom), fill=255)
        white = Image.new("RGB", source_rgb.size, "white")
        masked = Image.composite(source_rgb, white, mask)
        image_path = out_dir / "codex_ocr_vector_regions.png"
        masked.save(image_path)
    return image_path, "vector_fill_regions", len(regions)


def ocr_numeric_texts(
    pdf_path: str | Path,
    lines: List[LineSeg],
    out_dir: Path,
    page_number: int = 1,
    zoom: float = 3.0,
) -> tuple[List[NumText], str]:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:
        return [], f"ocr_import_failed:{exc.__class__.__name__}"

    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    _meta_lines, _meta_texts, metadata = parse_pdf(pdf_path, return_metadata=True, page_number=page_number)
    try:
        img_path, image_width, image_height, render_source = render_pdf_page(
            pdf_path,
            out_dir,
            page_number,
            zoom,
            metadata,
        )
    except DimensionCalibrationError as exc:
        return [], f"ocr_render_failed:{exc}"
    ocr_path, region_source, region_count = prepare_ocr_image(
        img_path,
        image_width,
        image_height,
        metadata,
        out_dir,
    )

    engine = RapidOCR()
    result, _ = engine(str(ocr_path))
    if not result:
        return [], f"ocr_no_result,render={render_source},regions={region_source}:{region_count}"

    minx = float(metadata["minx"])
    miny = float(metadata["miny"])
    page_width = float(metadata["width"])
    page_height = float(metadata["height"])
    rotation = int(metadata.get("rotation", 0)) % 360
    rotated_width = page_height if rotation in (90, 270) else page_width
    rotated_height = page_width if rotation in (90, 270) else page_height
    rendered_rotated = abs((image_width / max(image_height, 1)) - (rotated_width / max(rotated_height, 1))) < 0.01
    coordinate_width = rotated_width if rendered_rotated else page_width
    coordinate_height = rotated_height if rendered_rotated else page_height
    nums: List[NumText] = []
    for box, text, score in result:
        try:
            score_value = float(score)
        except Exception:
            score_value = 0.0
        if score_value < 0.45:
            continue
        clean = normalize_ocr_number(str(text))
        if clean is None:
            continue
        value = float(clean)
        # Ignore area labels like 21.527 when dimensions are expected in mm.
        if value < 100:
            continue
        xs = [float(pt[0]) for pt in box]
        ys = [float(pt[1]) for pt in box]
        # OCR box direction describes glyph layout, not necessarily the
        # dimension-line direction; geometry matching must inspect both axes.
        orientation = "any"
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        page_x = (cx / max(image_width, 1)) * coordinate_width
        page_y = coordinate_height - (cy / max(image_height, 1)) * coordinate_height
        if rotation and not rendered_rotated:
            page_x, page_y = rotate_point_for_page(page_x, page_y, rotation, page_width, page_height)
        x = page_x - minx
        y = page_y - miny
        nums.append((x, y, value, orientation))
    unique_nums: List[NumText] = []
    seen_nums: set[tuple[float, float, float, str]] = set()
    for x, y, value, orientation in nums:
        key = (round(x, 3), round(y, 3), value, orientation)
        if key not in seen_nums:
            seen_nums.add(key)
            unique_nums.append((x, y, value, orientation))
    return unique_nums, f"ocr_candidates={len(unique_nums)},render={render_source},regions={region_source}:{region_count}"


def measure_candidates_from_text(lines: List[LineSeg], x: float, y: float, orientation: str = "any") -> List[MeasureCandidate]:
    candidates: List[MeasureCandidate] = []
    eps = 1e-6
    cad_w, cad_h = bbox_from_lines(lines)
    extent = max(cad_w, cad_h, 1.0)
    near = max(extent * 0.025, 12.0)
    line_margin = max(extent * 0.006, 2.0)
    intersect_tol = max(extent * 0.0015, 0.5)
    min_dim_line_len = extent * 0.020
    min_ext_line_len = max(extent * 0.006, 4.0)

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
                span_key: SpanKey = ("horizontal", round(y_line, 6), left, right)
                candidates.append((dist, right - left, "dimension_intersection_horizontal_adjacent", span_key))

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
                span_key = ("vertical", round(x_line, 6), lower, upper)
                candidates.append((dist, upper - lower, "dimension_intersection_vertical_adjacent", span_key))
    return [c for c in candidates if c[1] > eps]


def collect_dimension_intersection_pairs(lines: List[LineSeg], nums: List[NumText]) -> List[ScalePair]:
    pairs: List[ScalePair] = []
    seen: set[tuple[int, SpanKey]] = set()
    for idx, (x, y, value, orientation) in enumerate(nums):
        for _dist, measured, kind, span_key in measure_candidates_from_text(lines, x, y, orientation):
            if measured <= 0:
                continue
            if kind.startswith("dimension_intersection_"):
                key = (idx, span_key)
                if key in seen:
                    continue
                seen.add(key)
                scale = value / measured
                pairs.append((scale, value, measured, kind, idx, span_key))
    return pairs


def pick_median_scale(pairs: List[ScalePair], tolerance: float = 0.03) -> tuple[float, str, List[ScalePair]] | None:
    if len(pairs) < 3:
        return None

    best_group: List[ScalePair] = []
    for scale, *_rest in pairs:
        group = [p for p in pairs if abs(p[0] - scale) / max(scale, 1e-9) <= tolerance]
        by_label: dict[int, ScalePair] = {}
        used_spans: set[SpanKey] = set()
        for item in sorted(group, key=lambda p: abs(p[0] - scale)):
            if item[4] in by_label or item[5] in used_spans:
                continue
            by_label[item[4]] = item
            used_spans.add(item[5])
        distinct_group = list(by_label.values())
        if len(distinct_group) > len(best_group):
            best_group = distinct_group

    if len(best_group) < 3:
        return None
    best_group.sort(key=lambda p: p[0])
    scale = best_group[len(best_group) // 2][0]
    used = ";".join(f"{value:g}/{measured:.3f}/{kind}" for _, value, measured, kind, _idx, _span_key in best_group)
    return scale, f"dimension_intersection_median,count={len(best_group)},used={used}", best_group


def write_measurement_debug(
    path: Path,
    nums: List[NumText],
    pairs: List[ScalePair],
    selected: List[ScalePair],
) -> None:
    selected_keys = {(idx, round(measured, 6), kind, span_key) for _scale, _value, measured, kind, idx, span_key in selected}
    rows = [
        "rule=dimension_line_intersections_only",
        "minimum_valid_labels=3",
        "dimensions=" + describe_dimensions(nums),
        "measurements:",
    ]
    for item in sorted(pairs, key=lambda p: (p[4], p[0])):
        scale, value, measured, kind, idx, span_key = item
        x, y, _value, orientation = nums[idx]
        marker = "selected" if (idx, round(measured, 6), kind, span_key) in selected_keys else "candidate"
        rows.append(
            f"{marker}\tidx={idx}\tvalue={value:g}\torientation={orientation}\t"
            f"pos=({x:.3f},{y:.3f})\tmeasured={measured:.6f}\tscale={scale:.12f}\tkind={kind}\tspan={span_key}"
        )
    path.write_text("\n".join(rows), encoding="utf-8")


def calc_global_scale(
    lines: List[LineSeg],
    texts: List[TextEnt],
    measurements_path: Path,
    pdf_path: str | Path | None = None,
    use_ocr: bool = False,
    out_dir: Path | None = None,
    page_number: int = 1,
    require_valid_scale: bool = False,
) -> tuple[float, str, List[NumText]]:
    if use_ocr and pdf_path is not None:
        # Prefer rendered OCR in dimension mode because PDF text objects may be
        # font-encoded room labels or unrelated numbers rather than dimensions.
        nums, ocr_reason = ocr_numeric_texts(pdf_path, lines, out_dir or measurements_path.parent, page_number)
        source = "ocr"
        if not nums:
            nums = extract_numeric_texts(texts)
            source = "pdf_text_fallback"
    else:
        nums = extract_numeric_texts(texts)
        source = "pdf_text"
        ocr_reason = ""
    if not nums:
        write_measurement_debug(measurements_path, nums, [], [])
        if require_valid_scale:
            raise DimensionCalibrationError(
                f"no numeric dimension labels detected ({ocr_reason or 'no_numeric_text_found'}); "
                f"see {measurements_path}"
            )
        return 1.0, ocr_reason or "no_numeric_text_found", nums

    pairs = collect_dimension_intersection_pairs(lines, nums)
    picked = pick_median_scale(pairs)
    if picked is not None:
        scale, reason, selected = picked
        write_measurement_debug(measurements_path, nums, pairs, selected)
        return scale, f"source={source},{reason}", nums
    write_measurement_debug(measurements_path, nums, pairs, [])
    if require_valid_scale:
        raise DimensionCalibrationError(
            f"only {len(pairs)} dimension-line intersection candidates found; "
            f"at least 3 consistent dimensions are required; see {measurements_path}"
        )
    return 1.0, f"source={source},need_at_least_3_dimension_intersections", nums


def describe_dimensions(nums: List[NumText]) -> str:
    if not nums:
        return "none"
    return ";".join(f"{value:g}@({x:.3f},{y:.3f})/{orientation}" for x, y, value, orientation in nums)


def deduplicate_lines(lines: List[LineSeg]) -> List[LineSeg]:
    """Remove only exact duplicate segments while preserving source order."""
    seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    unique: List[LineSeg] = []
    for start, end in lines:
        start_key = (round(start[0], LINE_DEDUP_PRECISION), round(start[1], LINE_DEDUP_PRECISION))
        end_key = (round(end[0], LINE_DEDUP_PRECISION), round(end[1], LINE_DEDUP_PRECISION))
        key = tuple(sorted((start_key, end_key)))
        if key in seen:
            continue
        seen.add(key)
        unique.append((start, end))
    return unique


def scale_lines(lines: List[LineSeg], factor: float) -> List[LineSeg]:
    if factor <= 0.0:
        raise ValueError("scale factor must be greater than zero")
    return [((x1 * factor, y1 * factor), (x2 * factor, y2 * factor)) for (x1, y1), (x2, y2) in lines]


def scale_texts(texts: List[TextEnt], factor: float) -> List[TextEnt]:
    if factor <= 0.0:
        raise ValueError("scale factor must be greater than zero")
    return [(x * factor, y * factor, text, height * factor) for x, y, text, height in texts]


def generate_lsp(
    lines: List[LineSeg],
    texts: List[TextEnt],
    scale_factor: float,
    line_layer: str = "PDF_DIRECT_WALL",
    text_layer: str = "PDF_DIRECT_TEXT",
    delete_existing: bool = True,
) -> str:
    lines = scale_lines(lines, scale_factor)
    texts = scale_texts(texts, scale_factor)
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
    out.append(f"  (entmake (list (cons 0 \"LINE\") (cons 8 {lisp_str(line_layer)}) (cons 10 (list x1 y1 0.0)) (cons 11 (list x2 y2 0.0))))\n")
    out.append(")\n")
    out.append("(defun codex-text (x y h s)\n")
    out.append(f"  (entmake (list (cons 0 \"TEXT\") (cons 8 {lisp_str(text_layer)}) (cons 10 (list x y 0.0)) (cons 40 h) (cons 1 s) (cons 50 0.0)))\n")
    out.append(")\n")
    out.append("(defun c:CODEXAUTODRAWPDF-SETUP (/ oldlayer)\n")
    out.append("  (setq oldlayer (getvar \"CLAYER\"))\n")
    out.append(f"  (codex-make-layer {lisp_str(line_layer)} 7)\n")
    out.append(f"  (codex-make-layer {lisp_str(text_layer)} 2)\n")
    out.append("  (command \"_.UNDO\" \"_BE\")\n")
    if delete_existing:
        out.append(f"  (codex-delete-layer-entities {lisp_str(line_layer)})\n")
        out.append(f"  (codex-delete-layer-entities {lisp_str(text_layer)})\n")
    out.append("  oldlayer\n")
    out.append(")\n")
    out.append("(defun c:CODEXAUTODRAWPDF-FINISH (oldlayer)\n")
    out.append("  (setvar \"CLAYER\" oldlayer)\n")
    out.append("  (command \"_.UNDO\" \"_E\")\n")
    out.append("  (command \"_.ZOOM\" \"_E\")\n")
    out.append("  (princ \"\\nCodex auto direct draw finished.\")\n")
    out.append("  (princ)\n")
    out.append(")\n")
    out.append("(setq codex-oldlayer (c:CODEXAUTODRAWPDF-SETUP))\n")
    for (x1, y1), (x2, y2) in lines:
        out.append(f"(codex-line {x1:.6f} {y1:.6f} {x2:.6f} {y2:.6f})\n")
    for x, y, text, h in texts:
        clean = text.replace("\r", " ").replace("\n", " ").strip()
        if clean:
            out.append(f"(codex-text {x:.6f} {y:.6f} {h:.6f} {lisp_str(clean)})\n")
    out.append("(c:CODEXAUTODRAWPDF-FINISH codex-oldlayer)\n")
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


def autocad_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def vbs_str(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def split_batches(items: List[tuple], chunk_size: int) -> List[List[tuple]]:
    return [items[index:index + chunk_size] for index in range(0, len(items), chunk_size)]


def generate_chunked_setup_lsp(
    *,
    line_layer: str,
    text_layer: str,
    delete_existing: bool,
    total_batches: int,
    progress_path: Path,
    error_path: Path,
    setup_marker_path: Path,
    done_marker_path: Path,
) -> str:
    progress_lisp_path = lisp_str(autocad_path(progress_path))
    error_lisp_path = lisp_str(autocad_path(error_path))
    setup_marker_lisp_path = lisp_str(autocad_path(setup_marker_path))
    done_marker_lisp_path = lisp_str(autocad_path(done_marker_path))
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
    out.append("(defun codex-write-marker (path message / fh)\n")
    out.append("  (setq fh (open path \"w\"))\n")
    out.append("  (if fh (progn (write-line message fh) (close fh)))\n")
    out.append(")\n")
    out.append("(defun codex-write-progress (stage batch total / fh)\n")
    out.append(f"  (setq fh (open {progress_lisp_path} \"w\"))\n")
    out.append("  (if fh\n")
    out.append("    (progn\n")
    out.append("      (write-line (strcat \"stage=\" stage) fh)\n")
    out.append("      (write-line (strcat \"batch=\" (itoa batch)) fh)\n")
    out.append("      (write-line (strcat \"total_batches=\" (itoa total)) fh)\n")
    out.append("      (write-line (strcat \"entity_failures=\" (itoa codex-entity-failures)) fh)\n")
    out.append("      (close fh)\n")
    out.append("    )\n")
    out.append("  )\n")
    out.append(")\n")
    out.append("(defun codex-line (x1 y1 x2 y2)\n")
    out.append(f"  (if (not (entmake (list (cons 0 \"LINE\") (cons 8 {lisp_str(line_layer)}) (cons 10 (list x1 y1 0.0)) (cons 11 (list x2 y2 0.0)))))\n")
    out.append("    (setq codex-entity-failures (1+ codex-entity-failures))\n")
    out.append("  )\n")
    out.append(")\n")
    out.append("(defun codex-text (x y h s)\n")
    out.append(f"  (if (not (entmake (list (cons 0 \"TEXT\") (cons 8 {lisp_str(text_layer)}) (cons 10 (list x y 0.0)) (cons 40 h) (cons 1 s) (cons 50 0.0))))\n")
    out.append("    (setq codex-entity-failures (1+ codex-entity-failures))\n")
    out.append("  )\n")
    out.append(")\n")
    out.append("(defun c:CODEXAUTODRAWPDF-SETUP (/ oldlayer)\n")
    out.append("  (setq codex-entity-failures 0)\n")
    out.append("  (setq oldlayer (getvar \"CLAYER\"))\n")
    out.append(f"  (codex-make-layer {lisp_str(line_layer)} 7)\n")
    out.append(f"  (codex-make-layer {lisp_str(text_layer)} 2)\n")
    out.append("  (command \"_.UNDO\" \"_BE\")\n")
    if delete_existing:
        out.append(f"  (codex-delete-layer-entities {lisp_str(line_layer)})\n")
        out.append(f"  (codex-delete-layer-entities {lisp_str(text_layer)})\n")
    out.append(f"  (codex-write-progress \"setup\" 0 {total_batches})\n")
    out.append(f"  (codex-write-marker {setup_marker_lisp_path} \"ok\")\n")
    out.append("  oldlayer\n")
    out.append(")\n")
    out.append("(defun c:CODEXAUTODRAWPDF-FINISH (oldlayer)\n")
    out.append("  (setvar \"CLAYER\" oldlayer)\n")
    out.append("  (command \"_.UNDO\" \"_E\")\n")
    out.append("  (command \"_.ZOOM\" \"_E\")\n")
    out.append("  (if (> codex-entity-failures 0)\n")
    out.append(f"    (codex-write-marker {error_lisp_path} (strcat \"entity_failures=\" (itoa codex-entity-failures)))\n")
    out.append("  )\n")
    out.append(f"  (codex-write-progress \"done\" {total_batches} {total_batches})\n")
    out.append(f"  (codex-write-marker {done_marker_lisp_path} \"ok\")\n")
    out.append("  (princ (strcat \"\\nCodex auto direct draw finished. failures=\" (itoa codex-entity-failures)))\n")
    out.append("  (princ)\n")
    out.append(")\n")
    out.append("(setq codex-oldlayer (c:CODEXAUTODRAWPDF-SETUP))\n")
    return "".join(out)


def generate_chunk_lsp(
    kind: str,
    batch: List[tuple],
    batch_index: int,
    total_batches: int,
    marker_path: Path,
) -> str:
    out = [f"(codex-write-progress \"drawing\" {batch_index} {total_batches})\n"]
    if kind == "lines":
        for (x1, y1), (x2, y2) in batch:
            out.append(f"(codex-line {x1:.6f} {y1:.6f} {x2:.6f} {y2:.6f})\n")
    else:
        for x, y, text, h in batch:
            clean = text.replace("\r", " ").replace("\n", " ").strip()
            if clean:
                out.append(f"(codex-text {x:.6f} {y:.6f} {h:.6f} {lisp_str(clean)})\n")
    out.append(f"(codex-write-progress \"drawing\" {batch_index} {total_batches})\n")
    out.append(f"(codex-write-marker {lisp_str(autocad_path(marker_path))} \"ok\")\n")
    out.append(f"(princ \"\\nCodex batch {batch_index}/{total_batches} finished.\")\n")
    return "".join(out)


def generate_finish_lsp() -> str:
    return "(c:CODEXAUTODRAWPDF-FINISH codex-oldlayer)\n"


def generate_chunked_vbs(
    setup_path: Path,
    chunks: List[tuple[Path, Path]],
    finish_path: Path,
    setup_marker_path: Path,
    done_marker_path: Path,
    error_path: Path,
    chunk_timeout_seconds: int,
) -> str:
    def load_command(path: Path) -> str:
        return f'(load "{autocad_path(path)}")'

    stages = [("setup", setup_path, setup_marker_path)]
    stages.extend((f"batch {index}/{len(chunks)}", path, marker) for index, (path, marker) in enumerate(chunks, start=1))
    stages.append(("finish", finish_path, done_marker_path))
    error_marker = vbs_str(str(error_path))
    out = [
        "Option Explicit\n",
        "Dim acad, doc, fso\n",
        "Set fso = CreateObject(\"Scripting.FileSystemObject\")\n",
        "On Error Resume Next\n",
        "Set acad = GetObject(, \"AutoCAD.Application\")\n",
        "If Err.Number <> 0 Then\n",
        "  WScript.Echo \"ERROR: AutoCAD is not reachable: \" & Err.Description\n",
        "  WScript.Quit 1\n",
        "End If\n",
        "Set doc = acad.ActiveDocument\n",
        "If Err.Number <> 0 Then\n",
        "  WScript.Echo \"ERROR: no active AutoCAD document: \" & Err.Description\n",
        "  WScript.Quit 1\n",
        "End If\n",
        "On Error GoTo 0\n",
        "acad.Visible = True\n",
        "doc.Activate\n",
        "WScript.Echo \"Target drawing: \" & doc.Name\n",
        "\n",
        "Function ElapsedSeconds(started)\n",
        "  ElapsedSeconds = Timer - started\n",
        "  If ElapsedSeconds < 0 Then ElapsedSeconds = ElapsedSeconds + 86400\n",
        "End Function\n",
        "\n",
        "Function ReadMarker(path)\n",
        "  Dim fh\n",
        "  ReadMarker = \"\"\n",
        "  If fso.FileExists(path) Then\n",
        "    Set fh = fso.OpenTextFile(path, 1, False)\n",
        "    ReadMarker = fh.ReadAll\n",
        "    fh.Close\n",
        "  End If\n",
        "End Function\n",
        "\n",
        "Function SendWithRetry(commandText)\n",
        "  Dim attempt, number, description\n",
        "  SendWithRetry = False\n",
        "  For attempt = 1 To 20\n",
        "    On Error Resume Next\n",
        "    Err.Clear\n",
        "    doc.Activate\n",
        "    doc.SendCommand commandText & vbCr\n",
        "    number = Err.Number\n",
        "    description = Err.Description\n",
        "    On Error GoTo 0\n",
        "    If number = 0 Then\n",
        "      SendWithRetry = True\n",
        "      Exit Function\n",
        "    End If\n",
        "    WScript.Sleep 500\n",
        "  Next\n",
        "  WScript.Echo \"ERROR: AutoCAD rejected command after 20 attempts: \" & description\n",
        "End Function\n",
        "\n",
        "Sub Fail(stage, detail)\n",
        "  WScript.Echo \"ERROR: \" & stage & \" - \" & detail\n",
        "  WScript.Quit 2\n",
        "End Sub\n",
        "\n",
        "Sub RunStage(stage, commandText, markerPath)\n",
        "  Dim started\n",
        "  WScript.Echo \"Starting \" & stage\n",
        "  If Not SendWithRetry(commandText) Then Fail stage, \"command submission failed\"\n",
        "  started = Timer\n",
        "  Do\n",
        f"    If fso.FileExists({error_marker}) Then Fail stage, ReadMarker({error_marker})\n",
        "    If fso.FileExists(markerPath) Then\n",
        "      WScript.Echo \"Completed \" & stage\n",
        "      Exit Sub\n",
        "    End If\n",
        f"    If ElapsedSeconds(started) >= {chunk_timeout_seconds} Then Fail stage, \"timed out waiting for its completion marker\"\n",
        "    WScript.Sleep 250\n",
        "  Loop\n",
        "End Sub\n",
        "\n",
    ]
    for stage, lsp_path, marker_path in stages:
        out.append(f"RunStage {vbs_str(stage)}, {vbs_str(load_command(lsp_path))}, {vbs_str(str(marker_path))}\n")
    out.append("WScript.Echo \"Codex PDF draw finished successfully.\"\n")
    return "".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto direct draw PDF vectors into AutoCAD with validated dimension scaling.")
    parser.add_argument("--pdf", type=str, required=True, help="PDF path to parse and draw")
    parser.add_argument("--page", type=int, default=1, help="1-based PDF page number to parse")
    parser.add_argument("--out-dir", type=str, default=str(DEFAULT_OUT_DIR), help="Directory for generated LSP/VBS/debug output")
    parser.add_argument("--line-layer", type=str, default="PDF_DIRECT_WALL", help="AutoCAD layer for generated line entities")
    parser.add_argument("--text-layer", type=str, default="PDF_DIRECT_TEXT", help="AutoCAD layer for generated text entities")
    parser.add_argument("--no-delete-existing", action="store_true", help="Append output without deleting existing entities on output layers")
    parser.add_argument("--skip-text", action="store_true", help="Skip generated text entities and draw vector linework only")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="Entities per AutoCAD drawing batch")
    parser.add_argument("--chunk-timeout", type=int, default=DEFAULT_CHUNK_TIMEOUT_SECONDS, help="Seconds to wait for each AutoCAD batch")
    parser.add_argument("--scale-from-dimension", action="store_true", help="Require actual dimension-based scaling; fail unless 3 consistent dimensions are found")
    parser.add_argument("--ocr", action="store_true", help="Compatibility flag; strict dimension scaling always prefers OCR")
    args = parser.parse_args()

    if args.chunk_size <= 0:
        parser.error("--chunk-size must be greater than zero")
    if args.chunk_timeout <= 0:
        parser.error("--chunk-timeout must be greater than zero")
    scale_requested = args.scale_from_dimension

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    run_vbs_path = out_dir / "run_codex_auto_direct_draw.vbs"
    plan_txt_path = out_dir / "codex_auto_direct_plan.txt"
    measurements_txt_path = out_dir / "codex_dimension_measurements.txt"

    raw_lines, texts = parse_pdf(args.pdf, page_number=args.page)
    # Keep the untouched geometry for dimension detection; clean only the drawing copy.
    measurement_lines = raw_lines
    drawing_lines = deduplicate_lines(raw_lines)
    scale_factor = 1.0
    scale_reason = "disabled"
    dimensions: List[NumText] = []
    if scale_requested:
        try:
            scale_factor, scale_reason, dimensions = calc_global_scale(
                measurement_lines,
                texts,
                measurements_txt_path,
                args.pdf,
                True,
                out_dir,
                args.page,
                require_valid_scale=True,
            )
        except DimensionCalibrationError as exc:
            raise SystemExit(f"ERROR: dimension calibration failed: {exc}") from exc
    output_texts = [] if args.skip_text else filter_autocad_texts(texts)
    drawing_lines = scale_lines(drawing_lines, scale_factor)
    output_texts = scale_texts(output_texts, scale_factor)
    run_id = uuid4().hex[:12]
    progress_dir = out_dir / f"codex-progress-{run_id}"
    progress_dir.mkdir(parents=True, exist_ok=False)
    progress_path = progress_dir / "progress.txt"
    error_path = progress_dir / "error.txt"
    setup_marker_path = progress_dir / "setup.ok"
    done_marker_path = progress_dir / "done.ok"

    batches = [("lines", batch) for batch in split_batches(drawing_lines, args.chunk_size)]
    batches.extend(("texts", batch) for batch in split_batches(output_texts, args.chunk_size))
    total_batches = len(batches)
    setup_path = out_dir / f"codex_{run_id}_setup.lsp"
    finish_path = out_dir / f"codex_{run_id}_finish.lsp"
    setup_path.write_text(
        generate_chunked_setup_lsp(
            line_layer=args.line_layer,
            text_layer=args.text_layer,
            delete_existing=not args.no_delete_existing,
            total_batches=total_batches,
            progress_path=progress_path,
            error_path=error_path,
            setup_marker_path=setup_marker_path,
            done_marker_path=done_marker_path,
        ),
        encoding="utf-8-sig",
    )
    chunk_files: List[tuple[Path, Path]] = []
    for batch_index, (kind, batch) in enumerate(batches, start=1):
        marker_path = progress_dir / f"batch-{batch_index:04d}.ok"
        chunk_path = out_dir / f"codex_{run_id}_batch_{batch_index:04d}.lsp"
        chunk_path.write_text(
            generate_chunk_lsp(kind, batch, batch_index, total_batches, marker_path),
            encoding="utf-8-sig",
        )
        chunk_files.append((chunk_path, marker_path))
    finish_path.write_text(generate_finish_lsp(), encoding="utf-8-sig")
    run_vbs_path.write_text(
        generate_chunked_vbs(
            setup_path,
            chunk_files,
            finish_path,
            setup_marker_path,
            done_marker_path,
            error_path,
            args.chunk_timeout,
        ),
        encoding="utf-16",
    )

    plan_txt_path.write_text(
        "\n".join(
            [
                f"pdf={args.pdf}",
                f"page={args.page}",
                ("mode=scaled_by_dimension_strict" if scale_requested else "mode=proportional_pdf_units"),
                f"scale={scale_factor:.12f}",
                f"scale_reason={scale_reason}",
                f"dimension_calibration={'required' if scale_requested else 'disabled'}",
                "geometry_scale_application=coordinates_before_cad",
                f"dimensions={describe_dimensions(dimensions)}",
                f"lines={len(raw_lines)}",
                f"raw_lines={len(raw_lines)}",
                f"drawing_lines={len(drawing_lines)}",
                f"deduplicated_lines={len(raw_lines) - len(drawing_lines)}",
                f"dedup_precision={LINE_DEDUP_PRECISION}",
                f"texts={len(texts)}",
                f"drawn_texts={len(output_texts)}",
                f"line_layer={args.line_layer}",
                f"text_layer={args.text_layer}",
                f"delete_existing={not args.no_delete_existing}",
                f"run_id={run_id}",
                "execution=chunked_autolisp",
                f"chunk_size={args.chunk_size}",
                f"chunk_timeout_seconds={args.chunk_timeout}",
                f"total_batches={total_batches}",
                f"progress={progress_path}",
                f"error={error_path}",
                f"measurements={measurements_txt_path}",
                f"setup_lsp={setup_path}",
                f"finish_lsp={finish_path}",
                f"runner={run_vbs_path}",
            ]
        ),
        encoding="utf-8",
    )
    print(plan_txt_path)
    print("ready")


if __name__ == "__main__":
    main()
