"""
Github Desktop 汉化工具 v1.1.0
自动探测安装目录，读取词典并替换 JS 文件中的英文字符串。

v1.1.0 变更:
  - 支持 asar 打包模式（自动 extract/pack）
  - 替换限定在字符串字面量内，避免破坏 JS 逻辑
  - 词典去重 + 版本头声明
  - 备份范围动态化 + 旧备份自动清理
  - BOM 保留 + UTF-8 严格读取
  - --no-backup 与 --deploy 组合需 --force 确认
  - 部署后可选 node --check 语法校验

用法:
  python apply_zh.py --deploy                  # 自动探测 + 备份 + 替换 + 部署
  python apply_zh.py --dry-run                 # 预览替换效果，不写文件
  python apply_zh.py --rollback                # 回滚最新备份
  python apply_zh.py --deploy --dict <path>    # 使用自定义词典
  python apply_zh.py --deploy --src <path>     # 手动指定 GH 路径
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

VERSION = "1.1.0"

# 常量
MIN_SOURCE_LEN = 3
MIN_FILE_SIZE = 1024
MATCH_WARN_THRESHOLD = 80
BACKUP_KEEP = 5
SUBSTR_FALLBACK_MIN_LEN = 10  # 字面量未命中时，长度 >= 此值且含空格的 source 回退到子串匹配
BOM = b"\xef\xbb\xbf"

# 词典版本头正则
RE_DICT_VERSION = re.compile(r"^#\s*@gh-version:\s*([\d.\-\w*]+)", re.IGNORECASE)


# ============================================================
# 依赖检查
# ============================================================

def check_dependencies():
    """检查 Python 版本和系统工具可用性，失败则 sys.exit。"""
    if sys.version_info < (3, 6):
        print(f"[ERROR] 需要 Python >= 3.6，当前: {sys.version}")
        sys.exit(1)

    if sys.platform != "win32":
        print("[ERROR] 本脚本仅支持 Windows 平台")
        sys.exit(1)

    for cmd in ("reg", "tasklist"):
        if not _command_available([cmd, "/?"]):
            print(f"[ERROR] 系统缺少 {cmd} 命令")
            sys.exit(1)


def _command_available(cmd_args):
    try:
        subprocess.run(cmd_args, capture_output=True, timeout=3)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return True  # 命令存在但返回非零，视为可用


# ============================================================
# 自动探测
# ============================================================

# 注册表候选键（覆盖常见安装位置）
REG_HIVES = [
    (r"HKCU\Software\GitHubDesktop", "InstallPath"),
    (r"HKLM\SOFTWARE\GitHubDesktop", "InstallPath"),
    (r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\GitHubDesktop_is1", "InstallLocation"),
    (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\GitHubDesktop_is1", "InstallLocation"),
    (r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\GitHubDesktop_is1", "InstallLocation"),
]

# 常见安装根路径
COMMON_PATHS = [
    os.path.expandvars(r"%LOCALAPPDATA%\GitHubDesktop"),
    os.path.expandvars(r"%APPDATA%\GitHubDesktop"),
    os.path.expandvars(r"%ProgramFiles%\GitHubDesktop"),
    os.path.expandvars(r"%ProgramW6432%\GitHubDesktop"),
    r"C:\Program Files\GitHubDesktop",
    r"D:\Program Files\GitHubDesktop",
]


def find_gh_desktop():
    """探测 GH 安装目录，返回 (target_path, mode)。
    mode: 'loose' = resources\\app 目录; 'asar' = resources\\app.asar 文件。
    失败返回 (None, None)。
    """
    # ① 注册表
    for hive, value_name in REG_HIVES:
        path = _query_reg(hive, value_name)
        if path:
            result = _probe_resources(path)
            if result:
                return result

    # ② 常见路径
    for p in COMMON_PATHS:
        if os.path.isdir(p):
            result = _probe_resources(p)
            if result:
                return result

    return (None, None)


def _query_reg(hive, value_name):
    try:
        result = subprocess.run(
            ["reg", "query", hive, "/v", value_name],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if value_name in line and "REG_" in line:
                return line.split("REG_SZ", 1)[-1].strip()
    except Exception:
        return None
    return None


def _probe_resources(gh_root):
    """在安装根目录下找版本号最大的 app-*，判断 loose/asar 模式。"""
    if not os.path.isdir(gh_root):
        return None
    best_ver = (0, 0, 0)
    best_app_dir = None
    for name in os.listdir(gh_root):
        if not name.startswith("app-"):
            continue
        ver = _parse_version(name[4:])
        if ver is None:
            continue
        if ver > best_ver:
            best_ver = ver
            best_app_dir = os.path.join(gh_root, name)

    if not best_app_dir:
        return None

    resources_dir = os.path.join(best_app_dir, "resources")
    if not os.path.isdir(resources_dir):
        return None

    # 优先 loose 模式
    loose_dir = os.path.join(resources_dir, "app")
    if os.path.isdir(loose_dir):
        return (loose_dir, "loose")

    # 其次 asar 模式
    asar_file = os.path.join(resources_dir, "app.asar")
    if os.path.isfile(asar_file):
        return (asar_file, "asar")

    return None


def _parse_version(ver_str):
    """从 '3.4.0' 或 '3.4.0-beta1' 提取数字元组，忽略非数字后缀。"""
    nums = re.findall(r"\d+", ver_str)
    if not nums:
        return None
    return tuple(int(x) for x in nums)


# ============================================================
# asar 工具
# ============================================================

def check_asar_available():
    """检测 asar 命令是否可用。"""
    try:
        result = subprocess.run(
            ["asar", "--version"], capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    except Exception:
        return False


def extract_asar(asar_path, dest_dir):
    """解包 asar 到 dest_dir，失败 sys.exit。"""
    try:
        os.makedirs(dest_dir, exist_ok=True)
        result = subprocess.run(
            ["asar", "extract", asar_path, dest_dir],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"[ERROR] asar extract 失败: {result.stderr}")
            sys.exit(1)
    except FileNotFoundError:
        print("[ERROR] 未找到 asar 命令，请先安装: npm install -g @electron/asar")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("[ERROR] asar extract 超时")
        sys.exit(1)


def pack_asar(src_dir, asar_path):
    """打包 src_dir 为 asar 文件，失败 sys.exit。"""
    try:
        result = subprocess.run(
            ["asar", "pack", src_dir, asar_path],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            print(f"[ERROR] asar pack 失败: {result.stderr}")
            sys.exit(1)
    except FileNotFoundError:
        print("[ERROR] 未找到 asar 命令，请先安装: npm install -g @electron/asar")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("[ERROR] asar pack 超时")
        sys.exit(1)


# ============================================================
# 词典加载
# ============================================================

def load_dict(dict_path):
    """加载词典。返回 (entries, dict_version)。
    - 按 (source, src_file) 去重，保留首条
    - 按源字符串长度降序排列
    - dict_version 可能为 None
    """
    entries = []
    seen = set()
    dict_version = None
    line_no = 0

    try:
        with open(dict_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line_no += 1
                line = line.strip()
                if not line:
                    continue

                # 读取版本头
                m = RE_DICT_VERSION.match(line)
                if m and dict_version is None:
                    dict_version = m.group(1)
                    continue

                parts = line.split(">*.*<")
                if len(parts) < 2:
                    continue

                source = _unwrap_quotes(parts[0])
                target = _unwrap_quotes(parts[1])
                category = parts[2] if len(parts) > 2 else "待分组"
                src_file = parts[3] if len(parts) > 3 else "renderer.js"

                # 去重键
                key = (source, src_file)
                if key in seen:
                    continue
                seen.add(key)

                entries.append((source, target, category, src_file))
    except UnicodeDecodeError:
        print(f"[ERROR] 词典文件编码错误，应为 UTF-8: {dict_path}")
        sys.exit(1)
    except IOError as e:
        print(f"[ERROR] 无法读取词典文件: {e}")
        sys.exit(1)

    if not entries:
        print(f"[ERROR] 词典为空: {dict_path}")
        sys.exit(1)

    entries.sort(key=lambda x: len(x[0]), reverse=True)
    return entries, dict_version


def _unwrap_quotes(s):
    """剥离外层引号。"""
    s = s.strip()
    if len(s) >= 2:
        if (s.startswith('"') and s.endswith('"')) or \
           (s.startswith("'") and s.endswith("'")):
            return s[1:-1]
    return s


# ============================================================
# 文件读写（保留 BOM）
# ============================================================

def read_js_file(path):
    """读取 JS 文件，返回 (content, has_bom)。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except IOError as e:
        print(f"[ERROR] 无法读取源文件 {os.path.basename(path)}: {e}")
        sys.exit(1)

    has_bom = raw.startswith(BOM)
    if has_bom:
        raw = raw[len(BOM):]
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        print(f"[ERROR] 文件非 UTF-8 编码 {os.path.basename(path)}: {e}")
        sys.exit(1)
    return content, has_bom


