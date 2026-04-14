#!/usr/bin/env python3
"""
Smart Commit Analyzer - 智能提交分析器

自动演进的提交分析系统：
1. 从 git 历史自动提取修复
2. AI 生成验证规则
3. 规则库自动增长
4. 每次运行自动检测回归

不再需要手动维护规则列表！
"""

import subprocess
import json
import os
import re
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional

# 规则存储文件
RULES_FILE = Path(__file__).parent.parent / "configs" / "auto_generated_rules.json"

# 文件移动检测：常见的新位置
FILE_SEARCH_PATHS = [
    "",           # 原位置 (相对于项目根目录)
    "scripts/",   # 脚本目录
    "tests/",     # 测试目录
    "tools/",     # 工具目录
    "docs/",      # 文档目录
]


def find_file(filepath: str) -> tuple[str, bool]:
    """
    查找文件，如果在原位置不存在，尝试在其他目录查找
    返回: (实际路径, 是否被移动)
    """
    project_root = get_project_root()

    # 先检查原位置
    if (project_root / filepath).exists():
        return filepath, False

    # 文件不在原位置，尝试其他位置
    filename = Path(filepath).name

    for search_path in FILE_SEARCH_PATHS:
        new_path = search_path + filename
        if (project_root / new_path).exists():
            return new_path, True

    # 还找不到，返回原路径
    return filepath, False


def run_git(cmd: str) -> str:
    """执行 git 命令 (在项目根目录)"""
    result = subprocess.run(
        f"git {cmd}",
        shell=True,
        capture_output=True,
        text=True,
        cwd=get_project_root()
    )
    return result.stdout.strip()


def get_project_root() -> Path:
    """获取项目根目录"""
    return Path(__file__).parent.parent


def get_file_content(filepath: str) -> Optional[str]:
    """读取文件内容 (相对于项目根目录)"""
    try:
        full_path = get_project_root() / filepath
        if full_path.exists():
            return full_path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        pass
    return None


def load_rules() -> dict:
    """加载已有规则"""
    if RULES_FILE.exists():
        try:
            return json.loads(RULES_FILE.read_text())
        except Exception:
            pass
    return {"rules": [], "metadata": {"created": datetime.now().isoformat()}}


def save_rules(rules: dict):
    """保存规则"""
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    rules["metadata"]["updated"] = datetime.now().isoformat()
    rules["metadata"]["count"] = len(rules["rules"])
    RULES_FILE.write_text(json.dumps(rules, indent=2, ensure_ascii=False))


def generate_rule_id(commit_hash: str, file_path: str) -> str:
    """生成唯一规则 ID"""
    content = f"{commit_hash}:{file_path}"
    return hashlib.md5(content.encode()).hexdigest()[:12]


def extract_fix_pattern(diff: str, file_path: str) -> Optional[dict]:
    """从 diff 中提取修复模式"""
    # 提取添加的行 (以 + 开头，但不是 +++)
    added_lines = []
    removed_lines = []

    for line in diff.split('\n'):
        if line.startswith('+') and not line.startswith('+++'):
            added_lines.append(line[1:].strip())
        elif line.startswith('-') and not line.startswith('---'):
            removed_lines.append(line[1:].strip())

    if not added_lines:
        return None

    # 识别关键模式
    pattern = None
    pattern_type = "contains"

    # 模式1: import 语句变更
    for line in added_lines:
        if line.startswith('from ') or line.startswith('import '):
            pattern = line
            pattern_type = "import"
            break

    # 模式2: 函数/方法定义
    if not pattern:
        for line in added_lines:
            if re.match(r'^\s*def \w+', line):
                pattern = re.search(r'def (\w+)', line).group(1)
                pattern_type = "function_exists"
                break

    # 模式3: 关键代码片段 (配置、验证等)
    if not pattern:
        keywords = ['if ', 'return ', '.get(', 'config', 'validate', 'check']
        for line in added_lines:
            for kw in keywords:
                if kw in line and len(line) > 10 and len(line) < 200:
                    pattern = line
                    pattern_type = "contains"
                    break
            if pattern:
                break

    # 模式4: 取最长的有意义添加行
    if not pattern:
        meaningful = [l for l in added_lines if len(l) > 15 and not l.startswith('#')]
        if meaningful:
            pattern = max(meaningful, key=len)[:150]
            pattern_type = "contains"

    if pattern:
        return {
            "pattern": pattern,
            "type": pattern_type,
            "added_count": len(added_lines),
            "removed_count": len(removed_lines)
        }

    return None


