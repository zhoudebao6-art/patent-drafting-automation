# 在 Windows 安装专利撰写自动化 v6.73

1. 下载发布的完整 ZIP 和 SHA256SUMS.txt。使用 PowerShell 的 Get-FileHash 核对 SHA-256。
2. 解压后，将整个 patent-drafting-automation 文件夹放到当前用户的 .codex/skills 下。自定义 CODEX_HOME 时使用该目录下的 skills。已有同名技能，先把原目录备份到 skills 之外。在途批次沿用各自的冻结副本。
3. 在新 Codex 对话中，按 references/runtime.md 探测本机文档 Python、Poppler、字体，以及已安装的 WPS 或 LibreOffice。按实际路径和已核验程序配置 references/runtime.json。运行 scripts/render_docx_checked.py --preflight，再实际渲染并查看代表性样稿；有公式的案件还要核对数学字形。
4. 在 Codex 中确认账号可以使用阶段配置指定的模型。默认池上限为初稿16、复查8、修改8，实际并发取决于账号和运行环境。

安装后，用新对话发送下面的指令，并替换案件路径。

    使用 $patent-drafting-automation 处理“D:\待撰写案件”，每案复查通过后立即将终稿交付到桌面同一文件夹。

本包提供技能、规则、运行脚本和已公开的参考样本。运行环境配置为空模板，外部工具由目标机提供。
