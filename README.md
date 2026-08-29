# codex-pdf-cad

PDF 矢量平面图到原生 AutoCAD 实体的自动化工作流。

An automation workflow for converting vector PDF floor plans into native, editable AutoCAD entities.

## 项目实际解决的问题 | Problems Solved

### PDF 不是可编辑 CAD 实体 | PDF is not editable CAD geometry

读取 PDF 中的矢量路径，在当前打开的 AutoCAD 图纸中直接生成原生 `LINE` 和安全的 `TEXT` 实体，不使用 PDF 底图、图片或外部参照替代 CAD 线稿。

The workflow reads vector paths from the PDF and creates native `LINE` and safe `TEXT` entities in the active AutoCAD drawing. It does not use a PDF underlay, image, or external reference as the drawing result.

### 坐标比例与图纸标注不一致 | PDF coordinates do not match drawing dimensions

需要按毫米标定时，尺寸比例只从尺寸线计算：

1. 找到尺寸数字附近的尺寸线。
2. 寻找与尺寸线垂直的两条最近尺寸界线。
3. 计算尺寸线与两条尺寸界线交点之间的原始距离。
4. 使用 `比例 = 标注值（mm） / 交点距离`。
5. 至少读取 3 个不同跨度，并取一致结果的中位数作为全局比例。

Dimension calibration is based only on dimension geometry:

1. Find the dimension line near the numeric label.
2. Find the two nearest extension lines perpendicular to that dimension line.
3. Measure the distance between the two dimension-line intersections.
4. Use `scale = labelled millimetres / intersection distance`.
5. Use at least three distinct spans and take the median of the consistent results.

文字边界框、箭头端点、斜杠、文字笔画和墙线端点都不参与比例计算。同一个几何跨度不能重复计数；证据不足或比例不一致时，流程会在进入 AutoCAD 前停止，不会静默使用 `1.0`。

Text bounding boxes, arrow endpoints, slash marks, text strokes, and wall endpoints are excluded from scale calculation. The same geometric span cannot be counted twice. The workflow stops before AutoCAD when evidence is insufficient or inconsistent; it never silently falls back to `1.0` in strict mode.

### 图元太多时 AutoCAD 看起来无响应 | Large drawings appear to hang AutoCAD

输出会拆成多个 AutoLISP 批次，由 Windows VBScript 等待上一批完成后再发送下一批，并写入进度和错误文件，避免一次性发送过长命令。

Output is split into AutoLISP batches. A Windows VBScript runner waits for each batch to finish before submitting the next one and records progress and errors.

### 重复路径造成线条重叠 | Duplicate paths create overlapping lines

绘图副本只做完全重复线段去重，包括反向重复线段；原始几何保留给尺寸测量，因此清理不会改变标定依据。

The drawing copy removes only exact duplicate segments, including reversed duplicates. Raw geometry remains available for dimension measurement, so cleanup does not alter calibration evidence.

## 技术路线 | Technology

- `pypdf` 或 `PyPDF2`：读取页面、变换矩阵、矢量路径和文字对象。
- `RapidOCR`：识别尺寸标注数字。
- `PyMuPDF` 或 Poppler：渲染页面供 OCR 使用。
- AutoLISP `entmake`：直接创建 AutoCAD `LINE` 和 `TEXT` 实体。
- Windows VBScript + AutoCAD COM：连接当前活动图纸并进行分批调度。
- Python `unittest`：验证尺寸交点、比例中位数、重复跨度保护和坐标缩放。

- `pypdf` or `PyPDF2`: PDF pages, transformation matrices, vector paths, and text objects.
- `RapidOCR`: numeric dimension recognition.
- `PyMuPDF` or Poppler: page rendering for OCR.
- AutoLISP `entmake`: direct creation of AutoCAD `LINE` and `TEXT` entities.
- Windows VBScript + AutoCAD COM: connection to the active drawing and batch scheduling.
- Python `unittest`: intersection measurement, median scaling, duplicate-span protection, and coordinate-scaling tests.

当前流程没有使用 `pywin32`；AutoCAD 调度通过 Windows VBScript 和 COM 完成。

The current workflow does not use `pywin32`; AutoCAD scheduling is handled through Windows VBScript and COM.

## 快速使用 | Quick Start

确保 AutoCAD 已打开，并且目标图纸是当前活动文档。