def write_js_file(path, content, has_bom):
    """写入 JS 文件，按原 BOM 状态写入。"""
    try:
        data = content.encode("utf-8")
        if has_bom:
            data = BOM + data
        with open(path, "wb") as f:
            f.write(data)
    except IOError as e:
        print(f"[ERROR] 无法写入 {os.path.basename(path)}: {e}")
        sys.exit(1)


# ============================================================
# 备份
# ============================================================

def backup_files(file_paths, backup_dir, keep=BACKUP_KEEP):
    """备份给定文件列表，自动清理旧备份。失败 sys.exit。"""
    try:
        os.makedirs(backup_dir, exist_ok=True)
    except OSError as e:
        print(f"[ERROR] 无法创建备份目录: {e}")
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backed = []
    for src in file_paths:
        if not os.path.exists(src):
            print(f"[WARN] 源文件不存在，跳过备份: {src}")
            continue
        fname = os.path.basename(src)
        dst = os.path.join(backup_dir, f"{fname}.bak_{ts}")
        try:
            shutil.copy2(src, dst)
            backed.append(dst)
        except OSError as e:
            print(f"[ERROR] 备份失败 {fname}: {e}")
            sys.exit(1)

    if not backed:
        print("[ERROR] 没有可备份的源文件")
        sys.exit(1)

    _prune_old_backups(backup_dir, keep)
    return backed


