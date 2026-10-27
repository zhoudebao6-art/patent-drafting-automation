# 在另一台电脑安装 v6.58

本版是 patent-drafting-automation v6.58，规则84，共65条。完整技能包含规则、运行脚本、测试和既有格式参考；不包含Python、Office、Poppler等外部程序，也不包含原电脑正在处理的案件。

## 推荐：交给新电脑上的Codex安装

复制下面整段到新电脑的Codex：

```text
请从 https://github.com/zhoudebao6-art/patent-drafting-automation/releases/tag/v6.58 下载并安装完整的 patent-drafting-automation v6.58 技能，核对发布页的 SHA256SUMS.txt。已有同名技能时先完整备份到技能目录之外，不修改任何在途项目的冻结副本。按技能 references/runtime.md 检测并配置本机文档运行环境，核验实际程序、路径与字体，完成环境预检和首次样稿页面检查；缺少的依赖明确报告，不套用其他电脑路径，不把预检成功当作视觉验收。本次只安装与验证，不开始撰写案件。
```

## 手动放置技能文件

1. 打开[本版发布页](https://github.com/zhoudebao6-art/patent-drafting-automation/releases/tag/v6.58)，下载 `patent-drafting-automation-v6.58.zip` 和 `SHA256SUMS.txt`。
2. 解压后，将其中整个 `patent-drafting-automation` 文件夹放入 `%USERPROFILE%\.codex\skills`。设置过自定义CODEX_HOME时，放到该目录下的 `skills` 文件夹。不要多嵌套一层同名目录。
3. 如果已有旧版，先备份到skills目录之外，再放入新版；保留旧项目及其skill-snapshot。
4. 重新打开Codex会话，让Codex依据[运行环境说明](references/runtime.md)检测当前电脑并填写 `references/runtime.json`，完成预检及首次样稿页面检查。公开包的配置为空模板，未配置前不能视为可直接转换Word。

## 安装完成后的使用

```text
使用 $patent-drafting-automation，处理“C:\Users\你的用户名\Desktop\待撰写文件夹”中的技术项目优化书，按可用容量并行初稿、独立复查和修改，每案通过后立即将Word终稿放入桌面同一个批次文件夹，不等待整批完成。每案结束后释放该案专属进程。
```

将示例地址换成实际案件目录。账号必须提供技能所配置模型及执行权限；配置的6/3/3池上限不代表当前账号一定有相同并发额度。新版作用于新建批次，已有批次继续使用其冻结版本。
