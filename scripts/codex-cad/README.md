# codex-cad

PDF 矢量平面图到原生 AutoCAD 实体的自动化工作流。

An automation workflow that converts vector PDF floor plans into native AutoCAD entities.

## 项目实际解决的问题 | Problems Solved

### 1. PDF 导入后不是可编辑 CAD 实体

直接把 PDF 作为底图、图片或外部参照导入，通常不能得到可独立编辑的墙线和文字。本项目读取 PDF 的矢量路径，并在 AutoCAD 中直接生成原生 `LINE` 和安全的 `TEXT` 实体，不使用 PDF 底图、图片或块覆盖。

Direct PDF import often leaves an underlay, image, external reference, or block instead of independently editable CAD geometry. This workflow reads vector paths and creates native `LINE` entities, plus safe `TEXT` entities, directly in AutoCAD.

### 2. PDF 坐标长度与图纸标注长度不一致

PDF 内部坐标单位不一定等于毫米。启用 `--scale-from-dimension` 后，流程从尺寸文字附近的尺寸线出发：寻找与尺寸线垂直的最近两条尺寸界线，使用两条交点之间的距离计算比例。

PDF coordinate units do not necessarily equal millimetres. With `--scale-from-dimension`, the workflow finds the dimension line near each label, locates the nearest two perpendicular extension lines, measures their intersection-to-intersection distance, and calculates the scale from the labelled millimetre value.

比例规则 | Scale rule:

```text
比例 = 尺寸标注值（mm） / 尺寸线与两条尺寸界线交点之间的原始距离
scale = labelled millimetres / raw distance between the two dimension-line intersections
```

流程会至少采用 3 个不同几何跨度，并以一致比例的中位数作为全局比例。它不会使用文字边界框、箭头端点、斜杠、文字笔画或墙线端点计算比例；同一个几何跨度也不能重复计数。证据不足或比例不一致时，流程会在进入 AutoCAD 前停止，不会静默使用 `1.0`。

At least three distinct geometric spans are required, and the median of the consistent scale group becomes the global scale. Text bounding boxes, arrow endpoints, slash marks, text strokes, and wall endpoints are excluded. The same geometric span cannot count more than once. The workflow stops before AutoCAD if the evidence is insufficient or inconsistent; it never silently falls back to `1.0` in strict mode.

### 3. PDF 文字对象可能编码异常，普通文本提取容易误识别

尺寸识别优先使用 `RapidOCR` 读取渲染后的页面。页面中的房间面积、房间编号和乱码文字不会直接被当作尺寸；OCR 只接受单个纯数字标注。只有 OCR 没有结果时，才回退到 PDF 可提取文字。

Dimension recognition prefers `RapidOCR` on a rendered page because PDF text objects may be font-encoded or mixed with room labels and areas. OCR accepts only a single numeric token as a dimension candidate. Extractable PDF text is used only when OCR returns no candidates.

### 4. 图元太多时，AutoCAD 调度容易长时间无响应

流程将图元拆成默认 500 个实体一批，通过 AutoLISP 逐批生成，并为每批写入完成标记。VBScript 等待上一批完成后再发送下一批；命令拒绝、超时和实体创建失败都会被记录到进度文件中。

Large drawings can make a single AutoCAD command appear frozen. The workflow splits output into batches of 500 entities by default, creates them through AutoLISP, and writes a completion marker for each batch. VBScript submits the next batch only after the previous one is acknowledged, while rejections, timeouts, and entity failures are recorded.

### 5. PDF 路径重复导致 CAD 线条重叠

绘图副本只做完全重复线段去重，同时识别反向重复线段；原始几何仍保留给尺寸测量。这样不会为了清理线条而改变尺寸标定依据。

The drawing copy removes only exact duplicate segments, including reversed duplicates. Raw geometry remains untouched for dimension measurement, so cleanup does not change the calibration evidence.

## 使用了什么 | What It Uses

- `pypdf` 或 `PyPDF2`：读取 PDF 页面、变换矩阵、矢量路径和文字对象。
- `RapidOCR`：识别尺寸标注数字。
- `PyMuPDF` 或 Poppler `pdftoppm`：将 PDF 页面渲染为 OCR 图像。
- AutoLISP `entmake`：在 AutoCAD 中直接创建 `LINE` 和 `TEXT` 实体。
- Windows VBScript + AutoCAD COM：连接当前打开的 AutoCAD 文档、逐批发送 LSP。当前流程没有使用 `pywin32`。
- Python `unittest`：验证尺寸交点、比例中位数、重复跨度保护和坐标缩放。

- `pypdf` or `PyPDF2`: PDF pages, transformation matrices, vector paths, and text objects.
- `RapidOCR`: numeric dimension recognition.
- `PyMuPDF` or Poppler `pdftoppm`: PDF page rendering for OCR.
- AutoLISP `entmake`: direct creation of native `LINE` and `TEXT` entities in AutoCAD.
- Windows VBScript + AutoCAD COM: connects to the active AutoCAD document and submits LSP batches. The current workflow does not use `pywin32`.
- Python `unittest`: validation of dimension intersections, median scaling, duplicate-span protection, and coordinate scaling.

## 实际效果 | Results

输出结果具备以下特征：