def _prune_old_backups(backup_dir, keep):
    """按时间戳分组，保留最近 keep 个时间戳的备份。"""
    ts_files = {}  # ts -> [filenames]
    for f in os.listdir(backup_dir):
        if ".bak_" in f:
            ts = f.rsplit(".bak_", 1)[-1]
            ts_files.setdefault(ts, []).append(f)

    if len(ts_files) <= keep:
        return

    sorted_ts = sorted(ts_files.keys(), reverse=True)
    for ts in sorted_ts[keep:]:
        for fname in ts_files[ts]:
            try:
                os.remove(os.path.join(backup_dir, fname))
            except OSError:
                pass


def find_latest_backup(backup_dir, prefix=None):
    """找最新备份时间戳。prefix 可限定文件名前缀（如 'app.asar'）。"""
    if not os.path.isdir(backup_dir):
        return None
    timestamps = set()
    for f in os.listdir(backup_dir):
        if ".bak_" not in f:
            continue
        if prefix and not f.startswith(prefix):
            continue
        timestamps.add(f.rsplit(".bak_", 1)[-1])
    if not timestamps:
        return None
    return sorted(timestamps, reverse=True)[0]


# ============================================================
# 替换（安全：限定字符串字面量边界）
# ============================================================

def apply_translations(entries, files, output_dir, dry_run=False):
    """逐条替换。files: {fname: (content, has_bom)}。
    仅替换被引号包裹的完整字符串字面量，避免误伤代码逻辑。
    """
    stats = {"total": len(entries), "applied": 0, "skipped": 0, "not_found": []}
    working = {fname: content for fname, (content, _) in files.items()}

    for source, target, category, src_file in entries:
        if len(source) < MIN_SOURCE_LEN:
            stats["skipped"] += 1
            continue
        if not re.search(r"[a-zA-Z]", source):
            stats["skipped"] += 1
            continue

        fname = src_file if src_file in working else "renderer.js"
        if fname not in working:
            stats["skipped"] += 1
            continue

        content = working[fname]
        new_content, count = _safe_replace(content, source, target)
        if count > 0:
            if not dry_run:
                working[fname] = new_content
            stats["applied"] += 1
        else:
            stats["skipped"] += 1
            stats["not_found"].append(source[:60])

    if dry_run:
        return stats, []

    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as e:
        print(f"[ERROR] 无法创建输出目录: {e}")
        sys.exit(1)

    written = []
    for fname, content in working.items():
        has_bom = files[fname][1]
        out_path = os.path.join(output_dir, fname)
        write_js_file(out_path, content, has_bom)
        written.append(out_path)

    return stats, written


