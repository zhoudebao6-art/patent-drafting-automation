# 结构化成稿工具

`scripts/authoring_toolkit.py`把已经由模型核对的本案内容 JSON 生成 Word，并在保存后重新打开最终 DOCX，独立核对文字、五分区、源 OMML、媒体和定位。普通初稿不再逐案编写`build_draft.py`或客观对象台账代码。源材料理解、合法“所述”、技术条件、公式分类与独立视觉验收仍由原有 Sol/high、Astra/high 流程完成。

本页是可直接操作的接口说明。`rules.json`和[人工定稿规范](manual-format-and-wording.md)优先；本工具并非所有 Word 内容的无损转换器。遇到下面明确不支持的内容应保留原件，使用原有可审计手工生成路径，不删掉内容来迁就 schema。

## 三个命令

使用本机文档技能解析出的 Python；脚本依赖`python-docx`、`lxml`、`Pillow`及同目录`inspect_docx.py`。`$docPython`须来自已验证的文档运行环境，不指向任意全局 Python。以下路径仅示意，`--case-root`必须是当前案的独立可写根目录。

```powershell
& $docPython -X utf8 scripts/authoring_toolkit.py inventory --case-root 'E:\案件\case001' --source 'sources/原始材料.docx'
& $docPython -X utf8 scripts/authoring_toolkit.py build --case-root 'E:\案件\case001' --input 'draft-content.json' --out 'draft-v1'
& $docPython -X utf8 scripts/authoring_toolkit.py verify --case-root 'E:\案件\case001' --out 'draft-v1'
```

- `inventory`只读，输出源文件 SHA256、`word/document.xml`内全部`oMath`的稳定序号/树哈希，以及媒体 part/哈希。`category: null`和导航文本不能原样充作已核定注册项；模型先分类，去掉`linear_text_for_navigation_only`后再放入`formulas`。
- `build`输出`draft.docx`、输入快照`content.json`、现有预检器的`inspection-cache/`及`build_manifest.json`。缓存内实际`inspection.json`、`content.txt`、源媒体与`cache.json`路径由返回值`inspection`和manifest给出。成稿页眉五分区由工具生成，不在正文重复写“说明书摘要”等分区标题。
- `verify`从最终 ZIP 再读一次，与输入快照和来源重新比对；不会修改产物。返回`structural_readback_passed`只代表本工具负责的客观结构通过，`semantic_review`、`visual_review`永远为`not_run`。

所有参数中的文件路径、`sources[].file`均相对`case-root`，必须位于该根目录内；绝对路径、`..`越界、解析后指向其他目录的链接拒绝。`case-root`本身由派发上下文提供，不能由另一案拷贝替换。源 DOCX/PNG/JPEG必须带 SHA256，构建前后核对；不修改来源。

`--out`必须是不存在的新目录。先在同层短临时目录生成，再独占建立输出目录并以不覆盖方式提交DOCX/输入快照，随后对最终路径独立读回、只调用一次现有结构预检，manifest最后提交。失败不覆盖旧版本；读回失败或提交中断可能留下没有有效manifest的不完整新目录，不能当成成功，应保留现场并使用新版本目录重做。重复执行不会覆盖已成功结果。

与批次交接：工具返回的`output`才是真实稿件路径，例如`attempt/built/draft.docx`；worker回执`artifact`和哈希必须填这个实际产物，不能照抄预留模板里的`attempt/output.docx`。`build`已执行`run_inspection(final_docx, artifact.parent/inspection-cache, 'draft', True, True)`一次，worker直接引用返回的已验证builder-cache，不再按默认prepared路径重复提取。下一轮handoff可按现有`artifact.parent/inspection-cache`命中。`verify`重新核验cache身份、提取器哈希和所有缓存输出；缓存仍是结构预检，不承载语义通过结论。后续若另外复制/改名DOCX，缓存输入身份会变化，应按新实际路径走原有检查。

## 可直接运行的最小 JSON

