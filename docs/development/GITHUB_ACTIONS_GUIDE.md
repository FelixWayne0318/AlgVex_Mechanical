# GitHub Actions 和 CI/CD 开发指南

本文档提供 AlgVex 项目中 GitHub Actions 工作流、CodeQL 安全扫描和 CI/CD 相关的完整指南。

## 目录

1. [GitHub Actions 工作流概述](#github-actions-工作流概述)
2. [Code Scanning (CodeQL) 配置和使用](#code-scanning-codeql-配置和使用)
3. [Code Scanning Alerts 访问方法](#code-scanning-alerts-访问方法)
4. [自定义 CodeQL 查询编写](#自定义-codeql-查询编写)
5. [Commit Analysis 工作流](#commit-analysis-工作流)
6. [权限配置和 Secrets 管理](#权限配置和-secrets-管理)
7. [故障排除](#故障排除)

---

## GitHub Actions 工作流概述

AlgVex 使用多个自动化工作流来确保代码质量和安全性。

### 核心工作流

| 工作流 | 文件 | 触发条件 | 用途 |
|--------|------|----------|------|
| **CodeQL Analysis** | `.github/workflows/codeql-analysis.yml` | Push 到 main, PR, 每周一 | 安全扫描和代码质量检查 |
| **Commit Analysis** | `.github/workflows/commit-analysis.yml` | Push, PR | 智能回归检测 + AI 分析 + 依赖分析 |
| **Claude Code** | `.github/workflows/claude.yml` | PR 评论中 `@claude` | AI 代码助手和自动化 |
| **Dependency Submission** | `.github/workflows/dependency-submission.yml` | Push 到 main | 提交 Python 依赖图到 GitHub |

### 工作流权限

每个工作流都有明确的权限声明，遵循最小权限原则：

**CodeQL Analysis** (`.github/workflows/codeql-analysis.yml:13-16`):
```yaml
permissions:
  actions: read
  contents: read
  security-events: write  # 允许上传扫描结果
```

**Claude Code** (`.github/workflows/claude.yml:9-17`):
```yaml
permissions:
  contents: write         # 读写代码
  pull-requests: write    # 管理 PR
  issues: write           # 管理 Issue
  actions: read           # 读取 Actions 日志
  security-events: read   # 读取安全扫描结果
  checks: write           # 更新 CI 检查状态
  statuses: write         # 更新 commit 状态
```

---

## Code Scanning (CodeQL) 配置和使用

CodeQL 是 GitHub 的语义代码分析引擎，可以发现安全漏洞和代码质量问题。

### 配置文件

**主配置**: `.github/workflows/codeql-analysis.yml`

**关键配置项**:
```yaml
strategy:
  matrix:
    language: ['python']  # 扫描 Python 代码

init:
  languages: python
  queries: +security-extended,security-and-quality,./.github/codeql/custom-queries
  # security-extended: 扩展安全规则集
  # security-and-quality: 安全 + 代码质量规则
  # custom-queries: 项目自定义查询
```

### 扫描内容

CodeQL 会检测以下问题：

**安全漏洞**:
- SQL 注入
- 命令注入
- XSS (跨站脚本)
- 路径遍历
- 不安全的反序列化
- 硬编码凭证
- 等等 ([完整列表](https://codeql.github.com/codeql-query-help/python/))

**代码质量**:
- 未使用的导入和变量
- 空 except 块
- 循环导入
- 类型不匹配
- 资源泄漏
- 等等

### 自定义查询

项目在 `.github/codeql/custom-queries/` 中定义了专用查询：

| 查询文件 | 用途 | 严重程度 |
|---------|------|---------|
| `find-imports.ql` | 追踪所有 import 语句，分析依赖关系 | recommendation |
| `hardcoded-secrets.ql` | 检测硬编码的 API key 和密码 | error |
| `bare-except.ql` | 检测裸 except 块 (会捕获 KeyboardInterrupt) | warning |
| `thread-unsafe-indicators.ql` | 检测不安全的 Rust 指标导入 | error |
| `config-bypass.ql` | 检测绕过 ConfigManager 的硬编码参数 | warning |

**查询套件配置**: `.github/codeql/custom-queries/suite.qls`
```yaml
- description: AlgVex custom security and quality checks
- queries: '.'
- from: codeql/python-queries
  queries:
    - include:
        kind: problem
```

---

## Code Scanning Alerts 访问方法

### 问题背景

GitHub Actions 的默认 `GITHUB_TOKEN` 对 Code Scanning Alerts API 有严格限制：

```bash
# ❌ 直接 API 访问失败
gh api repos/OWNER/REPO/code-scanning/alerts
# 错误: Resource not accessible by integration (HTTP 403)
```

**原因**: GitHub 安全设计，防止 CI/CD 自动访问安全扫描结果，需要更高权限的认证方式。

参考: [GitHub Community Discussion #60612](https://github.com/orgs/community/discussions/60612)

---

### 解决方案

#### 方法 1: 使用 SARIF Artifact ⭐ **推荐用于自动化**

CodeQL 会生成 SARIF (Static Analysis Results Interchange Format) 文件，包含完整的扫描结果。

**下载 SARIF 文件**:
```bash
# 1. 获取最新的 CodeQL 运行 ID
RUN_ID=$(gh api repos/OWNER/REPO/actions/workflows/codeql-analysis.yml/runs \
  --jq '.workflow_runs[0].id')

# 2. 下载 SARIF artifact
gh run download $RUN_ID -n codeql-sarif

# 3. 解析告警
jq -r '.runs[0].results[] | "\(.ruleId): \(.message.text)"' python.sarif
```

**详细解析示例**:
```bash
# 统计告警类型
jq '.runs[0].results | group_by(.ruleId) | map({rule: .[0].ruleId, count: length}) | sort_by(-.count)' python.sarif

# 提取特定告警的代码位置
jq -r '.runs[0].results[] |
  select(.ruleId == "py/unused-import") |
  "\(.locations[0].physicalLocation.artifactLocation.uri):\(.locations[0].physicalLocation.region.startLine)"' python.sarif

# 导出为 CSV
jq -r '.runs[0].results[] |
  [.ruleId, .message.text, .locations[0].physicalLocation.artifactLocation.uri, .locations[0].physicalLocation.region.startLine] |
  @csv' python.sarif > alerts.csv
```

**优点**:
- ✅ 无需额外权限
- ✅ 包含完整告警详情（比 API 更详细）
- ✅ 包含代码位置、行号、修复建议
- ✅ 完全自动化

**缺点**:
- ⏳ 需要等待 artifact 上传完成
- ⏳ 只能访问当前工作流生成的结果
- ⏳ 无法查看历史告警趋势

---

#### 方法 2: Fine-Grained Personal Access Token (PAT) ⭐ **用于交互式访问**

如果需要直接通过 API 访问，可以使用 Fine-Grained PAT。

**创建 PAT**:
1. 访问 https://github.com/settings/tokens?type=beta
2. 点击 "Generate new token (fine-grained)"
3. 配置:
   - **Token name**: `AlgVex Code Scanning Reader`
   - **Expiration**: 90 天（推荐定期轮换）
   - **Repository access**: `Only select repositories` → 选择你的仓库
   - **Permissions (Repository)**:
     - `Code scanning alerts`: **Read-only** ✅
     - `Contents`: **Read-only**
     - `Pull requests`: **Read and write**

**添加到 Repository Secrets**:
1. 访问 `https://github.com/OWNER/REPO/settings/secrets/actions`
2. 点击 "New repository secret"
   - **Name**: `CODE_SCANNING_PAT`
   - **Value**: 粘贴刚创建的 PAT

**在 Workflow 中使用**:
```yaml
# .github/workflows/your-workflow.yml
jobs:
  analyze:
    steps:
      - name: Check Code Scanning Alerts
        env:
          GH_TOKEN: ${{ secrets.CODE_SCANNING_PAT || secrets.GITHUB_TOKEN }}
        run: |
          gh api repos/${{ github.repository }}/code-scanning/alerts \
            --jq '.[] | "\(.rule.id): \(.most_recent_instance.location.path):\(.most_recent_instance.location.start_line)"'
```

**优点**:
- ✅ 可以直接使用 GitHub API
- ✅ 适合交互式脚本和手动检查
- ✅ 可以查看历史告警趋势

**缺点**:
- ⚠️ 需要手动创建和管理令牌
- ⚠️ 令牌过期后需要更新 Secret
- ⚠️ 是用户级令牌，如果用户离开组织会失效

**参考资料**:
- [Fine-grained personal access tokens](https://github.blog/security/application-security/introducing-fine-grained-personal-access-tokens-for-github/)
- [Permissions required for fine-grained PATs](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)

---

#### 方法 3: GitHub Web UI ⭐ **用于人工审查**

最简单的方式是直接访问 GitHub 的 Security 标签页。

**访问地址**:
```
https://github.com/OWNER/REPO/security/code-scanning
```

**功能**:
- ✅ 完整的 UI 体验（可过滤、排序、查看历史）
- ✅ 显示告警趋势图
- ✅ 可以标记为 false positive 或已修复
- ✅ 支持分支比较

**缺点**:
- ❌ 完全手动，无法集成到 CI/CD
- ❌ 不适合自动化脚本

---

#### 方法 4: 创建 GitHub App (企业级方案)

对于大型团队或企业项目，可以创建具有完整权限的 GitHub App。

**步骤**:
1. 访问 https://github.com/settings/apps/new
2. 配置权限:
   - `Code scanning alerts`: **Read-only**
   - `Contents`: **Read-only**
   - `Pull requests`: **Read and write**
3. 安装到仓库
4. 在 workflow 中使用 `actions/create-github-app-token@v1`

**优点**:
- ✅ 不依赖个人账户（组织级资源）
- ✅ 更细粒度的权限控制
- ✅ 更好的审计日志

**缺点**:
- ⚠️ 配置复杂
- ⚠️ 需要组织管理员权限创建 App
- ⚠️ 对个人仓库来说过于重量级

---

### 方案对比

| 场景 | 推荐方案 | 理由 |
|------|---------|------|
| **CI/CD 自动化** | 方法 1 (SARIF Artifact) | 无需额外权限，完全自动化 |
| **交互式脚本** | 方法 2 (Fine-Grained PAT) | 直接 API 访问，更灵活 |
| **人工审查** | 方法 3 (Web UI) | 最直观，功能最完整 |
| **企业/团队项目** | 方法 4 (GitHub App) | 组织级管理，更安全 |

---

## 自定义 CodeQL 查询编写

### 查询结构

CodeQL 查询由 QL 语言编写，基本结构如下：

```ql
/**
 * @name 查询名称
 * @description 查询描述
 * @kind problem                    # 查询类型: problem, path-problem, metric
 * @problem.severity warning        # 严重程度: error, warning, recommendation
 * @id your-org/query-id            # 唯一标识符
 * @tags security                   # 标签: security, correctness, maintainability
 *       external/cwe/cwe-79        # CWE 分类
 */

import python

from <声明变量>
where
  <条件表达式>
select <结果>, <消息>
```

### 常用 Python API

| API | 用途 | 示例 |
|-----|------|------|
| `ImportingStmt` | 所有 import 语句 | `from ImportingStmt imp` |
| `ImportingStmt.getAnImportedModuleName()` | 获取导入的模块名 | `imp.getAnImportedModuleName() = "os"` |
| `StringLiteral` | 字符串字面量 | `from StringLiteral str` |
| `StringLiteral.getText()` | 获取字符串内容 | `str.getText() = "secret_key"` |
| `ExceptStmt` | except 语句 | `from ExceptStmt except` |
| `ExceptStmt.getType()` | except 捕获的异常类型 | `not exists(except.getType())` (裸 except) |
| `File.getRelativePath()` | 文件相对路径 | `file.getRelativePath().matches("%test%")` |

**完整 API 文档**: https://codeql.github.com/codeql-standard-libraries/python/

### 示例 1: 检测硬编码的 API 端点

```ql
import python

from StringLiteral str
where
  exists(string value |
    value = str.getText() and
    value.regexpMatch("https?://api\\.(binance|telegram)\\..*")
  )
  and not str.getLocation().getFile().getRelativePath().matches("%test%")
select str, "Hardcoded API endpoint - consider using environment variable"
```

### 示例 2: 检测未使用 ConfigManager 的配置

```ql
import python

from Assign assign, Name target
where
  target = assign.getATarget() and
  // 检测变量名包含配置相关关键字
  target.getId().regexpMatch("(?i).*(config|setting|param|threshold|limit).*") and
  // 不在 ConfigManager 或测试文件中
  not assign.getScope().getEnclosingModule().getName().matches("%config%") and
  not assign.getLocation().getFile().getRelativePath().matches("%test%")
select assign, "Configuration variable '" + target.getId() + "' - consider using ConfigManager"
```

### 查询最佳实践

1. **使用 exists** 限定变量作用域：
   ```ql
   where
     exists(string value |
       value = str.getText() and
       value.length() > 20
     )
   ```

2. **排除测试文件**：
   ```ql
   and not str.getLocation().getFile().getRelativePath().matches("%test%")
   ```

3. **使用正则表达式匹配**：
   ```ql
   value.regexpMatch("^[A-Za-z0-9]{20,}$")
   ```

4. **提供清晰的错误消息**：
   ```ql
   select str, "Hardcoded secret detected: " + value.substring(0, 20) + "..."
   ```

### 测试查询

**本地测试**:
```bash
# 安装 CodeQL CLI
# https://github.com/github/codeql-cli-binaries/releases

# 创建数据库
codeql database create python-db --language=python

# 运行查询
codeql query run .github/codeql/custom-queries/your-query.ql \
  --database=python-db

# 生成 SARIF
codeql database analyze python-db \
  .github/codeql/custom-queries/ \
  --format=sarif-latest \
  --output=results.sarif
```

---

## Commit Analysis 工作流

### 工作流概述

`.github/workflows/commit-analysis.yml` 运行三个独立的分析工具：

| Job | 工具 | 功能 |
|-----|------|------|
| **Smart Regression Detection** | `scripts/smart_commit_analyzer.py` | 自动从 git 历史生成回归规则 |
| **Dependency Analysis** | `scripts/analyze_dependencies.py` | Python AST 依赖分析，循环导入检测 |

### Smart Regression Detection

**原理**: 分析 git 历史中的 "fix:" 提交，自动提取回归规则。

**示例规则生成**:
```
Commit: fix: use nautilus_trader.indicators instead of nautilus_pyo3
→ 生成规则:
  - 不应该从 nautilus_trader.core.nautilus_pyo3 导入指标
  - 应该使用 nautilus_trader.indicators
```

**运行**:
```bash
# 分析最近 100 个提交
python3 scripts/smart_commit_analyzer.py --commits 100

# 只更新规则库
python3 scripts/smart_commit_analyzer.py --update

# 只验证规则
python3 scripts/smart_commit_analyzer.py --validate

# 查看所有规则
python3 scripts/smart_commit_analyzer.py --show-rules

# JSON 输出 (用于 CI/CD)
python3 scripts/smart_commit_analyzer.py --json
```

### Dependency Analysis

**功能**:
- 使用 Python AST 解析导入语句
- 检测循环依赖
- 发现缺失的模块
- 生成依赖图

**运行**:
```bash
python3 scripts/analyze_dependencies.py
```

---

## 权限配置和 Secrets 管理

### Repository Secrets

**敏感信息必须存储在 Secrets 中**，不能硬编码在代码或配置文件。

**必需的 Secrets**:

| Secret 名称 | 用途 | 获取方式 |
|------------|------|---------|
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude Code Action 认证 | [Claude Code 设置](https://code.claude.com/) |
| `CODE_SCANNING_PAT` | 访问 Code Scanning API (可选) | [创建 Fine-Grained PAT](#方法-2-fine-grained-personal-access-token-pat-用于交互式访问) |
| `DEEPSEEK_API_KEY` | AI 提交分析 (可选) | [DeepSeek 官网](https://platform.deepseek.com/) |

**添加 Secret**:
1. 访问 `https://github.com/OWNER/REPO/settings/secrets/actions`
2. 点击 "New repository secret"
3. 输入名称和值
4. 点击 "Add secret"

### 在 Workflow 中使用 Secrets

```yaml
jobs:
  example:
    steps:
      - name: Use Secret
        env:
          API_KEY: ${{ secrets.YOUR_SECRET_NAME }}
        run: |
          # Secret 会自动脱敏在日志中显示为 ***
          echo "API Key: $API_KEY"
```

**安全建议**:
- ✅ 使用 Fine-Grained PAT 而不是 Classic PAT
- ✅ 设置最短过期时间（建议 90 天）
- ✅ 定期轮换 Secrets
- ✅ 使用最小权限原则
- ❌ 不要在日志中打印 Secrets
- ❌ 不要在 PR 评论中泄露 Secrets

---

## 故障排除

### CodeQL 常见错误

#### 错误 1: 查询编译失败

**错误信息**:
```
ERROR: 'value' is not bound to a value.
```

**原因**: 变量声明和使用不匹配

**解决**:
```ql
# ❌ 错误
from StrConst str, string value
where value = str.getText()

# ✅ 正确
from StringLiteral str
where exists(string value | value = str.getText() and ...)
```

#### 错误 2: 类型已废弃

**错误信息**:
```
WARNING: type 'StrConst' has been deprecated
```

**解决**: 使用新的 API
- `StrConst` → `StringLiteral`
- `ImportingStmt.getAName()` → `ImportingStmt.getAnImportedModuleName()`

#### 错误 3: 查询超时

**错误信息**:
```
Query timed out after 300 seconds
```

**解决**:
1. 优化查询逻辑，减少 `exists` 嵌套
2. 添加更多过滤条件 (如排除特定目录)
3. 使用索引属性 (如 `getName()` 比 `getText()` 快)

---

### Code Scanning API 403 错误

**错误信息**:
```
gh: Resource not accessible by integration (HTTP 403)
```

**原因**: 默认 `GITHUB_TOKEN` 无权访问 Code Scanning Alerts API

**解决**: 参见 [Code Scanning Alerts 访问方法](#code-scanning-alerts-访问方法)

---

### Workflow 权限不足

**错误信息**:
```
Resource not accessible by integration
```

**原因**: Workflow 权限配置不足

**解决**: 在 workflow 文件中添加权限声明
```yaml
permissions:
  contents: write
  pull-requests: write
  security-events: write
```

---

### Secret 未设置

**错误信息**:
```
Error: Input required and not supplied: YOUR_SECRET_NAME
```

**解决**: 在 Repository Settings → Secrets and variables → Actions 中添加 Secret

---

## 参考资料

### 官方文档

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [CodeQL Documentation](https://codeql.github.com/docs/)
- [Code Scanning Documentation](https://docs.github.com/en/code-security/code-scanning)
- [SARIF Specification](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html)

### CodeQL 资源

- [CodeQL for Python](https://codeql.github.com/docs/codeql-language-guides/codeql-library-for-python/)
- [CodeQL Query Help (Python)](https://codeql.github.com/codeql-query-help/python/)
- [CodeQL Standard Libraries](https://codeql.github.com/codeql-standard-libraries/python/)

### 安全最佳实践

- [GitHub Security Best Practices](https://docs.github.com/en/code-security/getting-started/github-security-features)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [CWE Database](https://cwe.mitre.org/)

---

## 联系和支持

如果遇到 CI/CD 相关问题：

1. 检查本文档的[故障排除](#故障排除)章节
2. 查看 GitHub Actions 运行日志
3. 搜索 [GitHub Community Discussions](https://github.com/orgs/community/discussions)
4. 在项目 Issue 中提问并 @claude