def _safe_replace(content, source, target):
    """优先字面量匹配（安全）；长 source 字面量未命中时回退到子串匹配。

    词典 source 常为 JS 字面量的核心部分（首尾空格差异或前缀截断），
    严格字面量匹配会漏匹配。长串误伤风险低，回退到子串匹配；短串保持严格。
    部署后由 node --check 兜底校验 JS 语法完整性。
    """
    # ① 字面量匹配：仅替换被相同引号包裹的完整字符串字面量
    pattern = re.compile(r'(["\'])' + re.escape(source) + r'\1')
    new_content, count = pattern.subn(lambda m: m.group(1) + target + m.group(1), content)
    if count > 0:
        return new_content, count

    # ② 长 source 回退到子串匹配（含空格的 UI 文本误伤变量名/注释的概率极低）
    if len(source) >= SUBSTR_FALLBACK_MIN_LEN and " " in source:
        c = content.count(source)
        if c > 0:
            return content.replace(source, target), c

    return content, 0


# ============================================================
# 部署 & 回滚
# ============================================================

def deploy_loose(output_dir, target_dir):
    """loose 模式部署：复制文件。"""
    deployed = []
    for fname in os.listdir(output_dir):
        src = os.path.join(output_dir, fname)
        dst = os.path.join(target_dir, fname)
        if not os.path.isfile(src):
            continue
        try:
            shutil.copy2(src, dst)
            deployed.append(dst)
        except OSError as e:
            print(f"[ERROR] 部署失败 {fname}: {e}")
            print("         请确认 Github Desktop 已关闭，且目标目录有写入权限。")
            sys.exit(1)

    if not deployed:
        print("[ERROR] 没有可部署的文件")
        sys.exit(1)
    return deployed


