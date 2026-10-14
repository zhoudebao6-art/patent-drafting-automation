# 默认提效流程

用户已授权省略不必要流程并直接执行。本流程适用于后续所有新批次；不改动已运行批次的冻结规则。模型、来源边界、六项最终验收、同案隔离和逐案即时交付保持。提效优先减少重复解析、重复转换、无关阅读和长回传，不靠降低模型强度。

## 实际执行顺序

1. 每批核对运行环境一次。沿用已验证的工具配置；只有首次配置、依赖改变或转换失败时才做独立小样。普通批次不重跑技能单元测试、不核验全部历史语料、不重读三组定稿全文，也不检查无关仓库、GitHub或全局配置。
2. 新批次优先使用[CLI池](cli-workers.md)连续填满实际可用空位并自动收稿。手工通道的队列建立后直接`reserve --prepare`并派发首案，不在每次reserve前另调用plan；reserve内部已调度和验证，并把准备好的缓存实际路径传给worker。worker忙碌时，总控可用本地工具预提取下一案的来源，写到该案独立source-cache，节省下一个worker等待时间；不要为预提取额外占一个模型名额。只预备即将开始的案，不先处理完整批资料再开始撰写。
3. Sol/high读取本案来源提取、冻结规则和必要的写作规范，完成初稿与一次结构预检。只做生成稿所需的检查，不模拟重复的Astra独立复查；默认不先渲染明显还需技术修订的初稿。记录可合并为一份evidence.json，各字段用来源定位引用，避免重复抄录整段正文；来源、术语、公式和图的必要对应关系仍须保留。
4. 新批次review_mode=review_fix_then_recheck：Astra/high先集中检查来源支持、所述、权项联动、公式和附图内容。有明确依据可修正的问题时，在同一次review调用内做一次修正，另存当前attempt的新DOCX，回执verdict=revised并记录改前哈希、新稿哈希及非空findings，每项含status=resolved或closed、location和correction_evidence；该回执只能变为revised，不能验收或交付，随后必须派发新的Astra/high独立重验。这样一次修正从review→revise→review的三次Astra派发减为review内修正→新review的两次。技术事实不足时needs_input；不适合本次内修正时可返回fix_required走原独立revise路径。内容仍待修改时rendered_pages如实记not_run，不转换待改稿。无内容阻断时，才生成或复用当前页面并查看全部最终页；pass六项必须全部passed。
5. Astra/high在本次review内或独立revise阶段按具体问题及关联章节修改，避免无理由重写全文。修订稿先做结构预检，下一位Astra/high独立重验；先处理内容，再对最终版本渲染和逐页验收。已有效的来源提取、定位表和同字节页面可复用，审查结论不自动继承。页面发现的问题由Astra修改，最终字节变化后重新渲染。
6. pass后立即交付到桌面同一文件夹根目录。总控验证最终哈希与回执，不再启动第二次内容复查或重复复制。无问题就结束该案，继续下一案；不能为了证明做过检查反复跑同样步骤。

## 来源提取缓存

总控在本案cases/<id>/source-cache准备一次，各worker只读复用返回路径。worker需要补充提取时，只能在自己的attempt目录建立缓存，不改其他attempt的产物。通常直接提取媒体，避免下一阶段重新打开ZIP抽图；纯文本来源可省略媒体选项。相同来源和选项在各阶段保持一致。

### 直接交接，避免每阶段重新查找

默认`reserve --prepare`使用同案固定source-cache，统一proposal与extract-media=true，对每份DOCX来源准备或验证一次。非DOCX直接返回原件位置待按其格式读取，不伪造提取成功。返回的prepared应作为模型message的结构化资料，包含：

- `rules_active`：冻结库所有active条目，保留scope、例外、检查方式和证据；省去history与非active条目。原rules.snapshot.json及哈希保持不变，项目范围仍须核对。
- `source_inspections`：已经核验的inspection、content、媒体实际位置和精简导航index；命中时直接读这些文件，不在新attempt重跑一次--reuse。不同缓存根不会自动互通。
- `input_inspection`：优先只读复用当前稿所属写稿attempt的`inspection-cache`；不存在或无效时只在本轮attempt新建。Sol或Astra写完新稿后，以`prepared.draft_inspection_cache`作为一次结构预检的--out，并用`--kind draft --extract-media --reuse`，下一轮即可直接复用。
- `render_candidates`：只列当前输入稿所属attempt、匹配同路径及稿件SHA的常规render*目录；只是候选，必须现有verify验证PDF/全部PNG，随后独立实际看图。不扫描别案/整批，也不把任意旧review页面搬过来绕过位置约束。已知失真或失败的后端记录不能当合格页；兼容重渲染仍须保留。

