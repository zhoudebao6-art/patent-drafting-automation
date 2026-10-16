# DOCX运行环境与页面验收

本技能以当前机器经过验证的转换后端生成同一DOCX的PDF和逐页PNG。v6.25提供正式WPS只读兼容后端，适合本机WPS交付及已发现的LibreOffice数学字形失真；缺省配置仍采用原LibreOffice路径，不能凭后端名称宣布显示正确。新机器只有完成程序身份核验和代表样稿实测后才启用WPS，已有冻结批次保持原配置。没有修改系统PATH、共享Office设置或文档插件文件。

配置保存在[runtime.json](runtime.json)：Python、原LibreOffice工具及Poppler路径、字体身份；`conversion_backend=wps`时另设`wps_program`、`wps_program_sha256`、匹配位数的`wps_powershell`及`wps_powershell_sha256`，可选`wps_progid`默认KWPS.Application。WPS helper随技能冻结，不能由worker随意指定；实际COM宿主程序、转换工具、字体及选项均绑定证据。该配置是当前机器的路径信息，不是便携包自带二进制。换机器或软件升级后先调用依赖加载器，探测安装路径和签名，更新此配置再实测；不能凭旧路径或可执行文件名猜成功。

## 执行

使用依赖加载器返回的文档Python运行：

```text
python scripts/render_docx_checked.py --preflight
python scripts/render_docx_checked.py 当前稿.docx --output-dir 本案/render-r1 --reuse --compact
python scripts/render_docx_checked.py 当前稿.docx --output-dir 本案/render-r1 --verify --compact
```

先预检再派发本批文稿任务，首次配置或环境改变时用小样实际渲染。预检仅证明依赖可找到；实际转换并打开每一页才证明页面可验收。正常批次中，draft和revise worker完成稿件与结构预检；review先完成内容核查，内容可通过时再在当前review目录渲染最终版本，或复用同一当前稿写稿attempt内的有效页面。有明确内容错误时先修改，不提前渲染待改稿。review验证`render.json`对应当前稿件SHA256，逐页打开全部PNG，单独记录视觉结论。

LibreOffice后端只对子进程设置已验证工具PATH，官方脚本创建唯一临时用户配置。WPS后端跨批次串行导出PDF，模型撰写及其他阶段继续并行；打开本次独立临时副本且只读，仅关闭本次Document，不更改Visible、DisplayAlerts等共享设置，不退出WPS或终止共享Office进程。超时保留失败证据，只处理本次创建的helper，不按进程名全局终止。新版本渲染使用新输出目录，失败不覆盖之前的页面或证据；--reuse仅在同一路径稿件、字节、工具/配置/DPI指纹及全部产物核验相同时复用，--compact只返回状态、页数和证据路径。

`render.json`记录输入、渲染器、原生程序及产物哈希，PDF页数必须等于连续编号PNG数量。生成成功状态是`rendered_pending_visual_review`，不是通过复查。改稿后旧渲染证据会被拒绝；必须重新渲染并由Sol/xhigh再验。

本次实际排版自检发现Word的Title样式可能带继承的蓝色下边框。文字和XML预检通过不代表标题、公式、图或分页已通过；视觉发现问题仍由Sol/xhigh修改，不能为了省略修改流程直接标记pass。

## 数学字形兼容性

2026-10-14历史同稿测试及后续批次发现LibreOffice可能忽略OMML正体/粗体或缺显特殊数学字符。v6.25正式包装器通过显式配置提供WPS转换，不再要求worker逐案编写COM、另造兼容证据或修改批次收稿代码。发现斜体替换、粗体丢失、问号/方框或运算符丢失时，先对照DOCX源树，区分稿件错误和渲染器失真，保留失败页面及日志；不能改公式含义去迎合转换器。

兼容复核须使用可用且经过验证的Word/WPS对**同一哈希DOCX**转换或查看，正式WPS后端沿用标准`render.json`、连续`page-N.png`和同一六项收稿契约，记录真实后端、版本/程序哈希、输入与PDF/PNG哈希并逐页验收；不复用另一稿的页面。已选WPS后端失败时报告具体失败，不悄悄降级LO或把转换成功当视觉通过。自动化Office时使用唯一只读副本和独立输出目录，只关闭本次打开的文档，不改变共享应用设置、不退出或终止用户实例。缺少可用兼容工具时保留未通过状态，不能用LO的失真结果签发通过。整页及公式区域检查仍由Sol/xhigh执行；结构诊断通过不能替代实际字形。

## 不可用时

日志保存在本次输出目录：统一`render.json`，LO的`renderer.log`，WPS的`converter.stdout.txt`、`converter.stderr.txt`、`converter-result.json`及`rasterizer.log`。报告缺失路径、身份变化或真实转换错误。WPS超时只终止本次helper，可能留下本次只读副本文档；应先依据输出目录中的唯一副本路径检查并恢复本次转换，不能反复启动新转换、退出共享WPS或把失败当成通过。保留稿件，不自动降低模型强度，不把重绘HTML、截图拼接或单独流程图当作最终Word页面，不宣称视觉验收完成。环境修复后的恢复流程见[批处理流程](batch-workflow.md)。

v5.1运行配置已随当前文档插件升级到26.1013.11914，并使用页脚五分区样例实际验证。每批检查依赖一次；依赖更新后重新实测。Word字节未改变时，审查worker先验证render.json及全部产物后复用已有页面；修改后必须新目录重新渲染。各项目及attempt的渲染目录分开，禁止多个任务共用输出文件名或目录。

v5.0的页面缓存指纹加入配置中font_files指向的实际字体文件内容，以及Windows字体替代配置。本机声明Cambria、宋体和黑体文件；字体更新会使旧缓存失效，配置字体缺失或路径非绝对路径时明确失败。换机器需重新探测这些路径。字体哈希只证明环境一致，不证明字形、公式或页面显示正确，仍须实际查看最终页面。
