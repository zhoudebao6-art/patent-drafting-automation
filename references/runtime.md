# DOCX运行环境与页面验收

本用户于2026-10-13明确要求验证并修复自检发现的Windows渲染阻塞。本技能已配置经过本机探测、签名及实际转换验证的原生LibreOffice，通过文档技能的官方`render_docx.py`生成PDF与逐页PNG。Windows原生路径是本技能此次修复后的实际运行配置；不再把“Windows没有打包LibreOffice”直接判为无法渲染。没有修改系统PATH、Office设置或文档插件文件。

配置保存在[runtime.json](runtime.json)：Python、官方渲染脚本、LibreOffice绝对路径及可执行文件哈希、打包Poppler目录。该配置是当前机器的路径信息，不是便携包自带二进制。换机器或软件升级后先调用依赖加载器，探测安装路径和签名，更新此配置再实测；不能凭旧路径或可执行文件名猜成功。

## 执行

使用依赖加载器返回的文档Python运行：

```text
python scripts/render_docx_checked.py --preflight
python scripts/render_docx_checked.py 当前稿.docx --output-dir 本案/render-r1
python scripts/render_docx_checked.py 当前稿.docx --output-dir 本案/render-r1 --verify
```

先预检再派发本批文稿任务，首次配置或环境改变时用小样实际渲染。预检仅证明依赖可找到；实际转换并打开每一页才证明页面可验收。正常批次中，draft和revise worker在最后一次编辑后渲染；review worker验证`render.json`对应当前稿件SHA256，逐页打开全部PNG，单独记录视觉结论。

包装器只对子进程设置已验证LibreOffice及打包Poppler的PATH。官方脚本创建唯一临时LibreOffice用户配置，不接管用户正在打开的文档。超时只终止本次包装器创建的渲染进程树，禁止按soffice、Word等名称全局结束进程。每次渲染使用新输出目录，失败不覆盖之前的页面或证据。

`render.json`记录输入、渲染器、原生程序及产物哈希，PDF页数必须等于连续编号PNG数量。生成成功状态是`rendered_pending_visual_review`，不是通过复查。改稿后旧渲染证据会被拒绝；必须重新渲染并由Astra/high再验。

本次实际排版自检发现Word的Title样式可能带继承的蓝色下边框。文字和XML预检通过不代表标题、公式、图或分页已通过；视觉发现问题仍由Astra/high修改，不能为了省略修改流程直接标记pass。

## 不可用时

日志保存到本次输出目录的`renderer.log`和`render.json`，报告缺失路径、文件更新或真实转换错误。保留稿件，不自动降低模型强度，不把重绘HTML、截图拼接或单独流程图当作最终Word页面，不宣称视觉验收完成。环境修复后的恢复流程见[批处理流程](batch-workflow.md)。

v4运行配置已随当前文档插件升级到26.1011.11113。每批检查依赖一次；依赖更新后重新实测。Word字节未改变时，审查worker先验证render.json及全部产物后复用已有页面；修改后必须新目录重新渲染。各项目及attempt的渲染目录分开，禁止多个任务共用输出文件名或目录。