`prepared/handoff.json`及精简规则/导航在派发绑定时核对哈希，同时复验交接缓存的本案目录、完整身份、cache.json原哈希及全部产物哈希，防止准备后损坏或连元数据一起被改写；这一检查不重新解析DOCX。损坏ZIP或XML按队列拒绝返回，不提交占槽。缓存可复用的是数据，不是旧审阅者的通过结论。新增准备由总控执行，worker仍只写自己的attempt。没有--prepare的旧调用保持可用；旧批次规则不因新工具自动升级。

缓存目录使用短键和短generation名以降低Windows深层路径失败；cache.json仍核对完整源/工具SHA及全部文件哈希，短目录名相同也不能混用另一身份。工具更新应重建该案缓存一次，不将版本失效后的必要重提取称为冗余。

```text
python scripts/inspect_docx.py 本案来源快照.docx --out 本案/source-cache --kind proposal --extract-media --reuse
```

返回inspection、content、output_directory的实际路径及cache_status。--out在reuse模式是缓存根目录，不能假定报告就在根部。缓存键包含来源绝对路径与SHA256、提取器SHA256、kind和媒体选项；命中时校验报告、文本及全部抽出媒体的哈希，热命中不再次解析ZIP。源字节、工具、选项改变时生成新键；证据丢失或损坏时保留原代并另建generation。路径及哈希相同仅说明结构证据可复用，不能代替来源语义审查。禁止跨案共享一个可写cache目录。

## 最终页面复用

```text
python scripts/render_docx_checked.py 当前稿.docx --output-dir 本次审查/render --reuse --compact
```

目录不存在时真实转换；已存在时核对同一稿件绝对路径和SHA、配置/工具/DPI指纹、PDF与全部连续PNG。命中不再启动LibreOffice，render.json原字节保持不变。源稿或工具不匹配时拒绝覆盖，使用新的独立输出目录重新转换。已有历史证据仍可用--verify核验，但没有新指纹的证据不会被--reuse视为命中。不同项目或稿件路径不互相借用页面。review内修订仍须新的独立review；不能以页面缓存命中取消该次验收。

渲染证据仅为rendered_pending_visual_review，必须由Astra/high实际查看每一页。渲染默认放当前review attempt，也可使用同一当前稿的写稿attempt内已验证页面；不能引用别案或任意旧review目录。不得为了节约时间减少最终页面数、降低DPI或跳过公式、图号及分页检查。

## 简短回执与按需检查

bind、record、cancel、defer优先加--compact，只缩短工具返回，不删除manifest、dispatch或receipt中的完整证据。用户侧主要报告完成、实际错误和待补缺口；普通步骤不输出大段规则和完整历史。规则JSON按status筛选active条目，读取适用指令/check/evidence，不向每个worker重复输出history和rule-backups；完整冻结库仍保留供追溯。先看规则中的active条目，按本阶段阅读规范；历史案例正文、数学对象细节仅在有相关问题时加载。

```text
python scripts/parallel_batch.py record --manifest 批次清单 --case 案号 --attempt attempt_id --receipt 本轮/receipt.json --controller 控制器 --compact
```

没有公式或附图仍核验来源确实没有，并检查稿件未新增；不制造空表或重复跑与该案无关的公式/图像计算。每次技能维护按实际改动选必要回归；文案更新不跑整套交付和渲染测试，缓存改动验证命中、失效、篡改和对应旧行为。只有调度、验收契约或跨模块依赖变化才扩大测试，全部通过后停止重复测试。

## 容量与可验证收益

2026-10-14实际批次只读审计发现，21份来源报告对应8个源SHA，但包含证据复制与提取器升级；33份render证据对应20种同案稿件SHA，13个额外位置包含7份相同证据复制、5次LO→WPS切换、1次失败修复重试。没有证明同稿同后端重复转换，不能按文件夹数量夸大可省次数。本轮优化的是已确认缺失的缓存路径交接、规则历史与大报告的默认读取、重复plan及台账重抄。来源完整性校验、独立语义审查和最终改稿后的页面验收保留。

实际worker数由工具决定，配置target_workers不能增加名额。新批次已获用户授权使用独立CLI，按cli-workers.md验证真实容量，不能把app子代理槽数当成CLI全部容量。只有一个worker时模型阶段按序，主控可并行做下一案的本地预提取；有多个名额时持续补位不同案件。不要增加用户聊天或修改全局设置绕过额度。CLI采用单总控、唯一attempt锁及启动意图；恢复先收稿，失败保留证据，不重放已完成模型阶段。reserve按状态先排序、遇首个有效候选即停止核验，正常收稿只在record执行完整证据门禁；bind、record、deliver跨边界验证仍保留。记录cache_status、渲染是否启动和必要的阶段起止时间即可；只有实际测量才报告加速，不把局部缓存计时等同整批文稿耗时。
