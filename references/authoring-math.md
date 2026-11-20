# 来源公式与符号的制作细则

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
5. 对真正公式和结构记号深复制完整数学子树，保留有义字体族和粗体、上下标、数组和运算顺序；版本66在显示副本统一正体，`m:sty`的i→p、bi→b并关闭w:i/w:iCs，原树和源SHA保持；字面空格须按版本26先核定并修正，不能盲删。不设置全局`m:sty=p`，不把ℬ改成B；显式single_line按本页单行接口处理已核定的外层显示续行或集合构造式内部续行，其他嵌套数学结构保留。独立显示在居中无首缩进段内，单式在对象外补普通中文分号，eqArr多行组在末行补显示分号，另存显示树SHA并保留原数学节点。源末行最后run已带分号则不重复，内部括号/下标分号不视为段末。多来源的`mathPr`必须一致，原样带入。
6. 观测到依赖外部字符样式、段落样式/段落字体、主题或关系ID、修订的OMML会明确失败，不悄悄丢掉依赖；源数学文字显式非黑色也拒绝，不静默改色或产出红色净稿。源对象未显式指定的字体/大小仍需结合来源继承与最终显示核对；该工具不声称完全复现任意源文档的字体解析环境。默认不改原OMML字号；明确single_line且核定source_base_pt后，才按比例缩放显式字号、补全局部字号。模板普通基准14pt，具体方法见下节，最终页面仍须核定。

版本62结构回读另在同一结果的source_formula_aliases保留每个重复源项的原文件/SHA/位置/树SHA、duplicate_of、依据和唯一目标出现；原源文件不改，不用删除登记来降低N。工具的完整关系指纹只是保守支持范围，未命中不证明无重复；全文仍用源唯一清单逐条核查。

主式、符号、类别及正文解释仍按`formula_registry`要求语义复核。树SHA是忽略XML前缀/属性次序及元素间缩进空白的结构哈希；保留数学文本（包括字面空白）、每个属性、节点次序与所有子结构，不等于原始XML字节哈希。

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

equation_count必须为1。source_base_pt须由原式显式属性和继承核定；font_size_pt只能为9至14的半磅步进，且不大于源基准。先从14pt开始，仅为放入实际列宽局部减小，选仍清楚的最大字号。工具支持只有一个外层等号的完整单式，或纯顶层eqArr显示续行：第一行可停在等号，也可已有右式，后续加减项按原序复制。未知数组属性、分段条件、多条完整等式及未选中的嵌套强制断点明确拒绝；只有已核定显示壳和直接数学run的对齐/显示断点可移除，嵌套矩阵、上下限、分式和数学子树不重建。集合构造式内部续行使用下述专门分类。

版本71集合定义式使用同一formula块及single_line对象，把classification设为set_builder，仍提供source_base_pt、font_size_pt且equation_count=1。先由模型按来源确认集合成员与筛选条件连续；工具仅支持外层一个等号、右式为唯一成对{}的m:d、其唯一m:e内单个eqArr，首行具有成员关系∈及筛选分隔符|或∣。每个行间边界必须已有上一行末的冒号/∧/∨或下一行首的∧/∨，不替无连接的独立条件补连接符。仅移除该数组显示壳，成对括号及所有行内完整子树原序保留，未知壳属性或结构拒绝；分段函数、矩阵及真正多行限制不走此分类。不满足该工具形态但已核定需要单行的集合式按既有保真路径处理，不用改写源式迎合工具。同次inspection提供needs_review位置提示，不能以候选或工具成功代替语义和最终页面检查。

字号只写入该式的数学run/控制属性、段落标记和同段收尾分号；显式原字号按font_size_pt/source_base_pt缩放，半磅取整，缺失字号用局部基准或同run已有字号补齐。字体族、粗体、花体、帽号、完整上下标和节点次序保持，正文及符号解释不缩小。原source_tree_sha256继续绑定未改源，display_tree_sha256绑定实际输出，source_objects记录single_line及主式数量；保存后读回逐树核对，并校验段落及分号字号。不能以改显示树为由改源内容或凑公式数。

同次inspect_docx提供single_equation_continuation_hits，已识别的单式显示续行须改稿，不能用来源身份豁免；缓存绑定新增helper哈希。此检查不是自动视觉适配器，不检测所有自动折行或跨段续行。最终仍在原formulas/rendered_pages核对同一DOCX的公式及整页：一行、可读、无越界或裁切、分号同排。工具边界之外继续原保真修稿流程，不能强行展开矩阵、盲缩到小字、隐藏溢出或宣布通过。

