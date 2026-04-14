#!/usr/bin/env python3
"""
依赖分析工具 - 分析项目内部的 import 依赖关系

功能:
1. 扫描所有 Python 文件的 import 语句
2. 构建模块依赖图
3. 检测循环依赖
4. 检测引用已移动/删除的模块
5. 输出依赖报告

使用:
  python3 scripts/analyze_dependencies.py              # 完整分析
  python3 scripts/analyze_dependencies.py --check      # 只检查问题
  python3 scripts/analyze_dependencies.py --graph      # 输出依赖图 (DOT 格式)
  python3 scripts/analyze_dependencies.py --json       # JSON 输出
"""

import ast
import os
import sys
import json
import argparse
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Set, Tuple, Optional


def get_project_root() -> Path:
    """获取项目根目录"""
    return Path(__file__).parent.parent


def get_python_files(root: Path) -> List[Path]:
    """获取所有 Python 文件"""
    exclude_dirs = {'venv', '.venv', '__pycache__', '.git', 'node_modules', '.tox'}
    files = []

    for path in root.rglob('*.py'):
        # 跳过排除的目录
        if any(excluded in path.parts for excluded in exclude_dirs):
            continue
        files.append(path)

    return files


def extract_imports(filepath: Path, project_root: Path) -> Dict:
    """从文件中提取 import 语句"""
    try:
        content = filepath.read_text(encoding='utf-8', errors='ignore')
        tree = ast.parse(content, filename=str(filepath))
    except SyntaxError as e:
        return {'error': str(e), 'imports': [], 'from_imports': []}

    imports = []
    from_imports = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({
                    'module': alias.name,
                    'alias': alias.asname,
                    'line': node.lineno
                })

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            for alias in node.names:
                from_imports.append({
                    'module': module,
                    'name': alias.name,
                    'alias': alias.asname,
                    'line': node.lineno,
                    'level': node.level  # 相对导入级别
                })

    return {
        'file': str(filepath.relative_to(project_root)),
        'imports': imports,
        'from_imports': from_imports
    }


def is_internal_module(module_name: str, project_modules: Set[str]) -> bool:
    """判断是否是项目内部模块"""
    # 检查完整模块名或前缀
    parts = module_name.split('.')
    for i in range(len(parts), 0, -1):
        prefix = '.'.join(parts[:i])
        if prefix in project_modules:
            return True
    return False


def module_to_path(module_name: str, project_root: Path) -> Optional[Path]:
    """将模块名转换为文件路径"""
    # 尝试作为包
    package_path = project_root / module_name.replace('.', '/') / '__init__.py'
    if package_path.exists():
        return package_path

    # 尝试作为模块
    module_path = project_root / (module_name.replace('.', '/') + '.py')
    if module_path.exists():
        return module_path

    return None


def build_dependency_graph(all_imports: List[Dict], project_modules: Set[str]) -> Dict:
    """构建依赖图"""
    graph = defaultdict(set)  # file -> set of dependencies

    for file_data in all_imports:
        if 'error' in file_data:
            continue

        source_file = file_data['file']

        # 处理 import 语句
        for imp in file_data['imports']:
            module = imp['module']
            if is_internal_module(module, project_modules):
                graph[source_file].add(module)

        # 处理 from ... import 语句
        for imp in file_data['from_imports']:
            module = imp['module']
            if module and is_internal_module(module, project_modules):
                graph[source_file].add(module)

    return dict(graph)


def detect_circular_dependencies(graph: Dict) -> List[List[str]]:
    """检测循环依赖"""
    # 将文件路径转换为模块名
    file_to_module = {}
    for file in graph.keys():
        module = file.replace('/', '.').replace('.py', '').replace('.__init__', '')
        file_to_module[file] = module

    # 构建模块级别的图
    module_graph = defaultdict(set)
    for file, deps in graph.items():
        source_module = file_to_module.get(file, file)
        for dep in deps:
            module_graph[source_module].add(dep)

    # DFS 检测环
    cycles = []
    visited = set()
    rec_stack = []

    def dfs(node: str, path: List[str]):
        if node in rec_stack:
            # 找到环
            cycle_start = rec_stack.index(node)
            cycle = rec_stack[cycle_start:] + [node]
            if cycle not in cycles:
                cycles.append(cycle)
            return

        if node in visited:
            return

        visited.add(node)
        rec_stack.append(node)

        for neighbor in module_graph.get(node, []):
            dfs(neighbor, path + [neighbor])

        rec_stack.pop()

    for node in module_graph:
        dfs(node, [node])

    return cycles