以下是**非技术、无公式、无图**排版夹具，不是可交付专利稿。保存为本案根目录`draft-content.json`即可构建。实际案件把示例文字替换成已核对内容，登记来源/保护名称/对象，不允许把示例套话当成技术来源。无图仍保留两个空的图分区，五种逻辑分区不省略；这可能产生空图页，应由本案规则及最终视觉复核决定是否另走可覆盖的版式路径。

```json
{
  "schema_version": 1,
  "case_id": "case001",
  "project_title": "文稿组件示例名称",
  "protected_names": [
    {"text": "文稿组件示例名称", "block_ids": ["summary", "title"]}
  ],
  "sources": [],
  "formula_policy": {
    "source_inventory_complete": true,
    "source_main_count": 0,
    "non_omml_formulas": "none",
    "no_formula_reason": "非技术排版夹具，无源公式"
  },
  "formulas": [],
  "figures": [],
  "sections": [
    {"kind": "abstract", "blocks": [
      {"id": "summary", "type": "paragraph", "role": "body", "text": "文稿组件示例名称仅用于检查版式。"}
    ]},
    {"kind": "abstract_figure", "blocks": []},
    {"kind": "claims", "blocks": [
      {"id": "claim1", "type": "paragraph", "role": "body", "text": "1.此处仅为首段排版夹具；"},
      {"id": "claim1_cont", "type": "paragraph", "role": "body", "text": "此处仅为续段排版夹具。"}
    ]},
    {"kind": "description", "blocks": [
      {"id": "title", "type": "paragraph", "role": "project_title", "text": "文稿组件示例名称"},
      {"id": "field", "type": "paragraph", "role": "heading", "text": "技术领域"},
      {"id": "body", "type": "paragraph", "role": "body", "runs": [
        {"type": "text", "text": "这里检查孤立字符"},
        {"type": "symbol_reference", "text": "g"},
        {"type": "text", "text": "的宋体正体基线显示。"}
      ]}
    ]},
    {"kind": "figures", "blocks": []}
  ]
}
```

顶层及各层字段严格检查，未知字段拒绝；ID为1至48字符的字母开头ASCII标识，可含数字、`_`、`-`。段落ID全篇唯一；源、公式、图片各自ID唯一。数组顺序就是正文顺序。正文字符串不支持制表符、内嵌换行、控制字符；真实分段写成多个 block，不能用空格或换行模拟自然段。

## 文字与保护名称

`sections`固定依次为`abstract`、`abstract_figure`、`claims`、`description`、`figures`，每节只有`kind`和`blocks`。正文 block：

```json
{"id":"p001","type":"paragraph","role":"body","text":"已经核对的正文。"}
```

`role`为`body`、`heading`或`project_title`。栏目标题与项目标题只能在说明书分区；全稿恰好一个`project_title`，其纯文本必须精确等于顶层`project_title`。标题中“装置/组件/引擎”原词保留。工具不运行任何全文替换；本发明自称、模块称谓、权项禁词仍由模型按作用域修改JSON，现有`inspection.json`只列候选。

`text`与`runs`二选一。`runs`按序组合以下四种对象：

|run|字段|行为|
|---|---|---|
|`text`|`type,text`|宋体14pt正体、显式黑字；标题/栏目由role加粗。|
|`symbol_reference`|`type,text`，可选`formula_id`|一个普通Latin/Greek字母，正文宋体14pt基线；登记符号与源的语义对应仍需复查。|
|`formula`|`type,formula_id`|从注册来源复制完整OMML，绝不由字符串重建。|
|`figure_ref`|`type,figure_id`|由同一图片映射输出“图N”，避免手工图号漂移。|

特殊字形、上下标、向量粗体、帽号等不得塞入`symbol_reference`；用来源OMML。`protected_names`每项为`text`和非空`block_ids`数组，验证该精确名称在每个声明块中仍存在；自动项目标题核验不依赖这份数组。受保护名称的真实性、穷尽性及普通叙述对象不能过度豁免，仍由Astra按[人工定稿规范](manual-format-and-wording.md)检查。