def deploy_asar(work_dir, target_asar, backup_dir):
    """asar 模式部署：打包 work_dir 为 asar，覆盖目标。"""
    tmp_asar = work_dir + ".asar"
    pack_asar(work_dir, tmp_asar)
    try:
        shutil.copy2(tmp_asar, target_asar)
    except OSError as e:
        print(f"[ERROR] asar 部署失败: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(tmp_asar):
            os.remove(tmp_asar)
    return [target_asar]


def verify_deploy(deployed_files):
    """部署后验证：大小 + 可选 JS 语法校验。"""
    all_ok = True
    node_available = _command_available(["node", "--version"])

    for f in deployed_files:
        size = os.path.getsize(f)
        if size < MIN_FILE_SIZE:
            print(f"[WARN] 文件异常小 ({size} bytes): {os.path.basename(f)}")
            all_ok = False
            continue

        print(f"  {os.path.basename(f)} ({size/1024:.0f} KB) ✓", end="")

        # asar 文件不做 node --check；loose js 文件做语法校验
        if f.endswith(".js") and node_available:
            try:
                result = subprocess.run(
                    ["node", "--check", f], capture_output=True, timeout=30,
                )
                if result.returncode == 0:
                    print(" 语法✓", end="")
                else:
                    print(f" 语法✗ {result.stderr.decode('utf-8', errors='ignore').strip()}", end="")
                    all_ok = False
            except Exception:
                pass
        print()

    return all_ok


def rollback(src_target, backup_dir, mode):
    """回滚。src_target: loose 模式为目录，asar 模式为 asar 文件路径。"""
    if mode == "asar":
        return _rollback_asar(src_target, backup_dir)
    return _rollback_loose(src_target, backup_dir)


def _rollback_loose(src_dir, backup_dir):
    """loose 模式回滚：基于备份目录中实际存在的 .bak_ 文件。"""
    ts = find_latest_backup(backup_dir)
    if not ts:
        print("[ERROR] 未找到备份文件")
        return False

    # 收集该时间戳下所有备份文件
    bak_files = [f for f in os.listdir(backup_dir) if f.endswith(f".bak_{ts}")]
    if not bak_files:
        print(f"[ERROR] 时间戳 {ts} 下无备份文件")
        return False

    restored = []
    rollback_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for bak_fname in bak_files:
        orig_name = bak_fname.rsplit(".bak_", 1)[0]
        src = os.path.join(backup_dir, bak_fname)
        dst = os.path.join(src_dir, orig_name)
        try:
            if os.path.exists(dst):
                pre = os.path.join(backup_dir, f"{orig_name}.pre_rollback_{rollback_ts}")
                shutil.copy2(dst, pre)
            shutil.copy2(src, dst)
            restored.append(dst)
        except OSError as e:
            print(f"[ERROR] 回滚失败 {orig_name}: {e}")
            return False

    if restored:
        print(f"已回滚 {len(restored)} 个文件 (备份时间戳: {ts})")
        for f in restored:
            print(f"  {f}")
        return True
    return False


def _rollback_asar(target_asar, backup_dir):
    """asar 模式回滚：恢复 app.asar.bak_ts。"""
    asar_name = os.path.basename(target_asar)
    ts = find_latest_backup(backup_dir, prefix=asar_name)
    if not ts:
        print("[ERROR] 未找到 asar 备份文件")
        return False

    src = os.path.join(backup_dir, f"{asar_name}.bak_{ts}")
    if not os.path.exists(src):
        print(f"[ERROR] 备份文件不存在: {src}")
        return False

    rollback_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        if os.path.exists(target_asar):
            pre = os.path.join(backup_dir, f"{asar_name}.pre_rollback_{rollback_ts}")
            shutil.copy2(target_asar, pre)
        shutil.copy2(src, target_asar)
    except OSError as e:
        print(f"[ERROR] asar 回滚失败: {e}")
        return False

    print(f"已回滚 asar (备份时间戳: {ts})")
    print(f"  {target_asar}")
    return True


# ============================================================
# 运行检测
# ============================================================

def check_gh_running():
    """检测 GitHub Desktop 相关进程。"""
    for proc in ("GitHubDesktop.exe", "Update.exe"):
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {proc}"],
                capture_output=True, text=True, timeout=5,
            )
            if proc in result.stdout:
                return True, proc
        except Exception:
            continue
    return False, None


# ============================================================
# 主程序
# ============================================================

def main():
    check_dependencies()

    parser = argparse.ArgumentParser(description="Github Desktop 汉化工具")
    parser.add_argument("--dict", default=None, help="词典文件路径（默认: 同目录下 dict/Windows.zh）")
    parser.add_argument("--src", default=None, help="Github Desktop resources\\app 目录或 app.asar 文件（默认: 自动探测）")
    parser.add_argument("--output", default=None, help="输出目录（默认: src 同级的 zh_output）")
    parser.add_argument("--backup", default=None, help="备份目录（默认: output 同级的 zh_backup）")
    parser.add_argument("--deploy", action="store_true", help="直接部署到 Github Desktop")
    parser.add_argument("--no-backup", action="store_true", help="跳过备份")
    parser.add_argument("--force", action="store_true", help="强制执行（GH 运行中 / 无备份部署）")
    parser.add_argument("--dry-run", action="store_true", help="预览替换效果，不写文件")
    parser.add_argument("--rollback", action="store_true", help="回滚到最新备份")
    args = parser.parse_args()

    try:
        _run(args)
    except KeyboardInterrupt:
        print("\n[ABORT] 用户中断")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[ERROR] 未预期的错误: {e}")
        sys.exit(1)


