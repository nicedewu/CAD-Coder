# PDF Vector to AutoCAD Workflow / PDF 矢量转 AutoCAD 工作流

## Goal / 目标

EN: Draw the PDF vector plan directly into AutoCAD, then optionally calibrate the whole drawing scale from visible dimension labels.
ZH: 将 PDF 矢量平面图直接绘制到 AutoCAD，然后可选根据图上可见尺寸标注校准整图比例。

EN: The default safe mode preserves PDF proportions only. When `--scale-from-dimension --ocr` is enabled, dimension labels such as 3300 or 4000 are used as millimeter references.
ZH: 默认安全模式只保持 PDF 比例。启用 `--scale-from-dimension --ocr` 时，3300、4000 等尺寸标注会作为毫米基准。

## Final Workflow / 最终流程

1. EN: Read the source PDF as vector data.  
   ZH: 以矢量方式读取源 PDF。
2. EN: Parse path operations such as `m`, `l`, `c`, `re`, and parse text operations when available.  
   ZH: 解析 `m`、`l`、`c`、`re` 等路径操作，并在可用时解析文字操作。
3. EN: Apply PDF page rotation metadata before drawing (for example `/Rotate 270`) so CAD direction matches PDF display direction.  
   ZH: 在绘制前应用 PDF 页面旋转元数据（例如 `/Rotate 270`），确保 CAD 方向与 PDF 显示方向一致。
4. EN: Normalize extracted coordinates so drawing starts from a clean CAD origin.  
   ZH: 对提取坐标做归一化，使绘图从干净的 CAD 原点开始。
5. EN: Preserve original PDF vector proportions first; only scale after the CAD entities are generated.  
   ZH: 先保持 PDF 原始矢量比例绘制实体；只在实体生成后再进行整体缩放。
6. EN: Before drawing, delete old generated entities on `PDF_DIRECT_WALL`, `PDF_DIRECT_TEXT`, and temporary test layers to avoid overlap.  
   ZH: 绘制前删除 `PDF_DIRECT_WALL`、`PDF_DIRECT_TEXT` 以及临时测试图层中的旧生成对象，避免叠图。
7. EN: Use AutoLISP `entmake` to create native CAD entities directly in ModelSpace.  
   ZH: 使用 AutoLISP `entmake` 在 ModelSpace 中直接创建原生 CAD 实体。
8. EN: Put linework on `PDF_DIRECT_WALL`.  
   ZH: 线条放到 `PDF_DIRECT_WALL` 图层。
9. EN: Put extractable text on `PDF_DIRECT_TEXT`.  
   ZH: 可提取文字放到 `PDF_DIRECT_TEXT` 图层。
10. EN: If OCR scaling is enabled, recognize dimension numbers and calculate one global scale factor.  
    ZH: 如果启用 OCR 缩放，则识别尺寸数字并计算一个整图统一缩放系数。
11. EN: Do not calculate scale from text bounding boxes, arrow endpoints, slash marks, text strokes, wall endpoints, or repeated label spacing.  
    ZH: 不使用文字边界框、箭头端点、斜杠、文字笔画、墙线端点或重复文字间距计算比例。
12. EN: For each dimension label, find the nearby dimension line first.  
    ZH: 对每个尺寸文字，必须先寻找文字附近的尺寸线。
13. EN: Find the two extension lines perpendicular to that dimension line, then use the distance between their intersections with the dimension line.  
    ZH: 再寻找与尺寸线垂直的两条尺寸界线，用尺寸线与两条尺寸界线的交点距离作为原始距离。
14. EN: Scale for one label is `dimension number / intersection distance`.  
    ZH: 单个标注比例为 `标注数值 / 交点距离`。
15. EN: Read at least three valid dimension labels and use the median scale as the global scale. If fewer than three valid intersection measurements are found, do not scale.  
    ZH: 至少读取 3 个有效尺寸标注，并取比例中位数作为全局比例；少于 3 个有效交点测量时不缩放。
16. EN: Apply one uniform `SCALE` to generated layers only after the median scale is accepted.  
    ZH: 只有中位数比例通过后，才对生成图层执行一次统一 `SCALE`。
17. EN: Run `ZOOM EXTENTS` after drawing.  
    ZH: 绘制完成后执行 `ZOOM EXTENTS`。

## Rules / 规则

- EN: Do not import PDF or DXF as a block/reference.  
  ZH: 不以块/外部引用方式导入 PDF 或 DXF。
- EN: Do not use PDF coordinate units as real millimeters before calibration.  
  ZH: 校准前不要将 PDF 坐标单位直接视为真实毫米。
- EN: Dimension scaling is optional and must happen after proportional vector drawing is confirmed.  
  ZH: 尺寸缩放是可选步骤，必须在确认矢量比例绘制完成后执行。
- EN: Prefer dimension-line and extension-line intersections only; reject adjacent wall-line guesses.  
  ZH: 只使用尺寸线与尺寸界线交点；拒绝相邻墙线猜测。
- EN: Always apply page rotation before drawing.  
  ZH: 必须先应用页面旋转再绘制。
- EN: Always redraw generated layers cleanly instead of stacking new output on top of previous output.  
  ZH: 每次都应清理后重绘，不在旧结果上叠加新结果。
- EN: When OCR scaling is enabled, validate by measuring the referenced dimension labels in AutoCAD.  
  ZH: 启用 OCR 缩放时，应在 AutoCAD 中测量被引用的尺寸标注进行验收。

## Current Implementation / 当前实现

- `pdf_direct_draw_to_autocad.py`  
  EN: Parse PDF vectors and apply page rotation.  
  ZH: 解析 PDF 矢量并应用页面旋转。
- `autocad_pdf_direct_pipeline.py`  
  EN: Generate AutoLISP and VBS runner scripts, optionally OCR dimensions and calculate scale.  
  ZH: 生成 AutoLISP 与 VBS 执行脚本，并可选 OCR 尺寸标注与计算缩放比例。
- `codex_auto_direct_draw.lsp`  
  EN: AutoLISP output that directly creates CAD entities.  
  ZH: 直接创建 CAD 实体的 AutoLISP 输出文件。
- `run_codex_auto_direct_draw.vbs`  
  EN: Send the AutoLISP command to the running AutoCAD session.  
  ZH: 向当前运行中的 AutoCAD 会话发送 AutoLISP 命令。

## Typical Command / 常用命令

```powershell
py -3 .\autocad_pdf_direct_pipeline.py --pdf 'C:/Users/bobo/Desktop/C区2F-模型.pdf' --scale-from-dimension --ocr
cscript.exe //nologo .\run_codex_auto_direct_draw.vbs
```

EN: The workflow must not force a single-label baseline. If fewer than three valid dimension-line intersection measurements are found, keep proportional PDF units and report the failure reason in `codex_auto_direct_plan.txt`.
ZH: 流程不能强行使用单个标注作为基准。若少于 3 个有效尺寸线交点测量，则保持 PDF 比例单位，并在 `codex_auto_direct_plan.txt` 中报告失败原因.
