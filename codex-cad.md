# codex-cad 工作流 / codex-cad Workflow

## 目标 / Goal

ZH: 将 PDF 里的矢量平面图直接绘制成 AutoCAD 原生实体，不导入 PDF、不生成图片、不作为块或外部参照插入。  
EN: Draw the vector floor plan from a PDF as native AutoCAD entities, without importing the PDF, generating an image, or inserting it as a block/reference.

ZH: 默认先保持 PDF 矢量比例绘制；当启用尺寸标注缩放时，再根据图纸上的毫米尺寸统一缩放整张图。  
EN: The default flow preserves PDF vector proportions first; when dimension scaling is enabled, the whole drawing is uniformly scaled from millimeter dimension labels.

## 最终流程 / Final Workflow

1. ZH: 读取 PDF 第一页矢量路径。  
   EN: Read vector paths from the first PDF page.
2. ZH: 解析 `m`、`l`、`c`、`re` 等路径操作，并在可用时解析文字。  
   EN: Parse path operations such as `m`, `l`, `c`, and `re`, plus text when available.
3. ZH: 先应用 PDF 页面旋转信息，例如 `/Rotate 270`，确保 CAD 方向与 PDF 显示方向一致。  
   EN: Apply PDF page rotation metadata, such as `/Rotate 270`, before CAD output so the CAD direction matches the PDF display.
4. ZH: 将坐标归一化到干净的 CAD 原点。  
   EN: Normalize coordinates to a clean CAD origin.
5. ZH: 生成 AutoLISP，通过 `entmake` 在 ModelSpace 中创建原生 `LINE` 和 `TEXT` 实体。  
   EN: Generate AutoLISP that creates native `LINE` and `TEXT` entities in ModelSpace with `entmake`.
6. ZH: 每次绘制前清理 `PDF_DIRECT_WALL`、`PDF_DIRECT_TEXT`、`CODEX_DIRECT_TEST` 上的旧生成对象。  
   EN: Before each run, delete old generated entities on `PDF_DIRECT_WALL`, `PDF_DIRECT_TEXT`, and `CODEX_DIRECT_TEST`.
7. ZH: 将线条放到 `PDF_DIRECT_WALL`，可提取文字放到 `PDF_DIRECT_TEXT`。  
   EN: Put linework on `PDF_DIRECT_WALL` and extractable text on `PDF_DIRECT_TEXT`.
8. ZH: 如果启用 `--scale-from-dimension --ocr`，先识别尺寸数字，再计算整图唯一比例。  
   EN: If `--scale-from-dimension --ocr` is enabled, recognize dimension numbers and calculate one global scale factor.
9. ZH: 比例通过后，只对生成图层执行一次统一 `SCALE`。  
   EN: After scale acceptance, apply one uniform `SCALE` to the generated layers only.
10. ZH: 绘制完成后执行 `ZOOM EXTENTS`。  
    EN: Run `ZOOM EXTENTS` after drawing.

## 尺寸缩放规则 / Dimension Scaling Rule

ZH: 不能用文字边界框、箭头端点、斜杠、文字笔画、墙线端点或重复文字间距计算比例。  
EN: Do not calculate scale from text bounding boxes, arrow endpoints, slash marks, text strokes, wall endpoints, or repeated label spacing.

ZH: 水平尺寸，例如 `9000`、`1310`、`7700`，必须先找到数字附近的水平尺寸线，再找数字左右两侧最相邻、并且与该尺寸线相交的垂直尺寸界线。  
EN: For horizontal dimensions such as `9000`, `1310`, and `7700`, first find the nearby horizontal dimension line, then find the nearest vertical extension lines on the left and right side of the number that intersect that dimension line.

ZH: 垂直尺寸同理：先找附近垂直尺寸线，再找数字上下两侧最相邻、并与尺寸线相交的水平尺寸界线。  
EN: Vertical dimensions follow the same rule: find the nearby vertical dimension line, then find the nearest horizontal extension lines below and above the number that intersect that dimension line.

ZH: 原始距离必须取两条尺寸界线与尺寸线的交点距离。  
EN: The raw distance must be the distance between the two extension-line intersections with the dimension line.

ZH: 单个标注比例为 `标注数值 / 交点距离`。  
EN: Per-label scale is `labeled value / intersection distance`.

ZH: 至少读取 3 个有效尺寸标注，并取一致比例组的中位数作为整图比例；少于 3 个时不缩放，只保留 PDF 原比例。  
EN: Read at least three valid dimension labels and use the median of the consistent scale group as the global scale; if fewer than three are found, do not scale and keep PDF proportions.

## 当前代码 / Current Code

- `scripts/codex-cad/pdf_direct_draw_to_autocad.py`  
  ZH: 解析 PDF 矢量、文字与页面旋转，提供坐标归一化后的线段。  
  EN: Parses PDF vectors, text, and page rotation, returning normalized line segments.
- `scripts/codex-cad/autocad_pdf_direct_pipeline.py`  
  ZH: 生成 AutoLISP/VBS，执行可选 OCR 尺寸缩放，并输出测量日志。  
  EN: Generates AutoLISP/VBS, runs optional OCR dimension scaling, and writes measurement logs.
- `scripts/codex-cad/README.md`  
  ZH: 英文版命令、输出与依赖说明。  
  EN: English command, output, and dependency notes.

## 常用命令 / Typical Command

```powershell
py -3 .\scripts\codex-cad\autocad_pdf_direct_pipeline.py --pdf "C:/Users/bobo/Desktop/C区2F-模型.pdf" --out-dir ".\codex-cad-output" --scale-from-dimension --ocr
cscript.exe //nologo .\codex-cad-output\run_codex_auto_direct_draw.vbs
```

EN: The workflow must not force a single-label baseline. If fewer than three valid dimension-line intersection measurements are found, keep proportional PDF units and report the failure reason in `codex_auto_direct_plan.txt`.
ZH: 流程不能强行使用单个标注作为基准。若少于 3 个有效尺寸线交点测量，则保持 PDF 比例单位，并在 `codex_auto_direct_plan.txt` 中报告失败原因。

## 验收 / Verification

ZH: 绘制后在 AutoCAD 中执行 `ZOOM E`，检查 `PDF_DIRECT_WALL` 图层实体是否出现，并测量用于缩放的标注，例如 `9000`、`1310`、`7700`。  
EN: After drawing, run `ZOOM E` in AutoCAD, check that entities exist on `PDF_DIRECT_WALL`, and measure the labels used for scaling, such as `9000`, `1310`, and `7700`.

ZH: `codex_auto_direct_plan.txt` 记录本次 PDF、比例、尺寸识别结果、线段数量与输出文件路径。  
EN: `codex_auto_direct_plan.txt` records the PDF path, scale, dimension recognition results, line count, and generated output paths.

ZH: `codex_dimension_measurements.txt` 记录候选标注、选中的标注、交点距离与比例证据。  
EN: `codex_dimension_measurements.txt` records candidate labels, selected labels, intersection distances, and scale evidence.