def _run(args):
    # === 回滚模式 ===
    if args.rollback:
        target, mode = _resolve_src(args.src)
        if target is None:
            print("[ERROR] 无法定位 Github Desktop，请用 --src 手动指定")
            sys.exit(1)
        backup_dir = os.path.abspath(args.backup) if args.backup else os.path.join(
            os.path.dirname(os.path.dirname(target)), "zh_backup"
        )
        ok = rollback(target, backup_dir, mode)
        sys.exit(0 if ok else 1)

    # === 危险组合拦截：--no-backup --deploy ===
    if args.no_backup and args.deploy and not args.force:
        print("[ERROR] --no-backup 与 --deploy 组合风险极高（无法回滚）")
        print("        若确认继续，请加 --force 参数。")
        sys.exit(1)

    # === 词典路径 ===
    if args.dict:
        dict_path = os.path.abspath(args.dict)
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        dict_path = os.path.join(script_dir, "..", "dict", "Windows.zh")
    dict_path = os.path.normpath(dict_path)
    if not os.path.isfile(dict_path):
        print(f"[ERROR] 词典文件不存在: {dict_path}")
        sys.exit(1)

    # === 源目录/文件 ===
    target, mode = _resolve_src(args.src)
    if target is None:
        print("[ERROR] 无法自动定位 Github Desktop，请用 --src 手动指定路径")
        sys.exit(1)

    if mode == "loose" and not os.path.isdir(target):
        print(f"[ERROR] 源目录不存在: {target}")
        sys.exit(1)
    if mode == "asar" and not os.path.isfile(target):
        print(f"[ERROR] asar 文件不存在: {target}")
        sys.exit(1)

    # asar 模式依赖检查
    if mode == "asar" and not check_asar_available():
        print("[ERROR] asar 模式需要 asar 工具，请先安装: npm install -g @electron/asar")
        sys.exit(1)

    # === 输出/备份目录 ===
    base_dir = os.path.dirname(os.path.dirname(target))  # app-* 目录
    output_dir = os.path.abspath(args.output) if args.output else os.path.join(base_dir, "zh_output")
    backup_dir = os.path.abspath(args.backup) if args.backup else os.path.join(base_dir, "zh_backup")

    # === 运行检测 ===
    if not args.dry_run:
        running, proc = check_gh_running()
        if running and not args.force:
            print(f"[ERROR] 检测到 {proc} 正在运行，请先关闭后再执行汉化。")
            print("        若确定要继续，请加 --force 参数。")
            sys.exit(1)

    # === 版本号 ===
    base_name = os.path.basename(base_dir)
    version = base_name[4:] if base_name.startswith("app-") else "unknown"

    print(f"Github Desktop 版本: {version}")
    print(f"目标:     {target}")
    print(f"模式:     {mode.upper()}")
    print(f"词典:     {dict_path}")
    if args.dry_run:
        print("预览:     DRY-RUN (不写文件)")
    else:
        print(f"输出目录: {output_dir}")
        print(f"备份目录: {backup_dir}")
    print()

    # === 1. 加载词典 ===
    entries, dict_version = load_dict(dict_path)
    print(f"词典条目: {len(entries)}")
    if dict_version:
        print(f"词典声明版本: {dict_version}")
        if version != "unknown" and not _version_matches(version, dict_version):
            print(f"[WARN] 词典版本 {dict_version} 与 GH 版本 {version} 可能不匹配")
            if not args.force and not args.dry_run:
                print("       若确认继续，请加 --force 参数。")
                sys.exit(1)

    # === 2. 准备工作文件 ===
    work_dir = None
    cleanup_needed = False
    try:
        if mode == "asar":
            # asar 模式：解包到临时目录
            work_dir = tempfile.mkdtemp(prefix="gh_zh_")
            cleanup_needed = True
            print(f"解包 asar 到临时目录...")
            extract_asar(target, work_dir)
            src_base = work_dir
        else:
            src_base = target

        # 读取所有词典涉及的源文件
        files = _load_source_files(entries, src_base)

        # === 3. 备份 ===
        if not args.no_backup and not args.dry_run:
            if mode == "asar":
                backed = backup_files([target], backup_dir)
            else:
                backed = backup_files(
                    [os.path.join(src_base, f) for f in files],
                    backup_dir,
                )
            print(f"已备份: {len(backed)} 个文件 -> {backup_dir}")
        elif args.no_backup:
            print("[SKIP] 跳过备份 (--no-backup)")

        # === 4. 替换 ===
        stats, written = apply_translations(entries, files, output_dir, dry_run=args.dry_run)
        print()
        match_pct = stats["applied"] / stats["total"] * 100 if stats["total"] else 0
        print(f"应用结果: {stats['applied']}/{stats['total']} 已替换 ({match_pct:.1f}%), {stats['skipped']} 跳过")

        if stats["not_found"]:
            print(f"未匹配: {len(stats['not_found'])} 条")
            for s in stats["not_found"][:10]:
                print(f"  - {s}")
            if len(stats["not_found"]) > 10:
                print(f"  ... 还有 {len(stats['not_found']) - 10} 条")
            if match_pct < MATCH_WARN_THRESHOLD:
                print()
                print(f"[WARN] 匹配率低于 {MATCH_WARN_THRESHOLD}%，可能 Github Desktop 版本升级，建议更新词典。")

        if args.dry_run:
            return

        # === 5. 部署 ===
        if args.deploy:
            if mode == "asar":
                print(f"\n打包 asar 并部署...")
                deployed = deploy_asar(output_dir, target, backup_dir)
            else:
                deployed = deploy_loose(output_dir, target)
            print(f"\n已部署到: {target}")
            verify_deploy(deployed)
            print("\n[提示] 请启动 Github Desktop 目视确认界面汉化效果。")
        else:
            print(f"\n汉化文件已生成在: {output_dir}")
            if mode == "asar":
                print("[提示] asar 模式下，未加 --deploy 时仅输出 loose 文件，需手动打包。")
            else:
                print("加 --deploy 参数可自动部署到 Github Desktop")

    finally:
        if cleanup_needed and work_dir and os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


