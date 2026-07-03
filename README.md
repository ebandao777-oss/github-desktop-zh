# GitHub Desktop 汉化工具

自动探测 GitHub Desktop 安装目录（Windows/macOS/Linux），使用内置词典对 `main.js` / `renderer.js` 执行字符串替换，完成界面汉化。支持预览、部署、回滚。

## 环境要求

- Windows 10/11 / macOS 10.15+ / Linux
- Python ≥ 3.6
- GitHub Desktop 已安装
- （可选）node 用于语法校验
- （asar 模式）`npm install -g @electron/asar`

## 快速开始

```powershell
# ① 预览替换效果（不修改任何文件）
python scripts/apply_zh.py --dry-run

# ② 执行汉化（备份 + 替换 + 部署）
python scripts/apply_zh.py --deploy

# ③ 重新启动 GitHub Desktop 生效
```

## 命令说明

| 命令 | 说明 |
|------|------|
| `--deploy` | 自动探测 + 备份 + 替换 + 部署到 GitHub Desktop |
| `--dry-run` | 预览模式，仅统计匹配数，不写文件 |
| `--rollback` | 回滚到最新备份 |
| `--force` | GitHub Desktop 运行中也强制执行 |
| `--no-backup` | 跳过备份（不推荐） |
| `--src <path>` | 手动指定 `resources\app` 目录 |
| `--dict <path>` | 使用自定义词典 |

## 目录结构

```
github-desktop-zh/
├── README.md             # 本文件
├── SKILL.md              # 技能定义（供 Marvis 使用）
├── dict/
│   └── dictionary.txt       # 汉化词典（1080 条）
└── scripts/
    └── apply_zh.py       # 汉化脚本
```

## 词典格式

词典文件 `dict/dictionary.txt` 每行一条记录，字段以 `>*.*<` 分隔：

```
源字符串>*.*<目标字符串>*.*<分类>*.*<目标文件
```

示例：

```
"Add local repository">*.*<"添加本地存储库">*.*<菜单-文件>*.*<main.js
```

## 工作流程

1. **探测** — 读取注册表 `HKCU/HKLM\Software\GitHubDesktop\InstallPath`，或搜索 `%LOCALAPPDATA%\GitHubDesktop\app-*`
2. **备份** — 复制 `main.js` / `renderer.js` 到 `zh_backup\`，带时间戳
3. **替换** — 按源字符串长度降序遍历词典，在 JS 源文件中执行字符串替换
4. **部署** — 将替换后的文件复制回 GitHub Desktop 目录 5.
5. **验证** — 检查部署文件大小（>1KB 视为正常）

## 已知限制

- 基于纯字符串替换，非 AST 级翻译，短字符串（如 ` to `）可能在非字符串上下文被误替换
- 词典中 93% 条目尚未分类（标记为"待分组"）
- GitHub Desktop 版本升级后可能需要更新词典
