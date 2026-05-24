# PDF Vector to AutoCAD Workflow

## Goal

Draw the PDF vector plan directly into AutoCAD while preserving the PDF drawing's visible direction and relative proportions.

This workflow does not convert annotation numbers such as 3300, 4000, or 9000 into real-world millimeter scale. The annotation numbers are treated as drawing content only.

## Final Workflow

1. Read the source PDF as vector data.
2. Parse path operations such as `m`, `l`, `c`, `re`, and text operations where available.
3. Apply the PDF page rotation metadata before drawing. For example, if the PDF page has `/Rotate 270`, rotate all extracted geometry so the CAD result matches the PDF's displayed direction.
4. Normalize the extracted coordinates so the drawing starts from a clean CAD origin.
5. Preserve the original PDF vector proportions. Do not scale by dimension annotation text.
6. Before drawing, delete old generated entities on `PDF_DIRECT_WALL`, `PDF_DIRECT_TEXT`, and temporary test layers to avoid overlapping duplicate results.
7. Use AutoLISP `entmake` to create native CAD entities directly in ModelSpace.
8. Put linework on `PDF_DIRECT_WALL`.
9. Put extractable text on `PDF_DIRECT_TEXT`.
10. Run `ZOOM EXTENTS` after drawing.

## Rules

- Do not import PDF or DXF as a block/reference.
- Do not use PDF coordinate units as real millimeters.
- Do not use visible dimension numbers to rescale to actual length unless explicitly requested later.
- Always apply page rotation before drawing.
- Always redraw generated layers cleanly instead of stacking new output on top of previous output.
- Validate the result by checking direction, proportions, and duplicate layers rather than real-world length.

## Current Implementation

The current working scripts are:

- `pdf_direct_draw_to_autocad.py`: parses PDF vectors and applies page rotation.
- `autocad_pdf_direct_pipeline.py`: generates AutoLISP and a VBS runner.
- `codex_auto_direct_draw.lsp`: AutoLISP output that directly creates CAD entities.
- `run_codex_auto_direct_draw.vbs`: sends the AutoLISP command to the running AutoCAD session.

## Typical Command

```powershell
& 'C:\Users\gex\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\autocad_pdf_direct_pipeline.py --pdf 'C:/Users/gex/Desktop/C区2F-模型.pdf'
cscript.exe //nologo .\run_codex_auto_direct_draw.vbs
```

If future PDFs contain extractable text and the user explicitly asks for real millimeter scaling, dimension-based scaling can be added as a separate optional step after the proportional CAD drawing is correct.