- CAD 中得到独立的原生线实体，而不是 PDF 参照或图片。
- 页面旋转和坐标原点在输出前统一处理。
- 标定后的比例直接写入新实体坐标，不再在 CAD 中对整层执行 `SCALE`，因此追加绘制不会重复缩放旧实体。
- 输出前生成比例证据、图元数量、去重数量、批次和运行路径，便于复核。
- 尺寸标定失败时不会继续生成错误比例的 CAD 图。

The output provides:

- Independently editable native CAD linework instead of a PDF underlay or image.
- Page rotation and coordinate-origin normalization before output.
- Scale applied directly to new coordinates; no layer-wide `SCALE` command is used, so append runs do not rescale existing entities.
- A plan file with scale evidence, entity counts, deduplication counts, batches, and runtime paths.
- Fail-fast behavior when dimension calibration is not trustworthy.

当前验证样例 | Current validation sample:

- 9 项单元测试通过。
- “天钥桥路”样例识别出 6 个独立尺寸跨度，计算比例为 `32.765399737877`。
- 原始线段 `18,428` 条，完全重复去重后为 `14,528` 条，拆分为 30 个 AutoCAD 批次。

- 9 unit tests pass.
- The “天钥桥路” sample selected 6 distinct dimension spans and calculated a scale of `32.765399737877`.
- `18,428` raw line segments were reduced to `14,528` after exact deduplication and split into 30 AutoCAD batches.

## 快速使用 | Quick Start

先确保 AutoCAD 已经打开，并且目标图纸是当前活动文档。

Make sure AutoCAD is open and the target drawing is the active document.

安装 OCR 到项目私有目录 | Install OCR into the project-private directory:

```powershell
py -3 -m pip install --target .vendor-ocr rapidocr_onnxruntime
```

生成 AutoLISP 和 VBScript | Generate AutoLISP and VBScript:

```powershell
py -3 .\scripts\codex-cad\autocad_pdf_direct_pipeline.py `
  --pdf "C:/path/to/plan.pdf" `
  --out-dir ".\codex-cad-output" `
  --scale-from-dimension `
  --chunk-size 500 `
  --chunk-timeout 120
```

运行 CAD 绘制 | Run the CAD drawing:

```powershell
cscript.exe //nologo .\codex-cad-output\run_codex_auto_direct_draw.vbs
```

`--scale-from-dimension` 是严格标定模式，要求至少 3 个一致的尺寸跨度。只需要按 PDF 矢量比例绘制、不需要按毫米标定时，可以省略该参数。`--ocr` 仍保留为兼容参数，严格尺寸模式会自动优先 OCR，标注值由流程自动读取。

`--scale-from-dimension` is strict calibration mode and requires at least three consistent dimension spans. Omit it when proportional PDF geometry is sufficient and millimetre calibration is not needed. `--ocr` remains as a compatibility flag; strict dimension mode automatically prefers OCR and reads the labels from the document.

## 输出文件 | Outputs

- `codex_<run_id>_setup.lsp`：创建图层、清理目标图层中的旧生成实体并初始化进度。
- `codex_<run_id>_batch_*.lsp`：逐批创建线和文字实体。
- `codex_<run_id>_finish.lsp`：关闭 Undo 组并执行 `ZOOM EXTENTS`。
- `run_codex_auto_direct_draw.vbs`：连接当前 AutoCAD 文档并按批次调度。
- `codex_auto_direct_plan.txt`：本次运行摘要。
- `codex_dimension_measurements.txt`：尺寸候选、交点距离、比例和选中证据。
- `codex-progress-<run_id>\\progress.txt`：当前阶段、批次和实体失败数。
- `codex-progress-<run_id>\\error.txt`：发生实体创建失败时生成。

- `codex_<run_id>_setup.lsp`: creates layers, clears old generated entities on target layers, and initializes progress.
- `codex_<run_id>_batch_*.lsp`: creates line and text entities in batches.
- `codex_<run_id>_finish.lsp`: closes the Undo group and runs `ZOOM EXTENTS`.
- `run_codex_auto_direct_draw.vbs`: connects to the active AutoCAD document and schedules batches.
- `codex_auto_direct_plan.txt`: run summary.
- `codex_dimension_measurements.txt`: dimension candidates, intersection distances, scale values, and selected evidence.
- `codex-progress-<run_id>\\progress.txt`: current stage, batch, and entity failure count.
- `codex-progress-<run_id>\\error.txt`: created when entity creation fails.

## 限制 | Limitations

- 输入需要包含可解析的 PDF 矢量路径；纯图片 PDF 不会自动还原成 CAD 墙线。
- OCR 只负责尺寸数字识别，不负责从栅格图片中重建全部平面图。
- 当前默认只写入可安全嵌入 AutoLISP 的 ASCII 文字；异常编码或中文文字可能被跳过，但矢量线段仍可生成。使用 `--skip-text` 可以明确只输出线段。
- 运行脚本前不要切换 AutoCAD 当前图纸，也不要同时启动第二个 runner。

- The input must contain parseable PDF vector paths; a raster-only PDF is not automatically reconstructed into CAD walls.
- OCR recognizes dimension numbers; it does not vectorize an entire raster floor plan.
- By default, only ASCII text safe for AutoLISP is emitted. Font-encoded or Chinese text may be skipped while vector linework is still generated. Use `--skip-text` to request linework only.
- Do not switch the active AutoCAD drawing or start a second runner before the current runner finishes.

## 测试 | Tests

```powershell
py -3 -m unittest discover -s .\scripts\codex-cad -p "test_*.py" -v
```

The generated `codex-cad-output*/` directories and local OCR dependency directory are ignored by Git.
