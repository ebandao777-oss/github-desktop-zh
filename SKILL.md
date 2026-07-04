---
name: github-desktop-zh
description: Github Desktop 汉化工具 — 自动探测安装目录（支持 loose 与 asar 打包模式），使用内置词典对 JS 源文件执行字符串字面量替换，完成界面汉化。支持预览、部署、回滚、语法校验。
version: 1.1.0
author: "智慧半岛"
license: MIT
allowed-tools:
  - Read
  - Grep
  - Glob
  - Shell
  - Edit
  - Write
tags: [github-desktop, 汉化, 本地化, localization, translation]
triggers:
  - Github Desktop 汉化
  - 应用汉化
  - 更新汉化词典
  - 恢复原版
  - 回滚
  - 撤销汉化
platforms: [windows, linux, macos]
os: [windows, linux, macos]
dependencies:
  python: ">=3.6"
  system_tools:
    windows: [reg, tasklist]
    macos: [pgrep]
    linux: [pgrep]
  optional: [node, asar]
---

# Github Desktop 汉化技能（Windows / macOS / Linux）

调用内置脚本自动探测 Github Desktop 安装目录，识别 loose 或 asar 打包模式，使用内置词典执行 JS 字符串字面量替换。

## 资源结构

```
GitHubDesktop-zh/
├── SKILL.md              # 本文件
├── dict/
│   └── dictionary.txt    # 汉化词典（首行可声明 # @gh-version: 3.*）
└── scripts/
    └── apply_zh.py       # 汉化脚本 v1.1.0
```

## 运行模式

脚本自动识别目标为 `resources\app`（loose 模式）或 `resources\app.asar`（asar 模式）：

| 模式 | 目标 | 替换流程 | 部署方式 | 依赖 |
|------|------|---------|---------|------|
| loose | `app\main.js` 等 loose 文件 | 直接读写 | 复制覆盖 | 无 |
| asar | `app.asar` 打包文件 | extract → 替换 → pack | 重新打包覆盖 | 需 asar 工具 |

asar 工具安装：`npm install -g @electron/asar`

## 执行前检查（Agent 必须执行）

在执行任何命令前，Agent 应检查：

```powershell
# ① Python 版本
python --version

# ② 脚本存在性
Test-Path "{技能根目录}/scripts/apply_zh.py"

# ③ 词典存在性
Test-Path "{技能根目录}/dict/dictionary.txt"
```

以上任一失败即中断，告知用户缺失项。

## 执行命令

### 汉化（默认：自动探测 + 备份 + 替换 + 部署）

```powershell
python "{技能根目录}/scripts/apply_zh.py" --deploy
```

### 预览（不修改任何文件，仅展示匹配统计）

```powershell
python "{技能根目录}/scripts/apply_zh.py" --dry-run
```

### 回滚

```powershell
python "{技能根目录}/scripts/apply_zh.py" --rollback
```

### 自定义词典

```powershell
python "{技能根目录}/scripts/apply_zh.py" --deploy --dict "{用户提供的词典路径}"
```

### 手动指定 GH 路径（loose 目录或 asar 文件）

```powershell
python "{技能根目录}/scripts/apply_zh.py" --deploy --src "D:\...\app"
python "{技能根目录}/scripts/apply_zh.py" --deploy --src "D:\...\app.asar"
```

### GH 运行时强制执行

```powershell
python "{技能根目录}/scripts/apply_zh.py" --deploy --force
```

## 流程断点与验证

脚本内部每个步骤失败时均会 `sys.exit(1)` 并打印 `[ERROR]`。Agent 应在执行后检查退出码和 stdout：

| 断点 | 关键输出 | 失败动作 |
|------|---------|---------|
| 依赖 | （无显式输出，通过即继续） | 缺 reg/tasklist → 中断；asar 模式缺 asar → 中断并提示安装 |
| 探测 | `Github Desktop 版本: x.x.x` + `模式: LOOSE/ASAR` | 无此输出 → 探测失败，提示用户提供 `--src` |
| 词典 | `词典条目: N` + `词典声明版本: x.*` | 无此输出 → 词典加载失败；版本不匹配且未 `--force` → 中断 |
| 解包 | `解包 asar 到临时目录...` | 仅 asar 模式；失败 → 中断 |
| 备份 | `已备份: N 个文件` | 无此输出 → 备份失败，脚本已中断 |
| 替换 | `应用结果: N/N 已替换 (x.x%)` | 含"未匹配"行 → 部分条目未命中；匹配率 <80% → WARN 提示更新词典 |
| 部署 | `已部署到:` | 无此输出 → 部署失败，检查权限/进程占用 |
| 验证 | `文件名 (N KB) ✓ 语法✓` | 语法✗ → 替换破坏 JS，需回滚并更新词典 |
| 验收 | `[提示] 请启动 Github Desktop 目视确认...` | Agent 应建议用户启动 GH 目视检查 |

## 故障处理

| 场景 | 处理 |
|------|------|
| Python 不可用 | 提示安装 Python ≥ 3.6 |
| 无法定位 GH | 提示用户确认已安装，或提供 `--src` 路径（loose 目录或 asar 文件） |
| GH 正在运行 | 检测 GitHubDesktop.exe / Update.exe，提示关闭后重试，或询问是否 `--force` |
| asar 模式缺 asar 工具 | 提示 `npm install -g @electron/asar` |
| 词典版本不匹配 | 打印 WARN，非 `--force` 时中断，提示更新词典或加 `--force` |
| 大量条目未匹配 (≥20%) | 可能版本升级，提示用户提供新版词典 |
| 替换后语法校验失败 | 提示词典含危险条目，建议回滚并修正词典 |
| --no-backup --deploy 组合 | 脚本拒绝执行，提示加 `--force` 确认风险 |
| 回滚无备份 | 提示无法回滚，建议重装 Github Desktop |
| 脚本执行报错 | 将完整 stderr 反馈给用户 |

## 词典格式

每行一条：`源串>*.*<译串>*.*<分组>*.*<目标文件`

- 源串/译串可用引号包裹（脚本自动剥离外层引号）
- 首行可声明版本：`# @gh-version: 3.*`（可选）
- 空行忽略；重复条目自动去重（按 源串+目标文件）
- 脚本按源串长度降序替换，避免短串破坏长串

## 安全机制

- **混合替换策略**：优先字面量匹配（`(["'])source\1`，仅命中被相同引号包裹的完整字符串字面量）；字面量未命中时，对长度 ≥10 且含空格的 source 回退到子串匹配（UI 文本特征，误伤变量名/注释概率低）；短串保持严格字面量匹配
- **BOM 保留**：读写时检测并保留原文件 BOM 状态
- **备份自动清理**：保留最近 5 个时间戳的备份，自动清理更旧的
- **部署后语法校验**：若 node 可用，自动对 .js 文件执行 `node --check`，语法错误时告警
- **回滚前预备份**：回滚前将当前文件备份为 `.pre_rollback_*`
