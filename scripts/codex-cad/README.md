# codex-cad PDF to AutoCAD Workflow

## Purpose

Convert a vector PDF floor plan into native AutoCAD entities without importing the PDF as a reference, block, image, or generated overlay.

## Current Default Flow

1. Parse the first PDF page as vector paths.
2. Apply PDF page rotation before CAD output.
3. Normalize coordinates to a clean CAD origin.
4. Generate AutoLISP that creates `LINE` and `TEXT` entities with `entmake`.
5. Delete previous generated entities on `PDF_DIRECT_WALL`, `PDF_DIRECT_TEXT`, and `CODEX_DIRECT_TEST`.
6. Draw all extracted linework to `PDF_DIRECT_WALL`.
7. If scaling is enabled, OCR dimension numbers and calculate one global scale.
8. Apply the accepted scale only after all CAD entities are created.
9. Run `ZOOM EXTENTS`.

## Dimension Scaling Rule

When `--scale-from-dimension --ocr` is enabled, scale is calculated only from dimension-line geometry:

1. Locate a dimension number.
2. Find the nearby dimension line in the same orientation.
3. For a horizontal dimension, find the nearest vertical extension line on the left and right side of the number.
4. For a vertical dimension, find the nearest horizontal extension line below and above the number.
5. Measure the distance between the two intersections with the dimension line.
6. Calculate `scale = labeled millimeters / intersection distance`.
7. Require at least three valid labels and use the median accepted scale.

The workflow deliberately ignores text bounding boxes, arrow endpoints, slash marks, text strokes, wall endpoints, and repeated label spacing.

## Command

```powershell
py -3 .\scripts\codex-cad\autocad_pdf_direct_pipeline.py --pdf "C:/Users/bobo/Desktop/C区2F-模型.pdf" --out-dir ".\codex-cad-output" --scale-from-dimension --ocr
cscript.exe //nologo .\codex-cad-output\run_codex_auto_direct_draw.vbs
```

## Outputs

- `codex_auto_direct_draw.lsp`: AutoLISP drawer.
- `run_codex_auto_direct_draw.vbs`: runner for the active AutoCAD session.
- `codex_auto_direct_plan.txt`: run summary.
- `codex_dimension_measurements.txt`: dimension candidates, selected measurements, and scale evidence.
- `codex_ocr_page.png`: OCR render when OCR is enabled.

## Dependencies

- AutoCAD running on Windows.
- Python 3.
- `pypdf` or `PyPDF2`.
- Optional for OCR scaling: `pymupdf`, `rapidocr_onnxruntime`.