def _resolve_src(src_arg):
    """解析 --src 参数或自动探测，返回 (target, mode)。"""
    if src_arg:
        src = os.path.abspath(src_arg)
        if os.path.isdir(src):
            return (src, "loose")
        if os.path.isfile(src) and src.endswith(".asar"):
            return (src, "asar")
        return (src, "loose")
    return find_gh_desktop()


def _load_source_files(entries, src_base):
    """读取词典涉及的所有源文件。返回 {fname: (content, has_bom)}。"""
    file_names = set()
    for _, _, _, src_file in entries:
        file_names.add(src_file if src_file else "renderer.js")
    # 确保主文件在列表中
    file_names.update({"main.js", "renderer.js"})

    files = {}
    for fname in file_names:
        path = os.path.join(src_base, fname)
        if os.path.exists(path):
            content, has_bom = read_js_file(path)
            files[fname] = (content, has_bom)
        else:
            print(f"[WARN] 源文件不存在: {fname}")

    if not files:
        print("[ERROR] 没有可处理的源文件")
        sys.exit(1)
    return files


def _version_matches(gh_version, dict_version_pattern):
    """简易版本匹配：dict_version_pattern 可含 * 通配符。"""
    if "*" not in dict_version_pattern:
        return gh_version.endswith(dict_version_pattern) or dict_version_pattern in gh_version
    # 简单通配：3.* 匹配 3.4.0
    prefix = dict_version_pattern.split("*")[0]
    return gh_version.startswith(prefix)


if __name__ == "__main__":
    main()
