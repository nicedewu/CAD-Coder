# codex-cad PDF to AutoCAD Workflow

## Purpose

Convert a vector PDF floor plan into native AutoCAD entities without importing the PDF as a reference, block, image, or generated overlay.

## Current Default Flow

1. Parse the selected PDF page as vector paths and keep the raw stroked line geometry.
2. Apply PDF page rotation before CAD output.
3. Normalize coordinates to a clean CAD origin.
4. When dimension scaling is requested, render the page and isolate small filled vector glyph regions for numeric OCR.
5. Calculate dimensions from the untouched raw geometry when scaling is enabled.
6. Create a separate drawing copy and remove only exact duplicate line segments, including reversed duplicates.
7. Generate AutoLISP that creates `LINE` and `TEXT` entities with `entmake`.
8. Split drawing entities into small AutoLISP batches; the default is 500 entities per batch.
9. Apply the accepted scale directly to the new entity coordinates, so an append run cannot rescale existing entities.
10. Delete previous generated entities on the configured output layers, then draw and acknowledge one batch at a time.
11. Wait for each batch completion marker before submitting the next batch. The runner stops with the exact batch number on timeout or AutoCAD command rejection.
12. Run `ZOOM EXTENTS` after all entities are created.

## Dimension Scaling Rule

When `--scale-from-dimension` is enabled, scale is calculated only from dimension-line geometry in the untouched raw line set. Rendered OCR is preferred because PDF text objects can be font-encoded room labels or unrelated numbers; extractable PDF text is only a fallback when OCR returns no candidates. `--ocr` remains accepted as a compatibility flag. The cleaned drawing copy is never used for calibration.

Scale calculation:

1. Locate a dimension number.
2. Find the nearby dimension line in the same orientation.
3. For a horizontal dimension, find the nearest vertical extension line on the left and right side of the number.
4. For a vertical dimension, find the nearest horizontal extension line below and above the number.
5. Measure the distance between the two intersections with the dimension line.
6. Calculate `scale = labeled millimeters / intersection distance`.
7. Require at least three valid labels on three distinct geometric spans and use the median accepted scale.
8. Stop before AutoCAD drawing if the labels, intersections, or scale agreement do not pass validation. There is no silent `1.0` fallback in this mode.

The workflow deliberately ignores text bounding boxes, arrow endpoints, slash marks, text strokes, wall endpoints, and repeated label spacing.

## Command

```powershell
py -3 .\scripts\codex-cad\autocad_pdf_direct_pipeline.py --pdf "C:/Users/bobo/Desktop/C区2F-模型.pdf" --out-dir ".\codex-cad-output" --chunk-size 500 --chunk-timeout 120 --scale-from-dimension
cscript.exe //nologo .\codex-cad-output\run_codex_auto_direct_draw.vbs
```

The runner targets the AutoCAD drawing that is active when it starts. Do not switch drawings or run a second runner until it reports success or failure.

If a run stops, open the `progress=` or `error=` path written in `codex_auto_direct_plan.txt`. The progress file identifies the latest completed batch; the runner reports the same batch in its error message. Re-run the generated runner to start a clean replacement run on the configured layers.

## Outputs

- `codex_<run_id>_setup.lsp`: creates layers, clears prior generated entities, and registers progress output.
- `codex_<run_id>_batch_*.lsp`: small, independently acknowledged draw batches.
- `codex_<run_id>_finish.lsp`: closes the undo group and zooms extents; generated coordinates are already scaled.
- `run_codex_auto_direct_draw.vbs`: runner for the active AutoCAD session; retries command submission and waits for every completion marker.
- `codex_auto_direct_plan.txt`: run summary.
- `codex-progress-<run_id>/progress.txt`: current stage, latest batch, total batch count, and entity failure count.
- `codex-progress-<run_id>/error.txt`: created when AutoLISP reports failed entity creation.
- `codex_dimension_measurements.txt`: dimension candidates, selected measurements, and scale evidence.
- `codex_ocr_page.png`: rendered page used for OCR.
- `codex_ocr_vector_regions.png`: OCR image containing only small filled vector regions when available.

## Dependencies

- AutoCAD running on Windows.
- Python 3.
- `pypdf` or `PyPDF2`.
- OCR scaling: `rapidocr_onnxruntime` (the local workflow installs it into the ignored `.vendor-ocr` directory; it is not committed to GitHub).
- Optional page renderer: `pymupdf`; the bundled Poppler `pdftoppm` is used as a fallback when available.

Install the OCR dependency into the project-private directory on Windows:

```powershell
py -3 -m pip install --target .vendor-ocr rapidocr_onnxruntime
```

The workflow also searches `.vendor-ocr` automatically, so no system-wide Python package installation is required.