def check_missing_modules(all_imports: List[Dict], project_root: Path, project_modules: Set[str]) -> List[Dict]:
    """检查引用了不存在的模块"""
    missing = []

    for file_data in all_imports:
        if 'error' in file_data:
            continue

        source_file = file_data['file']

        # 检查 from ... import
        for imp in file_data['from_imports']:
            module = imp['module']
            if not module:
                continue

            # 只检查内部模块
            if not is_internal_module(module, project_modules):
                continue

            # 检查模块是否存在
            if module_to_path(module, project_root) is None:
                missing.append({
                    'file': source_file,
                    'line': imp['line'],
                    'module': module,
                    'import_name': imp['name']
                })

    return missing


def generate_dot_graph(graph: Dict) -> str:
    """生成 DOT 格式的依赖图"""
    lines = ['digraph Dependencies {']
    lines.append('  rankdir=LR;')
    lines.append('  node [shape=box];')
    lines.append('')

    # 简化文件名
    def simplify(name: str) -> str:
        return name.replace('/', '_').replace('.py', '').replace('.', '_')

    for source, deps in graph.items():
        source_id = simplify(source)
        for dep in deps:
            dep_id = simplify(dep)
            lines.append(f'  "{source_id}" -> "{dep_id}";')

    lines.append('}')
    return '\n'.join(lines)


def run_analysis(check_only: bool = False, output_graph: bool = False, json_output: bool = False) -> Dict:
    """运行完整分析"""
    project_root = get_project_root()

    # 获取所有 Python 文件
    python_files = get_python_files(project_root)

    # 构建项目模块集合
    project_modules = set()
    for f in python_files:
        rel_path = f.relative_to(project_root)
        module = str(rel_path).replace('/', '.').replace('.py', '').replace('.__init__', '')
        project_modules.add(module)
        # 也添加父模块
        parts = module.split('.')
        for i in range(1, len(parts)):
            project_modules.add('.'.join(parts[:i]))

    # 提取所有 import
    all_imports = []
    for f in python_files:
        imports = extract_imports(f, project_root)
        all_imports.append(imports)

    # 构建依赖图
    graph = build_dependency_graph(all_imports, project_modules)

    # 检测循环依赖
    cycles = detect_circular_dependencies(graph)

    # 检测缺失模块
    missing = check_missing_modules(all_imports, project_root, project_modules)

    # 统计
    total_files = len(python_files)
    total_imports = sum(len(d.get('imports', [])) + len(d.get('from_imports', [])) for d in all_imports)
    internal_deps = sum(len(deps) for deps in graph.values())

    result = {
        'summary': {
            'total_files': total_files,
            'total_imports': total_imports,
            'internal_dependencies': internal_deps,
            'circular_dependencies': len(cycles),
            'missing_modules': len(missing)
        },
        'issues': {
            'circular_dependencies': cycles,
            'missing_modules': missing
        },
        'graph': graph
    }

    if json_output:
        # 转换 set 为 list 以便 JSON 序列化
        result['graph'] = {k: list(v) for k, v in graph.items()}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result

    if output_graph:
        print(generate_dot_graph(graph))
        return result

    # 打印报告
    print("=" * 60)
    print("📊 依赖分析报告")
    print("=" * 60)
    print(f"📁 扫描文件: {total_files}")
    print(f"📦 总 import 数: {total_imports}")
    print(f"🔗 内部依赖: {internal_deps}")
    print(f"🔄 循环依赖: {len(cycles)}")
    print(f"❌ 缺失模块: {len(missing)}")

    if cycles:
        print(f"\n{'=' * 60}")
        print("🔄 循环依赖详情:")
        print("=" * 60)
        for i, cycle in enumerate(cycles, 1):
            print(f"  {i}. {' → '.join(cycle)}")

    if missing:
        print(f"\n{'=' * 60}")
        print("❌ 缺失模块详情:")
        print("=" * 60)
        for m in missing:
            print(f"  [{m['file']}:{m['line']}] from {m['module']} import {m['import_name']}")

    if not cycles and not missing:
        print(f"\n✅ 未发现依赖问题")
    else:
        print(f"\n⚠️ 发现 {len(cycles) + len(missing)} 个问题")

    return result


def main():
    parser = argparse.ArgumentParser(description="依赖分析工具")
    parser.add_argument("--check", action="store_true", help="只检查问题")
    parser.add_argument("--graph", action="store_true", help="输出 DOT 格式依赖图")
    parser.add_argument("--json", action="store_true", help="JSON 输出")

    args = parser.parse_args()

    result = run_analysis(
        check_only=args.check,
        output_graph=args.graph,
        json_output=args.json
    )

    # 如果有问题，返回非零退出码
    if result['summary']['circular_dependencies'] > 0 or result['summary']['missing_modules'] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