## 版本39完整函数量和符号解释覆盖

source_symbol可用node_path指向完整m:func，或用相邻node_paths同时选择函数头及完整m:d参数，例如头路径与参数路径；实际上下标、帽号及全部参数结构保留，仍记录complete_symbol_copy及原片段路径，不增加主公式数量。只取函数头、只取参数括号、截掉完整量的修饰都会拒绝。函数参数中的完整输入量可另作source_symbol解释，不能据此宣称解释了整个调用；source_operator的name(·)仍只用于算子本体。源结构歧义如字母乘括号表达式，按本案语义走原保真路径，不猜函数。

普通symbol_reference如果携带formula_id，只允许与该条symbol_reference类别原裸量的完整字符、结构和有义字形一致；不能把带下标/帽号/向量的量或完整主式绑定到一个字母。未绑定普通i等索引仍可输出，但不覆盖完整量。工具不自动穷尽所有数学符号或判断解释含义；按formula-detail-standard.md版本39在既有formula_registry.symbols逐项关联实际文中解释，原formulas检查缺失/截短/未知含义不能通过。

版本41/67说明文字由写稿/修稿阶段按实际身份去重：同身份同义同域的完整符号只在当前稿首次出现它的公式后解释，后式只写新符号且共用首式解释定位，必要局部条件留在适用处。每式解释覆盖仍包含全部独立需定义的符号；通常含义的熟知运算符不列必解释项，运算本身仍核正确性，边界见[熟知运算符号不另作解释](formula-detail-standard.md#熟知运算符号不另作解释)。源对象ID不同不等于数学身份不同；生成器不凭字符串或XML自动删除解释。同一算子的完整source_operator记号及含义已经解释时，不再额外输出裸名为算子名称等重复text/symbol_reference说明。保留完整数学记号；前句独有技术信息合并至完整解释，独立变量/索引、有独立含义的具体调用量与算子本体、同名异义仍按原角色；不为已知运算和输入构成的每层组合输出重复解释，完整边界依formula-detail-standard.md版本39。生成器仅保真输出核定内容，不按字符串自动删除；复用原formulas及symbols关联，不改变原公式复制或主式数量。

### v6.15 来源绑定符号保真

与[正文符号与来源绑定](#正文符号与来源绑定)为同一契约，按该节执行，不重复读取；完整结构及源/显示树核对不变。

### v6.16 转置与英文记号检查

本工具不自动判断T的数学角色，也不自动翻译中文或生成新变量。源OMML含中文时停止直接导入并提示按原保真路径核定英文符号映射；保留原文件SHA、raw_formula/原树，记录normalized_formula/规范树及逐处映射，不将规范稿冒充原技术来源。受影响部分归一化后，正文解释复用同一完整规范符号，所有公式出现一致。

同次inspect_docx在既有formula_prose_evidence.review_candidates定位math_cjk_requires_english_symbol与superscript_T_requires_role_review，分别记录数学节点内偏移及精确math_path。output_contract对前者无source_math_identity/not_formula豁免；后者只有源支持非转置且format_disposition=not_matrix_transpose、object_id和math_path精确匹配才可关闭。真实转置改成上标⊺后重新提取；一个数学对象内多个T逐槽核对。普通正文、图内非公式文字及字体名不扫描为数学中文；图片/OLE/文本数学仍在原六项检查内核对。归一化不增加完整主公式数量，不新增模型阶段或扫描通道。

### v6.17 完整解释的语义边界

source_symbol已支持完整上下标结构和带实际参数的函数，拒绝截取结构内部基字或漏掉相邻参数；该保真能力不能证明该完整对象在文中已被解释。版本45不再把星号、裸E、裸∇等部件说明当作Δθ*、E_b(θ)、∇_θ的完整覆盖。解释引用完整规范片段，索引和功能标识的实际记号及含义由原formulas据源核对；s.t.等平面标点串若不在通用片段支持范围，使用既有保真表示，不修改源符号来迁就工具。不新增数学解析器、运行扫描、字段或阶段；同身份同义同域对象共用首式解释定位，后式不重复定义；非全部表达式都另造定义。