Make sure AutoCAD is open and the target drawing is the active document.

安装 OCR 到项目私有目录 | Install OCR into the project-private directory:

```powershell
py -3 -m pip install --target .vendor-ocr rapidocr_onnxruntime
```

按 PDF 矢量比例绘制，不进行毫米标定 | Draw using the PDF vector scale:

```powershell
py -3 .\scripts\codex-cad\autocad_pdf_direct_pipeline.py `
  --pdf "C:/path/to/plan.pdf" `
  --out-dir ".\codex-cad-output" `
  --chunk-size 500 `
  --chunk-timeout 120
```

需要按图上尺寸数字标定时，增加 `--scale-from-dimension`：

For millimetre calibration from dimension labels, add `--scale-from-dimension`:

```powershell
py -3 .\scripts\codex-cad\autocad_pdf_direct_pipeline.py `
  --pdf "C:/path/to/plan.pdf" `
  --out-dir ".\codex-cad-output" `
  --scale-from-dimension `
  --chunk-size 500 `
  --chunk-timeout 120

cscript.exe //nologo .\codex-cad-output\run_codex_auto_direct_draw.vbs
```

`--scale-from-dimension` 是严格模式，要求至少 3 个一致的尺寸跨度。`--ocr` 仍保留为兼容参数；严格尺寸模式会自动优先使用 OCR 读取标注数字。

`--scale-from-dimension` is strict mode and requires at least three consistent dimension spans. `--ocr` remains as a compatibility flag; strict dimension mode automatically prefers OCR for reading dimension labels.

## 输出与验证 | Outputs and Validation

流程会生成 AutoLISP 批次、VBScript runner、比例证据、图元统计和进度文件。生成结果是可独立编辑的 CAD 线实体，而不是 PDF 参照或图片。

The workflow generates AutoLISP batches, a VBScript runner, scale evidence, entity statistics, and progress files. The result is independently editable CAD linework rather than a PDF reference or image.

运行测试：

Run the tests:

```powershell
py -3 -m unittest discover -s .\scripts\codex-cad -p "test_*.py" -v
```

当前验证结果：9 项单元测试通过；测试覆盖尺寸线与尺寸界线交点、至少 3 个独立跨度、中位数比例、重复跨度保护和坐标缩放。

Current validation: 9 unit tests pass, covering dimension-line intersections, three or more distinct spans, median scaling, duplicate-span protection, and coordinate scaling.

更完整的中英文工作流记录见 [`scripts/codex-cad/README.md`](scripts/codex-cad/README.md)。

See [`scripts/codex-cad/README.md`](scripts/codex-cad/README.md) for the full bilingual workflow record.

## 输入限制 | Input Limitations

- 输入需要包含可解析的 PDF 矢量路径；纯图片 PDF 不会自动还原成 CAD 墙线。
- OCR 负责识别尺寸数字，不负责把栅格图片重建为完整平面图。
- 默认只输出适合嵌入 AutoLISP 的 ASCII 文字；异常编码或中文文字可能被跳过，但矢量线段仍可生成。
- 运行期间不要切换 AutoCAD 当前图纸，也不要同时启动第二个 runner。

- The input must contain parseable PDF vector paths; a raster-only PDF is not automatically reconstructed as CAD walls.
- OCR recognizes dimension numbers; it does not vectorize a complete raster floor plan.
- By default, only ASCII text safe for AutoLISP is emitted. Font-encoded or Chinese text may be skipped while vector linework can still be generated.
- Do not switch the active AutoCAD drawing or start a second runner while a run is in progress.

## 许可证边界 | License Boundary

仓库中仍保留部分上游 CAD-Coder/LLaVA 相关源码，因此根目录 `LICENSE` 保留其 Apache-2.0 法律文本。`scripts/codex-cad` 是本项目的 PDF 到 AutoCAD 工作流实现；使用、再发布或拆分仓库时，请同时遵守仓库中各部分代码适用的许可证和署名要求。

This repository still contains upstream CAD-Coder/LLaVA-related source code, so the root `LICENSE` retains the applicable Apache-2.0 legal text. `scripts/codex-cad` contains this project’s PDF-to-AutoCAD workflow; when using, redistributing, or splitting the repository, follow the licenses and attribution requirements applicable to each part.