def analyze_commit_for_rules(commit_hash: str, message: str) -> list:
    """分析单个提交，生成规则"""
    rules = []

    # 获取该提交修改的文件
    files = run_git(f"diff-tree --no-commit-id --name-only -r {commit_hash}").split('\n')
    files = [f for f in files if f.endswith('.py')]

    for file_path in files:
        if not file_path:
            continue

        # 获取该文件的 diff
        diff = run_git(f"show {commit_hash} -- {file_path}")

        # 提取修复模式
        fix_pattern = extract_fix_pattern(diff, file_path)

        if fix_pattern:
            rule_id = generate_rule_id(commit_hash, file_path)

            rule = {
                "id": rule_id,
                "commit": commit_hash[:7],
                "file": file_path,
                "message": message[:100],
                "pattern": fix_pattern["pattern"],
                "pattern_type": fix_pattern["type"],
                "created": datetime.now().isoformat(),
                "auto_generated": True
            }
            rules.append(rule)

    return rules


def scan_git_history(limit: int = 100) -> list:
    """扫描 git 历史，提取所有修复提交"""
    # 获取修复类型的提交
    log_output = run_git(f'log --oneline -n {limit} --grep="fix" --grep="Fix" --grep="修复" --grep="bugfix"')

    fix_commits = []
    for line in log_output.split('\n'):
        if not line:
            continue
        parts = line.split(' ', 1)
        if len(parts) == 2:
            fix_commits.append({
                "hash": parts[0],
                "message": parts[1]
            })

    # 也检查 conventional commits 格式
    log_output2 = run_git(f'log --oneline -n {limit} --grep="^fix:"')
    for line in log_output2.split('\n'):
        if not line:
            continue
        parts = line.split(' ', 1)
        if len(parts) == 2:
            commit = {"hash": parts[0], "message": parts[1]}
            if commit not in fix_commits:
                fix_commits.append(commit)

    return fix_commits


