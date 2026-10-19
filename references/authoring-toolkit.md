# 结构化成稿工具

`scripts/authoring_toolkit.py`把已经由模型核对的本案内容 JSON 生成 Word，并在保存后重新打开最终 DOCX，独立核对文字、五分区、源 OMML、媒体和定位。普通初稿不再逐案编写`build_draft.py`或客观对象台账代码。源材料理解、合法“所述”、技术条件、公式分类与独立视觉验收仍由原有 Sol/high、Sol/xhigh 流程完成。

本页是可直接操作的接口说明。`rules.json`和[人工定稿规范](manual-format-and-wording.md)优先；本工具并非所有 Word 内容的无损转换器。遇到下面明确不支持的内容应保留原件，使用原有可审计手工生成路径，不删掉内容来迁就 schema。

版本31名称校验：project_title须使用本案核定名称原文，不能默认拼接一种。名称槽中与其仅一种前缀增删或重复不同的写法，会触发NAME-01/title_reference_prefix；先按来源修正权项与完整名称引用，再生成方案。原标题自带一种保留，其他类别专属名称另按来源核定，普通一种用法和公式不自动替换。详见[名称原样引用](invention-content-standard.md#版本31-名称原样引用)。

摘要正文提交前按[摘要固定话术](writing-standard.md#摘要固定话术)写成“本发明涉及【领域】，公开了【完整名称】。该【客体】包括……”，同时准备技术领域的完整首句。工具保留已核定内容，不自动猜测领域或客体；技术句式和来源仍在原独立复查中核定。

权项引导语固定“其特征在于，”；生成输入即检查普通文本及跨text run，错误标点须改正后再生成，现有最终权项映射检查同时拦截。后续“包括：”不改。正体化不支持的Unicode数学斜体字母、未知字体继承及图片/OLE走既有来源保真处理并查看最终页，不以工具成功代替视觉核定。

## 三个命令

版本63先执行[初稿公式正确性校核](technical-review.md#公式)，再提交已核定的内容JSON；生成器只证明受支持结构及映射，不能证明源式数学正确。唯一有据修正保留原/改式和来源，不支持的结构按既有保真路径处理；未知保留formula_registry的unresolved并按needs_input交接，不把工具成功当初稿语义通过。不新增生成前脚本或独立校核模型。

使用本机文档技能解析出的 Python；脚本依赖`python-docx`、`lxml`、`Pillow`及同目录`inspect_docx.py`。`$docPython`须来自已验证的文档运行环境，不指向任意全局 Python。以下路径仅示意，`--case-root`必须是当前案的独立可写根目录。

```powershell
& $docPython -X utf8 scripts/authoring_toolkit.py inventory --case-root 'E:\案件\case001' --source 'sources/原始材料.docx'
& $docPython -X utf8 scripts/authoring_toolkit.py build --case-root 'E:\案件\case001' --input 'draft-content.json' --out 'draft-v1'
```

- `inventory`只读，输出源文件 SHA256、`word/document.xml`内全部`oMath`的稳定序号/树哈希，以及媒体 part/哈希。`category: null`和导航文本不能原样充作已核定注册项；模型先分类，去掉`linear_text_for_navigation_only`后再放入`formulas`。
- `build`输出`draft.docx`、输入快照`content.json`、现有预检器的`inspection-cache/`及`build_manifest.json`。缓存内实际`inspection.json`、`content.txt`、源媒体与`cache.json`路径由返回值`inspection`和manifest给出。成稿页眉五分区由工具生成，不在正文重复写“说明书摘要”等分区标题。
- `verify`从最终 ZIP 再读一次，与输入快照和来源重新比对；不会修改产物。返回`structural_readback_passed`只代表本工具负责的客观结构通过，`semantic_review`、`visual_review`永远为`not_run`。

`build`成功已包含独立读回，不紧接着再执行同样的`verify`。仅外部修改、恢复或具体完整性疑点时执行`authoring_toolkit.py verify --case-root 本案根目录 --out 产物目录`。需要来源段落、完整OMML或问题定位时直接用`prepared.pipeline.evidence_query`查询已有inspection，不重新解压或逐案写查询脚本；例如`evidence_query.py inspection.json --section paragraphs --start 1 --end 30`，按返回next_offset继续而不重叠读取。`--section formulas --ids F0001`保留完整数学对象；导航结果不表示已核定公式或审查通过。

所有参数中的文件路径、`sources[].file`均相对`case-root`，必须位于该根目录内；绝对路径、`..`越界、解析后指向其他目录的链接拒绝。`case-root`本身由派发上下文提供，不能由另一案拷贝替换。源 DOCX/PNG/JPEG必须带 SHA256，构建前后核对；不修改来源。

`--out`必须是不存在的新目录。先在同层短临时目录生成，再独占建立输出目录并以不覆盖方式提交DOCX/输入快照，随后对最终路径独立读回、只调用一次现有结构预检，manifest最后提交。失败不覆盖旧版本；读回失败或提交中断可能留下没有有效manifest的不完整新目录，不能当成成功，应保留现场并使用新版本目录重做。重复执行不会覆盖已成功结果。

与批次交接：工具返回的`output`才是真实稿件路径，例如`attempt/built/draft.docx`；worker回执`artifact`和哈希必须填这个实际产物，不能照抄预留模板里的`attempt/output.docx`。`build`已执行`run_inspection(final_docx, artifact.parent/inspection-cache, 'draft', True, True)`一次，worker直接引用返回的已验证builder-cache，不再按默认prepared路径重复提取。下一轮handoff可按现有`artifact.parent/inspection-cache`命中。`verify`重新核验cache身份、提取器哈希和所有缓存输出；缓存仍是结构预检，不承载语义通过结论。后续若另外复制/改名DOCX，缓存输入身份会变化，应按新实际路径走原有检查。

## 可直接运行的最小 JSON

版本27实际案件的纯文字权项默认在顶层设置`"invention_from_claims":true`。在description中把`role=heading`的发明内容与正文有益效果引导句紧接排列，中间不手写目的/方案；生成器从当前claims自动插入固定两句及去头后的全部技术段。内部所述、条件及续段保留；多run的text支持跨run引用头。不能和手写方案混用。含公式/图片/域/结构符号的权项及正文真实跨权项引用暂走原有来源保真手工路径，不能删对象迁就快捷生成。详细固定句、允许转换和同次核验见[invention-content-standard.md](invention-content-standard.md)。本选项不代替权项来源、所述合法性及独立内容审查。

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

v5.9的manual-v26-complete-symbols-no-spaces-v5提前拒绝可见文字与源数学中的字面空白，不自动粘连英文名称或改变公式。字段说明、来源路径等元数据不按可见正文处理。各正文/权项autoSpaceDE/DN关闭，页眉字距、首行缩进与数学自然间距保留。详见[完整符号、分行与空格](formula-detail-standard.md)。

具体带索引量的解释使用`{"type":"source_symbol","formula_id":"f1","node_path":"./m:sSub[1]"}`，node_path按原式准确定位完整最外层符号，支持r、sSub、sSup、sSubSup、sPre、acc、bar、groupChr。路径每级必须有1起序号，只支持m命名空间的相对子路径；选入完整下标/修饰结构的内部片段会拒绝，不支持的附加修饰不能通过选取内部裸字母绕过。生成器复制原子树、保留源树SHA及片段路径，不重新键入下标；普通裸字母索引仍可用symbol_reference。来源分类与解释含义由复查模型核定。

同一源oMath含多条完整等式时，在公式登记中显式提供equation_count，source_main_count按完整等式合计；不按OMML对象数认定主公式数。独立formula块可提供display_breaks，例如`[4]`表示在原oMath第4个非oMathPr顶层子节点之后换行。只支持完整关系之间、原独立逗号/分号节点之后的边界，原节点次序及数学内容不变；已是eqArr的公式直接保留，不再包一层。内部复杂条件不猜拆。source_symbol不增加主公式数。读回核对原来源SHA、原树SHA、显示树SHA、各行节点顺序、符号完整性和目标位置。

`sections`固定依次为`abstract`、`abstract_figure`、`claims`、`description`、`figures`，每节只有`kind`和`blocks`。正文 block：

```json
{"id":"p001","type":"paragraph","role":"body","text":"已经核对的正文。"}
```

`role`为`body`、`heading`、`project_title`或`benefit`。`benefit`只用于description分区内发明内容的有益效果引导句之后；提供不带编号的text或runs，工具按段生成1.、2.等文字前缀且不插空格，保留原run和OMML顺序，使用body的宋体14pt正体及2字符首缩进。已有序号的benefit输入拒绝；手写编号body也须连续，可与benefit按当前序号衔接。引导句不编号，离开该栏目不得继续使用benefit。栏目标题与项目标题只能在说明书分区；全稿恰好一个`project_title`，其纯文本必须精确等于顶层`project_title`。标题中“装置/组件/引擎”原词保留。工具不运行任何全文替换；本发明自称、模块称谓、权项禁词仍由模型按作用域修改JSON，现有`inspection.json`只列候选。

`text`与`runs`二选一。`runs`按序组合以下四种对象：

|run|字段|行为|
|---|---|---|
|`text`|`type,text`|宋体14pt正体、显式黑字；标题/栏目由role加粗。|
|`symbol_reference`|`type,text`，可选`formula_id,style`|独立普通A–Z/a–z字母用body宋体14pt正体，reference_letter为同样正文样式的兼容别名。带formula_id时须匹配完整已登记裸量，仍输出普通文字并保留来源证据；非英文单个公式符号不走普通symbol_reference文本入口，使用source_symbol或已登记的来源OMML formula；特殊字形不能降级，角色仍须复查。|
|`formula`|`type,formula_id`|按注册身份导入：真实公式保留完整OMML；symbol_reference中的独立普通英文单字母转正文w:r，复杂引用保留OMML并以正文14pt为基准；prose_example/prose_label仍为文字，保留原树和来源证据。|
|`figure_ref`|`type,figure_id`|由同一图片映射输出“图N”，避免手工图号漂移。|

特殊字形、上下标、向量粗体、帽号等不得塞入`symbol_reference`；用来源OMML。`protected_names`每项为`text`和非空`block_ids`数组，验证该精确名称在每个声明块中仍存在；自动项目标题核验不依赖这份数组。受保护名称的真实性、穷尽性及普通叙述对象不能过度豁免，仍由复查模型按[人工定稿规范](manual-format-and-wording.md)检查。protected_names在流程图范围外仍按原规则保护合法完整名称（包括非流程图及混合图独立非流程部分），不为流程图内装置/组件/引擎称谓豁免；图内实际文字（含图片像素和完整标题）须据源修正并独立查看最终图及全部复用页，生成器的块/名称校验不证明图内用词合规。

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
2. `category`为`main_formula`、`source_example`、`symbol_reference`、`prose_example`、`prose_label`。后两类仅用于来源已有、经语义核定的普通数字算例或平面标签，不计主公式。每个来源中的每个OMML仍须恰好登记一次；不可漏掉、用两个ID伪装同一源对象。版本62确认重复的main_formula/source_example项可增加duplicate_of（唯一非别名主项ID）及duplicate_reason（核定同一身份、含义和作用域的依据），所有源位置保留但该别名不能输出；同类别、equation_count及完整关系指纹须一致，别名链/环、无理由、异式或仍输出别名均拒绝。不支持的跨表示/复杂等价归并走原来源保真路线，不猜删式。其余主公式/来源算例及普通算例/标签各对应一个目标出现；符号引用可复用至少一次，完整等式不能冒充symbol_reference。目标表示可以是源数学对象或按核定身份输出的普通字符，不用OMML数核算主式N。
3. `formula_policy.source_main_count`须与已核定的非duplicate_of主项中各`main_formula`的`equation_count`合计相等，未指定时每项为1，成稿按分类保持N→N。`oMath`不是天然的完整主公式单位：同一对象含多条完整公式时明确计数；具备安全顶层边界时可使用`display_breaks`，原有合法`eqArr`可直接保留。仅计数无法核定或工具不支持的结构走原有手工保真路线，不能报成一个主式凑计数。
4. `source_inventory_complete:true`是上游的明确声明，工具不代替模型检查文本、图像、OLE或上下文公式。`non_omml_formulas`当前只接受`none`；确有非OMML公式时禁止假报none，按原有手工路线处理。无公式须提供非空`no_formula_reason`。
5. 对真正公式和结构记号深复制完整数学子树，保留有义字体族和粗体、上下标、数组和运算顺序；版本66在显示副本统一正体，`m:sty`的i→p、bi→b并关闭w:i/w:iCs，原树和源SHA保持；字面空格须按版本26先核定并修正，不能盲删。不设置全局`m:sty=p`，不把ℬ改成B；版本34仅允许显式single_line移除已核定的纯外层显示续行eqArr壳，嵌套数学结构不展开。独立显示在居中无首缩进段内，单式在对象外补普通中文分号，eqArr多行组在末行补显示分号，另存显示树SHA并保留原数学节点。源末行最后run已带分号则不重复，内部括号/下标分号不视为段末。多来源的`mathPr`必须一致，原样带入。
6. 观测到依赖外部字符样式、段落样式/段落字体、主题或关系ID、修订的OMML会明确失败，不悄悄丢掉依赖；源数学文字显式非黑色也拒绝，不静默改色或产出红色净稿。源对象未显式指定的字体/大小仍需结合来源继承与最终显示核对；该工具不声称完全复现任意源文档的字体解析环境。默认不改原OMML字号；明确single_line且核定source_base_pt后，才按比例缩放显式字号、补全局部字号。模板普通基准14pt，具体方法见下节，最终页面仍须核定。

版本62结构回读另在同一结果的source_formula_aliases保留每个重复源项的原文件/SHA/位置/树SHA、duplicate_of、依据和唯一目标出现；原源文件不改，不用删除登记来降低N。工具的完整关系指纹只是保守支持范围，未命中不证明无重复；全文仍用源唯一清单逐条核查。

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

独立图片源用同样`sources`文件/哈希登记，图片注册中省略`media_part`。版本28错误流程图由模型依据本案文字完成修正，再按这条既有路径导入本attempt内PNG/JPEG；sources是工具输入表，不把衍生图升级为本案原始来源。原始selection/source SHA保持，现有figure_registry记录原图SHA、文字依据、修正图SHA和改动；生成器只复制图，不推断流程也不自动证明修正正确。`figures`注册数组按最终图号1..N排列，数值需连续。图片宽度限制0.1..16.99cm，按原宽高比缩放，若对应高度超过23cm直接失败，不能挤掉图题。图名不能自由补写技术信息。

每图恰好一个说明书`figure_description`，按图序排列；恰好一个末尾`figures`分区的图片块，按图序排列。摘要附图只可复用图1一次，不新增图号：

```json
{"id":"fig_desc1","type":"figure_description","figure_id":"fig1"}
```

```json
{"id":"fig_image1","type":"figure","figure_id":"fig1"}
```

工具将附图说明输出为图N为本发明的完整图名；版本32图名末尾缺图时只补一个图，已有流程框图、示意图等结尾不重复，非末条分号、末条句号，使用原黑字正文格式。图名字段不带句末标点，重复图或含句末标点明确拒绝，修正输入后生成；不修改原注册title、图号和媒体。仅文末figures的图片后另起相邻段“图N”，图片段`keepNext`、图题段`keepLines`；abstract_figure复用图像但不生成caption，也不与后续文字keepNext。末尾图片本体不插入实施方式；实施方式用`figure_ref`引用并写核实后的解读。自由文字内出现未登记图号会拒绝；复杂编号、全文所有隐式引用、图内文字和新旧图号语义对应仍需审查。`same_page`明确记`requires_rendered_page_review`，控制属性和高度预估不冒充真实同页证明。

## 模板、读回与审查边界

工具使用`manual-v23-source-math-prose-v4`版式：A4、用户给定边距和312twip网格；正文宋体14pt黑色正体、角色化首缩进；项目名称居中加粗并遵从最新人工稿2字符首缩进；五节独立default页眉页脚，关闭first/even模式。四类页眉黑体14pt字距90twip，权项页眉宋体14pt加粗和0.75pt底边框；其他横线使用一个DrawingML锚定对象，不生成双份兼容线。说明书、说明书附图各从1起用真实PAGE域，PAGE域载体为黑体9pt，横线及空段按各自角色设置字体和行高；不复制缓存页码。

保存后直接读取最终ZIP，按隐藏书签定位每个block的实际直属正文段，重新校验文字、分区、公式完整树、图片源字节、图题相邻及基本段落/字体格式，并重新统计对象；记录不是生成时计数的自我复述。正文仅允许契约支持的直属段落和末尾分节，块/图题顺序及段内文字与数学对象的相对次序须和输入相同；PAGE域五个载体逐个核验9pt、黑体、黑色，不能只查段落默认值。manifest含输入/成稿/工具/inspection/content哈希、blocks、来源对象与最终位置、五节页眉页脚信息。实际页码仍须渲染后补充，不能把段号写成页号。`inspection.json`复用原检查器，既有六项检查继续使用它，不重复编写另一份语义台账。

当前明确不支持任意Word表格、超链接、脚注、源域、OLE/图片/文本公式自动重建、SVG/EMF转码、源图片重画、任意自定义页眉模板和编辑已有DOCX。JSON的文字可原样写入，但不证明其技术事实、公式完整盘点、术语、合法回指或法律质量。现有复杂稿和复查模型保真小改可以继续原路径；不得为用工具而丢表格/公式/图片。已存在稿件被复查模型修改后本次manifest自然失效，需要新稿/新审查与原有最终哈希绑定。

首次模板开发必须真实渲染代表页观察。v5.1按人工稿恢复多段页脚：可见横线载体固定200twip行高、黑体10pt；摘要/权项尾空段固定207twip（10.35pt），这是复现人工稿有效空行度量的显式参数，并非原XML字面值。说明书/附图在载体后增加200twip空段和9pt TNR/宋体单倍尾段。所有底线相对正文列+1pt，保留原footer距离227/70twip及纵向−3/+9.6pt。PAGE域的begin/code/separate/空结果/end各载体保持9pt黑体黑色，空结果不是伪造页码。摘要附图保持独立空页脚，避免继承错误。读回同时验证段数、行高、字体、锚点和Footer样式；不得再把页脚压成单段。这些参数不施加到正文或公式。相同官方渲染器下非技术五页样例的横线位置与人工稿对应区一致；未以Word/WPS另行验证，最终每案仍须逐页查看。

## 回归检查

```powershell
& $docPython -X utf8 -m unittest discover -s scripts -p test_authoring_toolkit.py -v
```

测试覆盖无公式、五节实际页眉/页脚/域、普通宋体14pt正体基线和续段、保护标题不替换、源script/bold/上下标与ℬ保真、图号/摘要复用、源DOCX媒体原字节复制、损坏ZIP/XML和重复JSON键、越界路径、未知字段/原始XML拒绝、未映射源式/重复主式/错误哈希拒绝、旧产物不覆盖、只提取一次且后续缓存命中、缓存媒体损坏拒绝，以及故意篡改生成器输出或最终数学字形后的独立读回拒绝。

这些运行秒数只能说明本地确定性生成/检查的开销，不能声称端到端撰写或复查提升了某个倍数；完整速度须对同样案件范围、质量门禁、模型与重试统计。

版本5.2沿用本生成器的原式复制与树SHA读回。新的标点/话术线索由同一次inspect_docx提供；公式中的引号和括号保留，不作全局字符删除。普通文字的改写由指定写稿/修稿模型完成，只有最终独立复查及交付门禁通过才作为成稿。


v5.5的manual-v22-natural-flow-v3按角色控制分页：body的keepLines/keepNext/pageBreakBefore/widowControl为0，短标题与文末图片采用必要的keepNext，完整公式及图下号保持keepLines。分节直接附在本节最后内容段；空逻辑节才保留最小1pt结构载体。没有减少或拆分技术文字、重排公式节点，也没有改变页脚定位空段。摘要图的客观记录caption_locator/caption_text为null，文末图仍有图号定位。读回拒绝普通正文重新启用分页束缚、摘要多出图号、额外满行高分节载体；后续验收复用page_flow_evidence，实际页面仍须查看。


### 正文符号与来源绑定

按已核定身份选择表示。来源绑定符号的正文/数学角色、完整节点及显示副本处理，按[公式与解释符号一致](formula-detail-standard.md#版本43-公式与解释符号一致)执行；已读该节不重复读取。prose_example/prose_label仍保留每个原有数值、记号和顺序；source_text_occurrences与omml_occurrences分开报告，main_formula核算主式N。源中已有普通文字直接用text，不绕道OMML。

普通符号示例：`{"type":"symbol_reference","text":"J","style":"reference_letter"}`。版本60中该参数为body兼容别名，输出正文宋体14pt正体；普通A–Z/a–z单字母不包OMML；希腊字母等其他单个公式符号及结构记号、有义字形均保留数学格式。正文/benefit开头式中，或式中：定向改为其中，或其中：，支持跨相邻text run，保留实施方式中/公式中的；其他回指由模型依原意改写。所有读回仍比对片段次序、精确文字、源树、字体和分号，最终视觉/语义不由manifest代签。

分类为symbol_reference的片段只能在paragraph.runs内引用，不能用独立formula块升格为完整公式；此限制同样适用于带结构的引用。

### v6.3 公式组标点与完整引用

v6.3生成档案标识为`manual-v30-inline-formula-roles-v6`，继承原Word模板参数，新增以下数学显示处理；当前标识见v6.6节。

已声明`equation_count>=2`的完整顶层eqArr组在显示层补齐非末行逗号和末行分号，保留每个源数学节点及顺序，另记显示树SHA。行数不匹配、条件或续行拒绝自动处理；独立段之间的分组仍按来源核定，不猜相邻段同组。

`source_symbol`可用`node_paths`代替`node_path`，如`["./m:r[1]","./m:r[2]"]`复制组合量的连续完整节点；两字段只能给一个，不能越过运算符、乱序或截断脚标。版本40不再允许source_symbol后接普通文本`=−4`拼出等式；直接赋值及数字参数函数等式均用一个formula run导入来源完整关系，包括等号、符号、数值和运算部分。source_symbol仅用于完整符号引用，版本60的`auto`与`math`按同一角色处理：独立普通英文单字母均转正文w:r；复杂完整引用保留OMML并对齐正文基准字号，不能用显式math恢复已禁止的裸字母包装。文字源式按来源保真建立OMML走原有路径，工具不从文字猜上下标或补算式；主式数量不因表示转换增加。同次inspection的精确候选交原formulas复核并修正，不新增扫描或模型阶段。

算子解释用`{"type":"source_operator","formula_id":"f1","name_path":"./m:r[2]","argument_path":"./m:d[1]"}`。路径必须指向原式中相邻的完整字母算子名与单参数圆括号结构；输出完整name(·)，名称保留原字符、字体族和有义粗细，按版本66与公式本体同步正体；参数替成占位点，原真实参数调用不变。记录source_formula_id、source_tree_sha256、source_fragment_path列表与source_transform=operator_argument_placeholder。此引用不增加主式数量；不支持的函数结构走原有保真路径，不硬凑路径或新增公式。

### v6.6 同一条长式放在一行

当前档案`manual-v34-single-line-equation-v7`。先依据原式确认一条完整公式；仅在独立formula块显式提供下例选项，与display_breaks互斥。示例12pt是待实际页面验证的排版值，不是所有公式统一字号。

```json
{"id":"display1","type":"formula","formula_id":"f1","single_line":{"classification":"single_equation","source_base_pt":14,"font_size_pt":12}}
```

equation_count必须为1。source_base_pt须由原式显式属性和继承核定；font_size_pt只能为9至14的半磅步进，且不大于源基准。先从14pt开始，仅为放入实际列宽局部减小，选仍清楚的最大字号。工具支持只有一个外层等号的完整单式，或纯顶层eqArr显示续行：第一行可停在等号，也可已有右式，后续加减项按原序复制。未知数组属性、条件、多条完整等式及嵌套强制断点明确拒绝；只有已知外层显示壳和直接数学run的对齐/显示断点可移除，嵌套矩阵、上下限、分式和数学子树不重建。

字号只写入该式的数学run/控制属性、段落标记和同段收尾分号；显式原字号按font_size_pt/source_base_pt缩放，半磅取整，缺失字号用局部基准或同run已有字号补齐。字体族、粗体、花体、帽号、完整上下标和节点次序保持，正文及符号解释不缩小。原source_tree_sha256继续绑定未改源，display_tree_sha256绑定实际输出，source_objects记录single_line及主式数量；保存后读回逐树核对，并校验段落及分号字号。不能以改显示树为由改源内容或凑公式数。

同次inspect_docx提供single_equation_continuation_hits，已识别的单式显示续行须改稿，不能用来源身份豁免；缓存绑定新增helper哈希。此检查不是自动视觉适配器，不检测所有自动折行或跨段续行。最终仍在原formulas/rendered_pages核对同一DOCX的公式及整页：一行、可读、无越界或裁切、分号同排。工具边界之外继续原保真修稿流程，不能强行展开矩阵、盲缩到小字、隐藏溢出或宣布通过。

## 版本38文末图外文字

figures分区只允许figure块，输出图像和单独居中的图N；不接受单位说明、注释或其他paragraph。保存读回同时拒绝图号段内br/cr/tab、域、自动编号、公式和其他对象，不能把看似图N的混排段当作合格图号。修订稿最终通过number_only_v38封存报告检查。源图内坐标、单位、图例和节点保持，正文figure_description继续写完整图名；abstract_figure仍无外置编号。工具不自动裁剪图片，不新增解释，最终页仍须人工标准复查。

## 版本39完整函数量和符号解释覆盖

source_symbol可用node_path指向完整m:func，或用相邻node_paths同时选择函数头及完整m:d参数，例如头路径与参数路径；实际上下标、帽号及全部参数结构保留，仍记录complete_symbol_copy及原片段路径，不增加主公式数量。只取函数头、只取参数括号、截掉完整量的修饰都会拒绝。函数参数中的完整输入量可另作source_symbol解释，不能据此宣称解释了整个调用；source_operator的name(·)仍只用于算子本体。源结构歧义如字母乘括号表达式，按本案语义走原保真路径，不猜函数。

普通symbol_reference如果携带formula_id，只允许与该条symbol_reference类别原裸量的完整字符、结构和有义字形一致；不能把带下标/帽号/向量的量或完整主式绑定到一个字母。未绑定普通i等索引仍可输出，但不覆盖完整量。工具不自动穷尽所有数学符号或判断解释含义；按formula-detail-standard.md版本39在既有formula_registry.symbols逐项关联实际文中解释，原formulas检查缺失/截短/未知含义不能通过。

版本41/67说明文字由写稿/修稿阶段按实际身份去重：同身份同义同域的完整符号只在当前稿首次出现它的公式后解释，后式只写新符号且共用首式解释定位，必要局部条件留在适用处。每式symbols及覆盖仍包含全部使用符号，源对象ID不同不等于数学身份不同；生成器不凭字符串或XML自动删除解释。同一算子的完整source_operator记号及含义已经解释时，不再额外输出裸名为算子名称等重复text/symbol_reference说明。保留完整数学记号；前句独有技术信息合并至完整解释，独立变量/索引、有独立含义的具体调用量与算子本体、同名异义仍按原角色；不为已知运算和输入构成的每层组合输出重复解释，完整边界依formula-detail-standard.md版本39。生成器仅保真输出核定内容，不按字符串自动删除；复用原formulas及symbols关联，不改变原公式复制或主式数量。

版本42导入流程图前，按technical-review.md核定逻辑并优化交叉/重叠/穿字穿框的布局；生成器复制媒体不等于连线布局合格，不从图片哈希或导入成功自动判通过。将当前attempt的修正图走既有独立图片来源路径，保持原图身份和来源映射，同步摘要/文末复用；原figures和rendered_pages实际检查完整最终图及当前Word页。仅布局修改保留核定节点/边/条件/回路，必要同图成对续接记原边映射；不改工具契约、不新增图审阶段。

### v6.15 来源绑定符号保真

与[正文符号与来源绑定](#正文符号与来源绑定)为同一契约，按该节执行，不重复读取；完整结构及源/显示树核对不变。

### v6.16 转置与英文记号检查

本工具不自动判断T的数学角色，也不自动翻译中文或生成新变量。源OMML含中文时停止直接导入并提示按原保真路径核定英文符号映射；保留原文件SHA、raw_formula/原树，记录normalized_formula/规范树及逐处映射，不将规范稿冒充原技术来源。受影响部分归一化后，正文解释复用同一完整规范符号，所有公式出现一致。

同次inspect_docx在既有formula_prose_evidence.review_candidates定位math_cjk_requires_english_symbol与superscript_T_requires_role_review，分别记录数学节点内偏移及精确math_path。output_contract对前者无source_math_identity/not_formula豁免；后者只有源支持非转置且format_disposition=not_matrix_transpose、object_id和math_path精确匹配才可关闭。真实转置改成上标⊺后重新提取；一个数学对象内多个T逐槽核对。普通正文、图内非公式文字及字体名不扫描为数学中文；图片/OLE/文本数学仍在原六项检查内核对。归一化不增加完整主公式数量，不新增模型阶段或扫描通道。

### v6.17 完整解释的语义边界

source_symbol已支持完整上下标结构和带实际参数的函数，拒绝截取结构内部基字或漏掉相邻参数；该保真能力不能证明该完整对象在文中已被解释。版本45不再把星号、裸E、裸∇等部件说明当作Δθ*、E_b(θ)、∇_θ的完整覆盖。解释引用完整规范片段，索引和功能标识的实际记号及含义由原formulas据源核对；s.t.等平面标点串若不在通用片段支持范围，使用既有保真表示，不修改源符号来迁就工具。不新增数学解析器、运行扫描、字段或阶段；同身份同义同域对象共用首式解释定位，后式不重复定义；非全部表达式都另造定义。