工具保留权项多自然段，设置首行2字符（560twip）、左对齐、单倍240、312twip行网格，不生成或补齐“所述”。每权项至少3处合法“所述”是语义门禁，字符串计数不能由manifest签通过。

## 源 OMML 绑定

完整来源登记例：

```json
{"id":"source1","file":"sources/原始材料.docx","sha256":"填写真实64位小写哈希"}
```

公式登记和显示块例：

```json
{
  "id": "f1",
  "source_id": "source1",
  "locator": {"part": "word/document.xml", "omath_index": 1},
  "tree_sha256": "填写inventory提供的真实64位小写树哈希",
  "category": "main_formula"
}
```

```json
{"id":"eq001","type":"formula","formula_id":"f1"}
```

1. 源SHA和OMML树SHA都匹配才导入。序号是`word/document.xml`内全部`m:oMath`的1起序号，不是页面、正文段号或主公式编号。源输入发生变化须重新提取与核对，不能只改哈希绕过。
2. `category`只能为`main_formula`、`source_example`、`symbol_reference`。每个来源中的每个OMML必须恰好登记一次；不可漏掉、用两个ID伪装同一源对象。主公式/来源算例各只允许一个目标完整对象，符号引用允许多处复用，但至少使用一次。
3. `formula_policy.source_main_count`须与已核定`main_formula`数相等，成稿按该分类保持N→N。`oMath`本身不是天然的“完整主公式”单位：同一OMML中含多条独立公式等无法建立一对象一式映射的情况，应走原有手工保真路线，不能报成一个主式凑计数。
4. `source_inventory_complete:true`是上游的明确声明，工具不代替模型检查文本、图像、OLE或上下文公式。`non_omml_formulas`当前只接受`none`；确有非OMML公式时禁止假报none，按原有手工路线处理。无公式须提供非空`no_formula_reason`。
5. 深复制完整数学子树，保留字形、`m:scr/m:sty`、上下标、数组、空白与运算顺序，不设置全局`m:sty=p`，不压平`eqArr`，不把ℬ改成B；独立显示只在外层设置居中和无首缩进。多来源的`mathPr`必须一致，原样带入。
6. 观测到依赖外部字符样式、段落样式/段落字体、主题或关系ID、修订的OMML会明确失败，不悄悄丢掉依赖；源数学文字显式非黑色也拒绝，不静默改色或产出红色净稿。源对象未显式指定的字体/大小仍需结合来源继承与最终显示核对；该工具不声称完全复现任意源文档的字体解析环境。缺字号不会擅自改写原OMML；本模板普通基准为14pt，必要的数学排版修正走原有Astra流程并说明依据。

主式、符号、类别及正文解释仍按`formula_registry`要求语义复核。树SHA是忽略XML前缀/属性次序及元素间缩进空白的结构哈希；保留数学文本（包括字面空白）、每个属性、节点次序与所有子结构，不等于原始XML字节哈希。

## 图片及图号

只接受本案独立PNG/JPEG或本案DOCX内`word/media/`图片，复制原字节，不重新编码。注册例：

```json
{
  "id": "fig1", "source_id": "source1", "media_part": "word/media/image1.png",
  "sha256": "填写该媒体真实64位小写哈希", "number": 1,
  "title": "来源已核对的图名", "width_cm": 15
}
```

独立图片源用同样`sources`文件/哈希登记，图片注册中省略`media_part`。`figures`注册数组按最终图号1..N排列，数值需连续。图片宽度限制0.1..16.99cm，按原宽高比缩放，若对应高度超过23cm直接失败，不能挤掉图题。图名不能自由补写技术信息。

每图恰好一个说明书`figure_description`，按图序排列；恰好一个末尾`figures`分区的图片块，按图序排列。摘要附图只可复用图1一次，不新增图号：

```json
{"id":"fig_desc1","type":"figure_description","figure_id":"fig1"}
```