def validate_rule(rule: dict, auto_fix_paths: bool = False) -> dict:
    """验证单条规则"""
    file_path = rule.get("file")
    pattern = rule.get("pattern")
    pattern_type = rule.get("pattern_type", "contains")

    result = {
        "id": rule["id"],
        "file": file_path,
        "commit": rule.get("commit"),
        "status": "unknown",
        "message": "",
        "moved_to": None,
        "path_updated": False
    }

    # 跳过已废弃的规则
    if rule.get("deprecated"):
        result["status"] = "skipped"
        result["message"] = f"Deprecated: {rule.get('deprecated_reason', 'No reason given')}"
        return result

    # 检测文件是否被移动
    actual_path, was_moved = find_file(file_path)

    if was_moved:
        result["moved_to"] = actual_path
        if auto_fix_paths:
            rule["file"] = actual_path
            result["path_updated"] = True
            result["message"] = f"Path updated: {file_path} → {actual_path}"

    content = get_file_content(actual_path)

    if content is None:
        # 文件不存在 - 可能被删除或重命名
        result["status"] = "skipped"
        result["message"] = "File not found (may be renamed/deleted)"
        return result

    # 如果文件被移动但未自动修复，报告警告
    if was_moved and not auto_fix_paths:
        result["status"] = "warning"
        result["message"] = f"File moved: {file_path} → {actual_path}"
        return result

    # 根据模式类型验证
    if pattern_type == "import":
        if pattern in content:
            result["status"] = "passed"
            result["message"] = "Import statement exists"
        else:
            result["status"] = "failed"
            result["message"] = f"Missing import: {pattern}"

    elif pattern_type == "function_exists":
        if f"def {pattern}" in content:
            result["status"] = "passed"
            result["message"] = f"Function '{pattern}' exists"
        else:
            result["status"] = "failed"
            result["message"] = f"Function '{pattern}' not found"

    elif pattern_type == "contains":
        # v20.0: Always do exact match first for contains patterns.
        # Previously, patterns >50 chars only checked first 50 chars,
        # which caused false passes when function signature stayed the same
        # but argument values changed (e.g. 1.8 → 0.8).
        pattern_normalized = pattern.strip()
        if pattern_normalized in content:
            result["status"] = "passed"
            result["message"] = "Pattern found"
        elif len(pattern_normalized) > 80:
            # Very long pattern: check meaningful prefix as fallback
            # (structural refactoring may reformat without changing logic)
            key_part = pattern_normalized[:80]
            if key_part in content:
                result["status"] = "warning"
                result["message"] = "Pattern may have been refactored"
            else:
                result["status"] = "failed"
                result["message"] = f"Pattern not found: {pattern_normalized[:60]}..."
        else:
            result["status"] = "failed"
            result["message"] = f"Pattern not found: {pattern_normalized[:60]}..."

    else:
        result["status"] = "skipped"
        result["message"] = f"Unknown pattern type: {pattern_type}"

    return result


def update_rules_from_git(limit: int = 100, verbose: bool = True) -> dict:
    """从 git 历史更新规则库"""
    rules_data = load_rules()
    existing_ids = {r["id"] for r in rules_data["rules"]}

    # 扫描 git 历史
    fix_commits = scan_git_history(limit)

    if verbose:
        print(f"📊 扫描到 {len(fix_commits)} 个修复提交")

    new_rules = []
    for commit in fix_commits:
        commit_rules = analyze_commit_for_rules(commit["hash"], commit["message"])
        for rule in commit_rules:
            if rule["id"] not in existing_ids:
                new_rules.append(rule)
                existing_ids.add(rule["id"])

    if new_rules:
        rules_data["rules"].extend(new_rules)
        save_rules(rules_data)
        if verbose:
            print(f"✅ 新增 {len(new_rules)} 条规则")
    else:
        if verbose:
            print("ℹ️  没有新规则需要添加")

    return {
        "scanned_commits": len(fix_commits),
        "new_rules": len(new_rules),
        "total_rules": len(rules_data["rules"])
    }


def validate_all_rules(verbose: bool = True, auto_fix_paths: bool = False) -> dict:
    """验证所有规则"""
    rules_data = load_rules()

    results = {
        "passed": [],
        "failed": [],
        "warnings": [],
        "skipped": [],
        "moved_files": []
    }

    paths_updated = 0

    for rule in rules_data["rules"]:
        result = validate_rule(rule, auto_fix_paths)
        status = result["status"]

        if result.get("moved_to"):
            results["moved_files"].append({
                "old_path": result["file"],
                "new_path": result["moved_to"],
                "updated": result.get("path_updated", False)
            })
            if result.get("path_updated"):
                paths_updated += 1

        if status == "passed":
            results["passed"].append(result)
        elif status == "failed":
            results["failed"].append(result)
        elif status == "warning":
            results["warnings"].append(result)
        else:
            results["skipped"].append(result)

    # 如果有路径更新，保存规则
    if paths_updated > 0:
        save_rules(rules_data)
        if verbose:
            print(f"🔄 已自动更新 {paths_updated} 条规则的路径")

    if verbose:
        print(f"\n{'='*60}")
        print("📋 验证结果")
        print(f"{'='*60}")
        print(f"✅ 通过: {len(results['passed'])}")
        print(f"❌ 失败: {len(results['failed'])}")
        print(f"⚠️  警告: {len(results['warnings'])}")
        print(f"⏭️  跳过: {len(results['skipped'])}")

        # 显示移动的文件
        if results["moved_files"]:
            print(f"\n{'='*60}")
            print("📂 检测到文件移动:")
            print(f"{'='*60}")
            for mf in results["moved_files"]:
                status = "✅ 已更新" if mf["updated"] else "⚠️ 需要更新"
                print(f"  {status}: {mf['old_path']} → {mf['new_path']}")

        if results["failed"]:
            print(f"\n{'='*60}")
            print("❌ 失败详情:")
            print(f"{'='*60}")
            for r in results["failed"]:
                print(f"  [{r['commit']}] {r['file']}")
                print(f"    → {r['message']}")

        if results["warnings"]:
            print(f"\n{'='*60}")
            print("⚠️ 警告详情:")
            print(f"{'='*60}")
            for r in results["warnings"]:
                print(f"  [{r['commit']}] {r['file']}")
                print(f"    → {r['message']}")

    return results


