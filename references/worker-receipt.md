# 提交前回执检查

## v6.26 增量复查

新批次冻结`review_reuse=verified_scope_v1`。首轮完整检查并集中汇总能确认的问题，写清已通过与未完成范围；后轮先用下方summary命令读取控制器差异摘要，不整读包含逐段哈希的内部计划。只复查修正项、改动、新增、关联内容、此前失败或未审范围，已有证据且未受影响的通过部分不重复检查。缺证据不能由没有finding推断通过。仍由独立review判断变更是否影响其他权项、技术方案、术语前置、公式解释、流程图和引用；受影响范围须从继承移入本轮检查，不仅看段落字节。

```text
python 本批skill-snapshot/scripts/incremental_review.py summary --plan prepared.review_reuse.path --plan-sha prepared.review_reuse.plan_sha256
```

在原decisions/receipt添加`review_coverage`，不另建台账：

```json
{
  "schema_version": 1,
  "plan_sha256": "prepared.review_reuse.plan_sha256",
  "checks": {
    "source_support": {"fresh": ["*"], "passed": ["*"], "inherited": [], "impact_review": "本轮实际判断及依据"}
  },
  "pages": [{"page": 1, "status": "passed", "mode": "fresh"}]
}
```

`checks`列出source_support、antecedents、claims_alignment、formulas、figures五类；原回执六项checks保持不变，rendered_pages覆盖只由pages表达，不写第二份。scope使用计划的部件名及段号，不自造。fresh表示本轮实际检查，passed仅为fresh中的通过部分；inherited只填计划允许且本轮确认未受影响的已过范围。整类已实际检查可用fresh=`["*"]`；仅整类通过才用passed=`["*"]`，整类确实可继承才用inherited=`["*"]`。集合支持`{"all_except":["明确排除的scope"]}`，减少几百个已过段号的重复抄写，例如检查全类仅两处失败时，fresh用`["*"]`、passed明确排除失败及未审范围。补集同样是实际覆盖承诺，不能由finding自动填出。未完成项允许空覆盖，原checks写not_run或具体失败。

页面仍从当前稿真实渲染生成。需要继承旧视觉结论时调用统一比对工具：

```text
python 本批skill-snapshot/scripts/incremental_review.py page-map --plan prepared.review_reuse.path --plan-sha prepared.review_reuse.plan_sha256 --render 当前render.json
```

工具只给候选，不生成passed。仅前轮真正看过且通过、当前图像相同且证据链有效的页可填mode=inherited；其余页实际查看后填fresh及真实status。当前全部页均须覆盖；未渲染则pages=[]且rendered_pages=not_run，不提前渲染已知待改稿。沿用当前render.evidence及当前稿SHA，不挪用旧render.json。缺少旧覆盖证据、来源/规则/工具变化或不明影响时，按计划扩大本轮检查；不增加另一次全检或修改者自签。旧冻结批次保持原契约。

v6.19识别三池独立准备的合法pending到ready转换：原始reservation.json字节及哈希仍保持原样，只有prepared/handoff.json的独立文件身份、封存哈希和本attempt上下文均一致时，才允许准备状态与准备封存两个字段产生预期差异。其他字段仍逐项严格比较，未知字段、来源/身份/封存变化不放行。修复用于以后新建批次；已授权任务内的必要接续由总控按[工具修复接续](tool-fault-recovery.md)验证后执行，无须重复批准，旧冻结工具及旧失败记录不改写。

仅 `execution_optimization=lean_execution_v24` 的新冻结批次使用。工具只装配客观字段、检查结构，不签发语义或视觉通过，不修改队列，也不替总控核验真实CLI最终回复。

在当前attempt中写 `decisions.json`，填写实际完成的 `verdict`、输出 `artifact` 绝对路径，以及复查实际执行或有效继承的 `checks`、实际问题 `findings`。通过复查时提供 `render.evidence` 的实际 `render.json` 绝对路径。需要的六项为 `source_support`、`antecedents`、`claims_alignment`、`formulas`、`figures`、`rendered_pages`；未执行写 `not_run`，有问题如实记录，不复制模板的passed。

版本28的figures技术审查范围只含流程图，另保留全图原有身份/图号/位置/显示检查。非流程图技术项在现有图台账中写not_in_scope，不能把它填为checks.figures的新枚举；无流程图则在证据说明该子项不适用，原基本检查实际完成后才passed。流程图错误需据本案内容修复，未完成或来源不明不得pass；review_only由review返回fix_required、revise修正后返回complete，再由新review独立重验；仅旧冻结策略允许review内修稿返回revised。

```text
python 本批skill-snapshot/scripts/worker_receipt.py --job 本attempt/cli-job.json --decisions 本attempt/decisions.json
```

工具从本attempt已绑定的job、reservation和dispatch取得身份，计算真实DOCX和render证据SHA。它不会猜测verdict、设置passed、关闭findings或把修改稿改为pass。只补缺失的客观字段；已填写的身份或SHA若矛盾则拒绝。可用 `--output 本attempt/receipt-candidate.json` 保存新候选文件，不能覆盖已有文件或写 `receipt.json`、`worker-final.json`。

成功时把标准输出中的完整JSON原样作为最终回复。失败时在本worker原轮次内修正具体字段或真实产物再检查；不重新启动本案CLI，不重写已正确内容。最终CLI日志、会话、模型强度、来源、六项审查、最终页面及业务验收仍由原收稿门禁执行。

普通初稿或修订使用 `complete`。review未修改且六项和全部当前页由本轮实查与有效继承完整覆盖并通过才用 `pass`；发现问题未改完用 `fix_required`；仅旧冻结review_fix_then_recheck下review已修改稿用 `revised` 并给出当前attempt的新DOCX和有真实 `location`、`correction_evidence` 的已关闭findings，之后交由新会话独立重验。来源不足用 `needs_input` 并给出具体reason；不能用回执工具解决技术证据缺口。

收稿只在真实执行、来源和身份验证后，把明确的字段缺失、错误枚举、同案过期路径归属等 `CaseReceiptError` 隔离为本案失败，保存 `cli-rejection.json` 及原产物，不自动重试；其他案件继续补位。来源或身份变化、SHA矛盾、跨案路径、链接、未知执行错误和无法确认退出均继续停池或保留占槽。分类依赖明确异常类型，不按错误消息的关键词猜测。

版本30行内公式候选仍在原findings中闭环。对带offset的候选必须逐项携带相同offset、end_offset、offset_projection及format_disposition=not_formula或format_already_correct，并保留原双SHA和精确来源/目标定位；只能说明误报或格式已正确，不能豁免真实错误。修改后使用新稿的inspection与SHA；组内缺逗号必须修复，不能以来源身份关闭。