```json
{"id":"fig_image1","type":"figure","figure_id":"fig1"}
```

工具将附图说明输出为“图N为本发明的图名。”，图片后另起相邻段“图N”，图片段`keepNext`、图题段`keepLines`。末尾图片本体不插入实施方式；实施方式用`figure_ref`引用并写核实后的解读。自由文字内出现未登记图号会拒绝；复杂编号、全文所有隐式引用、图内文字和新旧图号语义对应仍需审查。`same_page`明确记`requires_rendered_page_review`，控制属性和高度预估不冒充真实同页证明。

## 模板、读回与审查边界

工具使用`manual-v16-explicit-v1`版式：A4、用户给定边距和312twip网格；正文宋体14pt黑色正体、角色化首缩进；项目名称居中加粗并遵从最新人工稿2字符首缩进；五节独立default页眉页脚，关闭first/even模式。四类页眉黑体14pt字距90twip，权项页眉宋体14pt加粗和0.75pt底边框；其他横线使用一个DrawingML锚定对象，不生成双份兼容线。说明书、说明书附图各从1起用真实PAGE域，页脚载体与域均为黑体9pt；不复制缓存页码。

保存后直接读取最终ZIP，按隐藏书签定位每个block的实际直属正文段，重新校验文字、分区、公式完整树、图片源字节、图题相邻及基本段落/字体格式，并重新统计对象；记录不是生成时计数的自我复述。正文仅允许契约支持的直属段落和末尾分节，块/图题顺序及段内文字与数学对象的相对次序须和输入相同；PAGE域五个载体逐个核验9pt、黑体、黑色，不能只查段落默认值。manifest含输入/成稿/工具/inspection/content哈希、blocks、来源对象与最终位置、五节页眉页脚信息。实际页码仍须渲染后补充，不能把段号写成页号。`inspection.json`复用原检查器，既有六项检查继续使用它，不重复编写另一份语义台账。

当前明确不支持任意Word表格、超链接、脚注、源域、OLE/图片/文本公式自动重建、SVG/EMF转码、源图片重画、任意自定义页眉模板和编辑已有DOCX。JSON的文字可原样写入，但不证明其技术事实、公式完整盘点、术语、合法回指或法律质量。现有复杂稿和Astra保真小改可以继续原路径；不得为用工具而丢表格/公式/图片。已存在稿件被Astra修改后本次manifest自然失效，需要新稿/新审查与原有最终哈希绑定。

首次模板开发必须真实渲染代表页观察。已发现过“页脚载体继承14pt、缺少独立域结果载体使真实PAGE结果与固定横线重叠”的偏差，因此不能仅以五节和域存在就通过；Footer样式、段落标记、完整begin/code/separate/空结果/end域各载体现均显式9pt。只有纯PAGE页脚载体设置9pt定高，使保留的+9.6pt横线位于数字下方；这项细化不施加到正文或数学段。空结果不是伪造的缓存页码。该修正仍须由最终本案逐页检查确认。不同Word/WPS/LibreOffice和字体环境可有差异，非技术夹具的干净页面不保证任意技术稿、长公式和长图可用。

## 回归检查

```powershell
& $docPython -X utf8 -m unittest discover -s scripts -p test_authoring_toolkit.py -v
```

测试覆盖无公式、五节实际页眉/页脚/域、普通宋体14pt正体基线和续段、保护标题不替换、源script/bold/上下标与ℬ保真、图号/摘要复用、源DOCX媒体原字节复制、损坏ZIP/XML和重复JSON键、越界路径、未知字段/原始XML拒绝、未映射源式/重复主式/错误哈希拒绝、旧产物不覆盖、只提取一次且后续缓存命中、缓存媒体损坏拒绝，以及故意篡改生成器输出或最终数学字形后的独立读回拒绝。

这些运行秒数只能说明本地确定性生成/检查的开销，不能声称端到端撰写或复查提升了某个倍数；完整速度须对同样案件范围、质量门禁、模型与重试统计。