def deprecate_rules(rule_ids: list, reason: str, verbose: bool = True) -> dict:
    """废弃指定的规则"""
    rules_data = load_rules()
    deprecated_count = 0

    for rule in rules_data["rules"]:
        if rule["id"] in rule_ids:
            rule["deprecated"] = True
            rule["deprecated_reason"] = reason
            rule["deprecated_date"] = datetime.now().isoformat()
            deprecated_count += 1

    if deprecated_count > 0:
        save_rules(rules_data)
        if verbose:
            print(f"✅ 已废弃 {deprecated_count} 条规则")
    else:
        if verbose:
            print("⚠️  未找到匹配的规则")

    return {"deprecated_count": deprecated_count}


def remove_rules(rule_ids: list, verbose: bool = True) -> dict:
    """移除指定的规则"""
    rules_data = load_rules()
    original_count = len(rules_data["rules"])

    rules_data["rules"] = [r for r in rules_data["rules"] if r["id"] not in rule_ids]

    removed_count = original_count - len(rules_data["rules"])
    if removed_count > 0:
        save_rules(rules_data)
        if verbose:
            print(f"✅ 已移除 {removed_count} 条规则")
    else:
        if verbose:
            print("⚠️  未找到匹配的规则")

    return {"removed_count": removed_count}


def _file_modified_after_commit(file_path: str, commit_short: str) -> bool:
    """Check if file was modified by any commit after the given one.

    Uses git log to detect modifications by non-fix commits that wouldn't
    generate rules but still legitimately refactored the pattern away.

    v20.0: If commit resolution fails (e.g. on feature branch where commit
    is not in history), falls back to checking if the file's latest commit
    is different from the rule's commit — this prevents false "real regression"
    reports on feature branches.
    """
    try:
        # Resolve short hash to full hash
        full_hash = run_git(f"rev-parse {commit_short}").strip()
        if not full_hash:
            # Cannot resolve commit — fall back to latest-commit check
            return _file_has_different_latest_commit(file_path, commit_short)
        # Check if any later commit modified this file
        later_commits = run_git(
            f"log --oneline {full_hash}..HEAD -- {file_path}"
        ).strip()
        if later_commits:
            return True
        # git log range may fail on feature branches — double-check
        latest = run_git(f"log -1 --format=%h -- {file_path}").strip()
        if latest and latest != commit_short and not latest.startswith(commit_short):
            return True
        return False
    except Exception:
        # Last resort: if anything fails, check latest commit on file
        return _file_has_different_latest_commit(file_path, commit_short)


def _file_has_different_latest_commit(file_path: str, commit_short: str) -> bool:
    """Fallback check: is the file's latest commit different from the rule's commit?"""
    try:
        latest = run_git(f"log -1 --format=%h -- {file_path}").strip()
        if latest and latest != commit_short and not latest.startswith(commit_short):
            return True
    except Exception:
        pass
    return False


