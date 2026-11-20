# 结构化成稿工具

若本案涉及公式、正文符号、文本或图片中的公式、OLE或疑似数学对象，读取[来源公式与符号的制作细则](authoring-math.md)中对应的完整章节。先结合来源正文及图像核定，零个OMML不能判定无公式。

`scripts/authoring_toolkit.py`把已经由模型核对的本案内容 JSON 生成 Word，并在保存后重新打开最终 DOCX，独立核对文字、五分区、源 OMML、媒体和定位。普通初稿不再逐案编写`build_draft.py`或客观对象台账代码。源材料理解、合法“所述”、技术条件、公式分类与独立视觉验收仍由原有 Sol/high、Sol/xhigh 流程完成。

本页是可直接操作的接口说明。`rules.json`和[人工定稿规范](manual-format-and-wording.md)优先；本工具并非所有 Word 内容的无损转换器。遇到下面明确不支持的内容应保留原件，使用原有可审计手工生成路径，不删掉内容来迁就 schema。

project_title使用本案核定原名，不能默认拼接一种。规则76起仅权项类别主题/引用头及对应独立方案镜像按新模板核一个一种的句式边界；正式标题、摘要、目的段等原名及重复前缀错误仍核NAME-01/title_reference_prefix。类别名称另据来源核准，普通一种和公式不自动替换。旧冻结版维持原名称契约，详见[名称原样引用](invention-content-standard.md#版本31-名称原样引用)。

摘要正文提交前按[摘要固定话术](writing-standard.md#摘要固定话术)写成“本发明涉及【领域】，公开了【完整名称】。该【客体】包括……”，同时准备技术领域的完整首句。工具保留已核定内容，不自动猜测领域或客体；技术句式和来源仍在原独立复查中核定。

实际案件权项必须恰好十项且编号为1至10，来源不足不得填空项或重复凑数；数量、引用及方案同步见[权利要求必须十项](claim-families-and-benefits.md#权利要求必须十项)。权项引导语固定“其特征在于，”；生成输入即检查普通文本及跨text run，错误标点须改正后再生成，现有最终权项映射检查同时拦截。后续“包括：”不改。正体化不支持的Unicode数学斜体字母、未知字体继承及图片/OLE走既有来源保真处理并查看最终页，不以工具成功代替视觉核定。

权项先按[权项初稿先成形](claim-families-and-benefits.md#权项初稿先成形)组织，已在阶段规范完成则直接复用，不再重复建稿。

规则76起，原内容组织脚本可直接调用同目录`claims_contract.dependent_band(project_title, 权项编号)`取得从属项严格开区间，`visible_count(整项文字)`核工程字数；不另写抽签算法或反复重抽。除版本79完整固定介质第10项外，独立项150＜字数＜300；介质仅在三独权结构、用户完整句式和实际方法引用范围均成立时取得长度例外，来源/执行关系仍须核准。实际源文字优先，不能填词凑长。输入、一次保存后的inspection及最终交付共用该模块，交付只读已封存证据。它检查五种编号结构、字数、单末句号、含混代称和明确公式；图像/复杂符号、计算方式文字、逐字实施例来源和技术手段仍按原六项独立审查，零命中不是语义通过。来源名词中的非英文单符号按原生数学格式保留，不把一个α当完整公式删除；明确公式和计算表达不得放进权项。

## 三个命令

版本63先执行[初稿公式正确性校核](technical-review.md#公式)，再提交已核定的内容JSON；生成器只证明受支持结构及映射，不能证明源式数学正确。唯一有据修正保留原/改式和来源，不支持的结构按既有保真路径处理；未知保留formula_registry的unresolved并按needs_input交接，不把工具成功当初稿语义通过。不新增生成前脚本或独立校核模型。

使用本机文档技能解析出的 Python；脚本依赖`python-docx`、`lxml`、`Pillow`及同目录`inspect_docx.py`。`$docPython`须来自已验证的文档运行环境，不指向任意全局 Python。以下路径仅示意，`--case-root`必须是当前案的独立可写根目录。

```powershell
& $docPython -X utf8 scripts/authoring_toolkit.py inventory --case-root 'E:\案件\case001' --source 'sources/原始材料.docx'
& $docPython -X utf8 scripts/authoring_toolkit.py build --case-root 'E:\案件\case001' --input 'draft-content.json' --out 'draft-v1'
```

- `inventory`只读，输出源文件 SHA256、`word/document.xml`内全部`oMath`的稳定序号/树哈希，以及媒体 part/哈希。`category: null`和导航文本不能原样充作已核定注册项；模型先分类，去掉`linear_text_for_navigation_only`后再放入`formulas`。
- `build`输出`draft.docx`、输入快照`content.json`、现有预检器的`inspection-cache/`及`build_manifest.json`。缓存内实际`inspection.json`、`content.txt`、源媒体与`cache.json`路径由返回值`inspection`和manifest给出。成稿页眉五分区由工具生成，不在正文重复写“说明书摘要”等分区标题。
- 输出默认无页面背景及装饰底纹/高亮；来源公式在显示副本规范化时去除底纹/高亮，再生成显示树SHA，来源原件和技术图片不变。`verify`复用已读XML检查正文、页眉页脚实际底纹；复杂外部表格样式依净稿规范结合最终页面复查。
- `verify`从最终 ZIP 再读一次，与输入快照和来源重新比对；不会修改产物。返回`structural_readback_passed`只代表本工具负责的客观结构通过，`semantic_review`、`visual_review`永远为`not_run`。

`build`成功已包含独立读回，不紧接着再执行同样的`verify`。仅外部修改、恢复或具体完整性疑点时执行`authoring_toolkit.py verify --case-root 本案根目录 --out 产物目录`。需要来源段落、完整OMML或问题定位时直接用`prepared.pipeline.evidence_query`查询已有inspection，不重新解压或逐案写查询脚本；例如`evidence_query.py inspection.json --section paragraphs --start 1 --end 30`，按返回next_offset继续而不重叠读取。`--section formulas --ids F0001`保留完整数学对象；导航结果不表示已核定公式或审查通过。

所有参数中的文件路径、`sources[].file`均相对`case-root`，必须位于该根目录内；绝对路径、`..`越界、解析后指向其他目录的链接拒绝。`case-root`本身由派发上下文提供，不能由另一案拷贝替换。源 DOCX/PNG/JPEG必须带 SHA256，构建前后核对；不修改来源。

`--out`必须是不存在的新目录。先在同层短临时目录生成，再独占建立输出目录并以不覆盖方式提交DOCX/输入快照，随后对最终路径独立读回、只调用一次现有结构预检，manifest最后提交。失败不覆盖旧版本；读回失败或提交中断可能留下没有有效manifest的不完整新目录，不能当成成功，应保留现场并使用新版本目录重做。重复执行不会覆盖已成功结果。

与批次交接：工具返回的`output`才是真实稿件路径，例如`attempt/built/draft.docx`；worker回执`artifact`和哈希必须填这个实际产物，不能照抄预留模板里的`attempt/output.docx`。`build`已执行`run_inspection(final_docx, artifact.parent/inspection-cache, 'draft', True, True)`一次，worker直接引用返回的已验证builder-cache，不再按默认prepared路径重复提取。下一轮handoff可按现有`artifact.parent/inspection-cache`命中。`verify`重新核验cache身份、提取器哈希和所有缓存输出；缓存仍是结构预检，不承载语义通过结论。后续若另外复制/改名DOCX，缓存输入身份会变化，应按新实际路径走原有检查。

## 内容 JSON 结构示例

实际案件的纯文字权项默认在顶层设置`"invention_from_claims":true`。description中把发明内容标题与有益效果引导句紧接排列，中间不重复手写目的/方案；生成器从当前claims插入固定两句及去头后的全部技术段，保留内部所述、条件、续段及跨text run文字。不能和手写方案混用。结构符号/域等不支持对象及真实跨权项技术引用走原来源保真手工路径，不删对象迁就快捷生成；新权项禁止公式，不以手工路径豁免。版本79三独权固定介质第10项按[当前介质话术](claim-families-and-benefits.md#版本79三权第10项介质话术)执行；其实际方法引用在说明书中按既有手工映射写成直接技术叙述，不保留权利要求编号。含此类引用的稿件省略`invention_from_claims`并提供完整手工映射的发明内容，不设false或混用自动/手写方案；无此类引用的普通纯文字稿仍可自动投影，不仅凭类别名称改用手工。详细规则见[invention-content-standard.md](invention-content-standard.md)，本选项不代替来源与独立审查。

以下是**非技术、无公式、无图**的结构节选，不是可直接提交的完整稿。实际案件须将claims补成有来源、连续编号1至10的十项后，再保存为本案根目录`draft-content.json`构建；规则75起不足十项会被拒绝。示例文字须替换为已核对内容，登记来源/保护名称/对象，不允许把示例套话当成技术来源。无图仍保留两个空的图分区，五种逻辑分区不省略；这可能产生空图页，应由本案规则及最终视觉复核决定是否另走可覆盖的版式路径。

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

工具保留权项多自然段，设置首行2字符（560twip）、左对齐、单倍240、312twip行网格，不生成或补齐“所述”。每项至少3处合法“所述”的一般要求继续；仅版本79三独权第10项完整符合[固定介质话术及实际方法范围](claim-families-and-benefits.md#版本79三权第10项介质话术)时保留两处合法所述并免于一般独立项长度。其他项及其他第三类别不取得例外；逐字实施例特征来源优先，不为配额改源或补造技术内容，无法兼容记具体缺口。各自适用的数量及真实引用都是语义门禁，字符串计数不能由manifest签通过。

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

工具将附图说明输出为图N为本发明的完整图名；版本32图名末尾缺图时只补一个图，已有流程框图、示意图等结尾不重复，非末条分号、末条句号，使用原黑字正文格式。图名字段不带句末标点，重复图或含句末标点明确拒绝，修正输入后生成；不修改原注册title、图号和媒体。仅文末figures的图片后另起相邻段“图N”，版本80图片段及图号段的keepNext/keepLines/pageBreakBefore均为0，实际同页由最终页面核验；abstract_figure复用图像但不生成caption。末尾图片本体不插入实施方式；实施方式用`figure_ref`引用并写核实后的解读。自由文字内出现未登记图号会拒绝；复杂编号、全文所有隐式引用、图内文字和新旧图号语义对应仍需审查。`same_page`明确记`requires_rendered_page_review`，控制属性和高度预估不冒充真实同页证明。

版本42导入流程图前，按technical-review.md核定逻辑并优化交叉/重叠/穿字穿框的布局；生成器复制媒体不等于连线布局合格，不从图片哈希或导入成功自动判通过。将当前attempt的修正图走既有独立图片来源路径，保持原图身份和来源映射，同步摘要/文末复用；原figures和rendered_pages实际检查完整最终图及当前Word页。仅布局修改保留核定节点/边/条件/回路，必要同图成对续接记原边映射；不改工具契约、不新增图审阶段。

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


版本80在原自然分页流程中关闭全部角色及页眉页脚/文本框的keepLines/keepNext/pageBreakBefore，body的widowControl仍为0；标题衔接、完整公式及图片与图下号同页仍由最终页面核验，不再通过开启keep属性实现。分节直接附在本节最后内容段；空逻辑节才保留最小1pt结构载体。没有减少或拆分技术文字、重排公式节点，也没有改变页脚定位空段。摘要图的客观记录caption_locator/caption_text为null，文末图仍有图号定位。读回拒绝任何段落重新启用三种分页黑方块属性、摘要多出图号、额外满行高分节载体；后续验收复用page_flow_evidence，实际页面仍须查看。


## 版本38文末图外文字

figures分区只允许figure块，输出图像和单独居中的图N；不接受单位说明、注释或其他paragraph。保存读回同时拒绝图号段内br/cr/tab、域、自动编号、公式和其他对象，不能把看似图N的混排段当作合格图号。修订稿最终通过number_only_v38封存报告检查。源图内坐标、单位、图例和节点保持，正文figure_description继续写完整图名；abstract_figure仍无外置编号。工具不自动裁剪图片，不新增解释，最终页仍须人工标准复查。
