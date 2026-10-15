# 提交前回执检查

仅 `execution_optimization=lean_execution_v24` 的新冻结批次使用。工具只装配客观字段、检查结构，不签发语义或视觉通过，不修改队列，也不替总控核验真实CLI最终回复。

在当前attempt中写 `decisions.json`，填写实际完成的 `verdict`、输出 `artifact` 绝对路径，以及复查实际执行的 `checks`、`findings`。通过复查时提供 `render.evidence` 的实际 `render.json` 绝对路径。需要的六项为 `source_support`、`antecedents`、`claims_alignment`、`formulas`、`figures`、`rendered_pages`；未执行写 `not_run`，有问题如实记录，不复制模板的passed。

```text
python 本批skill-snapshot/scripts/worker_receipt.py --job 本attempt/cli-job.json --decisions 本attempt/decisions.json
```

工具从本attempt已绑定的job、reservation和dispatch取得身份，计算真实DOCX和render证据SHA。它不会猜测verdict、设置passed、关闭findings或把修改稿改为pass。只补缺失的客观字段；已填写的身份或SHA若矛盾则拒绝。可用 `--output 本attempt/receipt-candidate.json` 保存新候选文件，不能覆盖已有文件或写 `receipt.json`、`worker-final.json`。

成功时把标准输出中的完整JSON原样作为最终回复。失败时在本worker原轮次内修正具体字段或真实产物再检查；不重新启动本案CLI，不重写已正确内容。最终CLI日志、会话、模型强度、来源、六项审查、最终页面及业务验收仍由原收稿门禁执行。

普通初稿或修订使用 `complete`。review未修改且六项和全部最终页面已实际检查通过才用 `pass`；发现问题未改完用 `fix_required`；review已修改稿用 `revised` 并给出当前attempt的新DOCX和有真实 `location`、`correction_evidence` 的已关闭findings，之后交由新会话独立重验。来源不足用 `needs_input` 并给出具体reason；不能用回执工具解决技术证据缺口。

收稿只在真实执行、来源和身份验证后，把明确的字段缺失、错误枚举、同案过期路径归属等 `CaseReceiptError` 隔离为本案失败，保存 `cli-rejection.json` 及原产物，不自动重试；其他案件继续补位。来源或身份变化、SHA矛盾、跨案路径、链接、未知执行错误和无法确认退出均继续停池或保留占槽。分类依赖明确异常类型，不按错误消息的关键词猜测。