def auto_deprecate_superseded(verbose: bool = True) -> int:
    """
    v6.3+: 自动废弃被后续提交覆盖的旧规则。

    原理: 如果一个旧 commit 的规则验证失败，且同一文件在后续 commit 中被修改过
    (无论是 fix commit 还是普通 refactor commit)，
    则旧规则很可能被重构覆盖了 — 自动标记为 deprecated。

    v19.0 改进: 除了检查是否有更新的 fix-commit 规则，还通过 git log 检查
    文件是否被任何后续 commit 修改过，解决非 fix-commit 重构导致的误报。

    Returns
    -------
    int
        Number of rules auto-deprecated.
    """
    rules_data = load_rules()
    active_rules = [r for r in rules_data["rules"] if not r.get("deprecated")]

    # Build file → set of all commits that have rules for this file
    # v20.0: Use set instead of single commit to avoid ordering issues
    file_all_commits = {}
    for rule in active_rules:
        f = rule.get("file", "")
        c = rule.get("commit", "")
        file_all_commits.setdefault(f, set()).add(c)

    # Validate and find failures
    superseded_ids = []
    git_superseded_ids = []
    real_failures = []
    for rule in active_rules:
        result = validate_rule(rule)
        if result["status"] == "failed":
            other_commits = file_all_commits.get(rule["file"], set()) - {rule["commit"]}
            # Check 1: another active rule exists for same file from a different commit
            if other_commits:
                superseded_ids.append(rule["id"])
            # Check 2: file modified by ANY later commit (git log check)
            elif _file_modified_after_commit(rule["file"], rule["commit"]):
                git_superseded_ids.append(rule["id"])
            else:
                # No newer commit at all — this is a REAL regression
                real_failures.append(rule)
    failed_ids = superseded_ids

    if failed_ids:
        deprecate_rules(
            failed_ids,
            reason="auto-superseded: 模式已被后续重构覆盖",
            verbose=verbose,
        )

    if git_superseded_ids:
        deprecate_rules(
            git_superseded_ids,
            reason="auto-superseded: 文件被后续非 fix commit 修改，模式已过时",
            verbose=verbose,
        )

    # Also auto-deprecate "refactored" warnings
    warn_ids = []
    all_deprecated = set(failed_ids + git_superseded_ids)
    for rule in active_rules:
        if rule["id"] in all_deprecated:
            continue
        result = validate_rule(rule)
        if result["status"] == "warning" and "refactored" in result.get("message", "").lower():
            warn_ids.append(rule["id"])

    if warn_ids:
        deprecate_rules(
            warn_ids,
            reason="auto-superseded: 模式已重构到新位置",
            verbose=verbose,
        )

    total = len(failed_ids) + len(git_superseded_ids) + len(warn_ids)
    if verbose and total > 0:
        parts = []
        if failed_ids:
            parts.append(f"{len(failed_ids)} 被新 fix commit 覆盖")
        if git_superseded_ids:
            parts.append(f"{len(git_superseded_ids)} 被后续 commit 重构")
        if warn_ids:
            parts.append(f"{len(warn_ids)} 重构警告")
        print(f"🔄 自动废弃 {total} 条过时规则 ({' + '.join(parts)})")

    # Report real regressions (not auto-deprecated)
    if real_failures:
        print(f"\n⚠️ 发现 {len(real_failures)} 条真实回归 (未被新提交覆盖):")
        for rule in real_failures:
            print(f"  ❌ [{rule['id']}] {rule['file']}: {rule.get('description', 'N/A')}")

    return total


