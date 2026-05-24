# PDF Vector to AutoCAD Workflow / PDF 矢量转 AutoCAD 工作流

## Goal / 目标

EN: Draw the PDF vector plan directly into AutoCAD while preserving the PDF's visible direction and relative proportions.
ZH: 将 PDF 矢量平面图直接绘制到 AutoCAD，保持与 PDF 显示一致的方向和相对比例。

EN: This workflow does not convert annotation numbers (for example 3300, 4000, 9000) into real-world millimeter scale. Those numbers are treated as drawing content only.
ZH: 此流程不将标注数字（例如 3300、4000、9000）换算为真实毫米尺度，这些数字仅作为图纸内容保留。

## Final Workflow / 最终流程

1. EN: Read the source PDF as vector data.  
   ZH: 以矢量方式读取源 PDF。
2. EN: Parse path operations such as `m`, `l`, `c`, `re`, and parse text operations when available.  
   ZH: 解析 `m`、`l`、`c`、`re` 等路径操作，并在可用时解析文字操作。
3. EN: Apply PDF page rotation metadata before drawing (for example `/Rotate 270`) so CAD direction matches PDF display direction.  
   ZH: 在绘制前应用 PDF 页面旋转元数据（例如 `/Rotate 270`），确保 CAD 方向与 PDF 显示方向一致。
4. EN: Normalize extracted coordinates so drawing starts from a clean CAD origin.  
   ZH: 对提取坐标做归一化，使绘图从干净的 CAD 原点开始。
5. EN: Preserve original PDF vector proportions; do not scale by dimension annotation text.  
   ZH: 保持 PDF 原始矢量比例，不根据标注数字进行缩放。
6. EN: Before drawing, delete old generated entities on `PDF_DIRECT_WALL`, `PDF_DIRECT_TEXT`, and temporary test layers to avoid overlap.  
   ZH: 绘制前删除 `PDF_DIRECT_WALL`、`PDF_DIRECT_TEXT` 以及临时测试图层中的旧生成对象，避免叠图。
7. EN: Use AutoLISP `entmake` to create native CAD entities directly in ModelSpace.  
   ZH: 使用 AutoLISP `entmake` 在 ModelSpace 中直接创建原生 CAD 实体。
8. EN: Put linework on `PDF_DIRECT_WALL`.  
   ZH: 线条放到 `PDF_DIRECT_WALL` 图层。
9. EN: Put extractable text on `PDF_DIRECT_TEXT`.  
   ZH: 可提取文字放到 `PDF_DIRECT_TEXT` 图层。
10. EN: Run `ZOOM EXTENTS` after drawing.  
    ZH: 绘制完成后执行 `ZOOM EXTENTS`。

## Rules / 规则

- EN: Do not import PDF or DXF as a block/reference.  
  ZH: 不以块/外部引用方式导入 PDF 或 DXF。
- EN: Do not use PDF coordinate units as real millimeters.  
  ZH: 不将 PDF 坐标单位直接视为真实毫米。
- EN: Do not rescale by visible dimension numbers unless explicitly requested later.  
  ZH: 除非后续被明确要求，否则不根据可见标注数字进行尺度换算。
- EN: Always apply page rotation before drawing.  
  ZH: 必须先应用页面旋转再绘制。
- EN: Always redraw generated layers cleanly instead of stacking new output on top of previous output.  
  ZH: 每次都应清理后重绘，不在旧结果上叠加新结果。
- EN: Validate by direction, proportions, and duplicate-layer cleanup rather than real-world length.  
  ZH: 验收以方向、比例和去重清理为主，而非真实长度。

## Current Implementation / 当前实现

- `pdf_direct_draw_to_autocad.py`  
  EN: Parse PDF vectors and apply page rotation.  
  ZH: 解析 PDF 矢量并应用页面旋转。
- `autocad_pdf_direct_pipeline.py`  
  EN: Generate AutoLISP and VBS runner scripts.  
  ZH: 生成 AutoLISP 与 VBS 执行脚本。
- `codex_auto_direct_draw.lsp`  
  EN: AutoLISP output that directly creates CAD entities.  
  ZH: 直接创建 CAD 实体的 AutoLISP 输出文件。
- `run_codex_auto_direct_draw.vbs`  
  EN: Send the AutoLISP command to the running AutoCAD session.  
  ZH: 向当前运行中的 AutoCAD 会话发送 AutoLISP 命令。

## Typical Command / 常用命令

```powershell
& 'C:\Users\bobo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\autocad_pdf_direct_pipeline.py --pdf 'C:/Users/bobo/Desktop/模型.pdf'
cscript.exe //nologo .\run_codex_auto_direct_draw.vbs
```

EN: If a future task explicitly requires real millimeter scaling, add that as a separate optional calibration step after proportional drawing is confirmed.
ZH: 如果后续任务明确要求真实毫米尺度，可在“比例绘制确认正确”后，额外增加一个可选的标注校准步骤。
