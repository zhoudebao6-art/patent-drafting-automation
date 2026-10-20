# 专利撰写自动化

v6.50：按用户提供的权利要求模板统一五种结构、实施例逐字来源、完整术语、严格字数区间、单末句号及禁止公式/计算方式等要求，替换冲突的旧介质固定短句。规则仍为65条，共同要求并入原规则；生成、同次预检及交付复用同一检查逻辑，不增加模型阶段或第二遍DOCX扫描。更新前已完整备份v6.49，旧运行批次继续原快照。6/3/3三池、六项独立验收及逐案交付保留。

## 架构与阅读入口

|职责|入口|何时读取|
|---|---|---|
|识别办案或技能维护，分配阶段职责|[SKILL.md](SKILL.md)|开始任务时|
|规则与写作要求|[规则库](references/rules.json)及SKILL内的规范索引|worker读取本批冻结阶段包，细节按需查|
|三池调度、收稿和逐案交付|[批处理流程](references/batch-workflow.md)、[CLI池](references/cli-workers.md)|主控执行及恢复时|
|提取、生成、渲染和回执|[提效执行索引](references/efficiency.md)|按当前阶段调用既有工具|
|版本历史与旧验证记录|[CHANGELOG.md](CHANGELOG.md)|追溯版本时，普通办案不必读|

运行主线为来源准备→初稿→独立复查→必要修改后独立重验→逐案交付。当前来源、稿件与最终页面仍须完整覆盖六项验收；已验证且未受修改影响的范围按原证据继承。通过即交付，完成即结束本案专属进程；各案独立，其他在途任务继续。

## 安装与更新

从本仓库[最新发布](https://github.com/zhoudebao6-art/patent-drafting-automation/releases/latest)下载该发布页中的`patent-drafting-automation-v版本号.zip`，解压后将整个`patent-drafting-automation`文件夹放入`%USERPROFILE%\.codex\skills`；设置了自定义CODEX_HOME时使用其`skills`目录。更新时先把旧技能目录移到skills目录之外备份，再放入新版，避免同时发现两份同名技能；工作批次及其skill-snapshot不要移动或覆盖。

新电脑或软件升级后，先让Codex按[运行环境](references/runtime.md)探测本机Python、文档工具、WPS/LibreOffice、Poppler和字体，更新本机runtime.json并完成预检及首次样稿页面验证。包内runtime.json是原机器的配置样例，不能把其中路径或程序SHA当作新电脑已验证的环境；安装包不含这些外部程序。已有正确环境无需重复安装依赖。

重新打开Codex会话后可使用下方指令。新版用于以后新建批次，已运行批次继续自己的冻结版本。GitHub发布更新不会自动更新其他电脑的本地技能，需要在对应电脑安装新版。发布ZIP保留运行脚本、测试、完整现行规则/规范、必要来源证据与历史模板，历史规则备份目录留在Git中，不加入运行包。

## 使用

```text
请使用 $patent-drafting-automation，并行处理“D:\待撰写项目”中的技术项目优化书，按可用名额持续补位，每案独立复查和修改，每案Word复查通过后立即直接放到桌面同一批次文件夹根目录，不按项目建子文件夹，不等全部项目完成。
```

每完成并验收通过一案，总控立即交付并提供文件链接；后续成稿直接追加到同一目录根部。最终目录内直接排列“项目甲.docx”“项目乙.docx”等成稿。审阅台账、中间稿及待处理说明留在工作目录。不同项目可以同时处于撰写、复查、修正阶段，不再等待前一项目全部验收。每案内部保持有序且只允许一个活跃阶段；独立case/attempt目录和哈希绑定防止错案、错稿、旧回执混用。

默认目标为6个初稿、3个只读复查、3个独立修改worker，最多24案在制。控制器显式填写实际可用--workers；三池共用总容量、各池不超限。初检及修改后独立重验共用3个review名额，修改单独revise；一案通过立即交付。实际容量受账户及机器限制，没有CLI时按实际子代理名额执行，只有1个名额如实顺序。旧冻结批次继续原调度，具体见[三池执行](references/three-stage-pools.md)。

## 文件与维护

- [SKILL.md](SKILL.md)：技能入口。
- [本轮提速机制](references/fast-execution.md)、[公共Word生成器](references/authoring-toolkit.md)：单份执行清单、原式和图片导入、一次结构检查及工具冻结。
- [多CLI执行与恢复](references/cli-workers.md)：实际启动、会话证据、失败隔离和恢复。
- [并行流程](references/batch-workflow.md)、[策略](references/batch-policy.json)：占槽、派发、补位、取消、验收及交付。
- [规则库](references/rules.json)、[规则维护](references/rule-maintenance.md)：话术、范围、来源和版本记录。
- [写作标准](references/writing-standard.md)、[技术校核](references/technical-review.md)、[所述与公式规范](references/terminology-and-formula.md)：完整名词的前置、源公式数量、完整符号、无空白及图文要求。
- [历史格式证据](references/terminology-formula-evidence.md)：三组定稿的Cambria Math/14磅/正体、主公式映射和不再继承的个别缺陷。
- [正确稿净稿与图序](references/reference-style-standard.md)：不加红字解释，术语回指依章节句法核对，图片与图下号同页。
- [人工定稿格式与分范围用词](references/manual-format-and-wording.md)：权项分段、合法所述与来源优先边界、页眉页脚参数、孤立变量及名称保护；新增案例正文不随公开技能发布。
- [公式结构与版式](references/formula-layout-standard.md)：显示续行与合法条件数组的区别、空基底/空限制、角色样式、逐式显示验收与实际反例。
- [运行环境](references/runtime.md)：Windows Word页面转换及哈希验证。
- [案例资料](references/corpus)、[来源清单](references/source-manifest.json)：三组历史参考项目。

已有serial_per_project批次使用旧batch_tool.py及其冻结规则恢复，不被升级自动迁移。新批次使用parallel_batch.py。直接说“更新技能”即按长期通用规则保存，后续项目默认使用，无需再强调“长期”；仅本项目的例外请明确说明。

效率优化包括事件完成即补位、reserve直接交接缓存实际路径、公共生成器替代每案临时排版程序、一次结构预检供独立复查使用、原文规则清单替代多份重复阅读、复用有效同稿页面。reserve已包含调度，不再每次先plan；按等待时间推进复查并限制草稿积压。新批次命令自动使用本批冻结脚本，恢复保留原版本。最终六项复查模型审查及全部页面验收保留，不承诺绝对零错误或未经测量的加速比例。

## 验证与环境

runtime.json为本机已验证工具路径；当前文档插件26.1013.11914。换电脑或升级后需重新探测和实际渲染，ZIP不包含Python、LibreOffice或Poppler程序。三组历史参考文件未被宣称为全页视觉验收通过。

当前维护验证见对应版本记录；历史测试数量不代表本版或新案件已通过。每小时十件合格终稿是目标，须用真实完整60分钟窗口统计，不以局部工具优化代替产能实测。

## 只提供文件夹地址

实际办案时，直接发送案件文件夹完整路径即可；技能递归查找技术项目优化书，逐案撰写、独立复查及修改，合格终稿陆续放到桌面同一个批次文件夹。

如果消息是在规定“以后按这种地址办理”、举例或要求通用修复，则本轮只维护技能，不拿示例目录开工。确需同时写该批时，在同条明确“现在开始处理这个目录”即可，不增加确认步骤。