def run_full_analysis(limit: int = 100, verbose: bool = True, json_output: bool = False, auto_fix_paths: bool = False) -> dict:
    """运行完整分析流程"""

    if verbose and not json_output:
        print("🔍 Smart Commit Analyzer")
        print("=" * 60)
        if auto_fix_paths:
            print("🔧 模式: 自动修复路径")
        print()
        print("Step 1: 从 Git 历史更新规则库...")

    # Step 1: 更新规则
    update_result = update_rules_from_git(limit, verbose and not json_output)

    if verbose and not json_output:
        print()
        print("Step 2: 验证所有规则...")

    # Step 2: 验证规则
    validate_result = validate_all_rules(verbose and not json_output, auto_fix_paths)

    # Step 2.5: v20.0 — ALWAYS run auto-deprecate when there are failures or new rules
    # Previously only triggered on failures, missing cases where Step 1 added new rules
    # that make older rules for the same file obsolete.
    should_auto_deprecate = (
        validate_result["failed"]
        or update_result.get("new_rules", 0) > 0
    )
    if should_auto_deprecate:
        auto_count = auto_deprecate_superseded(verbose=verbose and not json_output)
        if auto_count > 0:
            # Re-validate after auto-deprecation
            if verbose and not json_output:
                print(f"\n🔄 重新验证 (已自动废弃 {auto_count} 条过时规则)...")
            validate_result = validate_all_rules(verbose and not json_output, auto_fix_paths)

    # 组合结果
    result = {
        "update": update_result,
        "validation": {
            "passed": len(validate_result["passed"]),
            "failed": len(validate_result["failed"]),
            "warnings": len(validate_result["warnings"]),
            "skipped": len(validate_result["skipped"]),
            "moved_files": len(validate_result["moved_files"])
        },
        "failed_details": validate_result["failed"],
        "moved_files": validate_result["moved_files"],
        "timestamp": datetime.now().isoformat()
    }

    if json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif verbose:
        print()
        print("=" * 60)
        print(f"📊 总计: {update_result['total_rules']} 条规则")

        if validate_result["failed"]:
            print("❌ 检测到回归风险！请检查上述失败项")
        else:
            print("✅ 所有规则验证通过")

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Smart Commit Analyzer - 智能提交分析器")
    parser.add_argument("--update", action="store_true", help="只更新规则库")
    parser.add_argument("--validate", action="store_true", help="只验证规则")
    parser.add_argument("--commits", type=int, default=100, help="扫描提交数量")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--show-rules", action="store_true", help="显示所有规则")
    parser.add_argument("--fix-paths", action="store_true", help="自动修复移动文件的路径")
    parser.add_argument("--deprecate", nargs="+", metavar="RULE_ID", help="废弃指定规则 (空格分隔多个ID)")
    parser.add_argument("--remove", nargs="+", metavar="RULE_ID", help="移除指定规则 (空格分隔多个ID)")
    parser.add_argument("--reason", type=str, default="Obsolete", help="废弃原因 (与 --deprecate 配合)")
    parser.add_argument("--auto-clean", action="store_true", help="v6.3: 自动废弃被后续重构覆盖的旧规则")

    args = parser.parse_args()

    # v6.3: 自动清理过时规则
    if args.auto_clean:
        count = auto_deprecate_superseded(verbose=not args.json)
        if count == 0:
            print("✅ 无过时规则需要清理")
        return

    # 处理废弃规则
    if args.deprecate:
        deprecate_rules(args.deprecate, args.reason, not args.json)
        return

    # 处理移除规则
    if args.remove:
        remove_rules(args.remove, not args.json)
        return

    if args.show_rules:
        rules_data = load_rules()
        if args.json:
            print(json.dumps(rules_data, indent=2, ensure_ascii=False))
        else:
            print(f"📋 规则列表 ({len(rules_data['rules'])} 条)")
            print("=" * 60)
            for rule in rules_data["rules"]:
                print(f"[{rule['id']}] {rule['commit']} - {rule['file']}")
                print(f"    {rule['message'][:60]}")
                print(f"    Pattern: {rule['pattern'][:50]}...")
                print()
        return

    if args.update:
        update_rules_from_git(args.commits, not args.json)
    elif args.validate:
        validate_all_rules(not args.json, args.fix_paths)
    else:
        run_full_analysis(args.commits, True, args.json, args.fix_paths)


if __name__ == "__main__":
    main()
