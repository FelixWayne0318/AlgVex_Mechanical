# 安全配置指南 (Security Guide)

## 🔒 保护 API Keys 的最佳实践

### `.gitignore` 的作用范围

**重要理解：**
- ✅ `.gitignore` **只保护 Git 仓库**：防止 `.env` 文件被提交到 GitHub/GitLab 等代码仓库
- ❌ `.gitignore` **不能保护云服务器上的文件**：如果你的 `.env` 已经在服务器上，需要通过其他方式保护

### 云服务器上保护 `.env` 文件的方法

#### 方法 1: 设置正确的文件权限（推荐）

在云服务器上执行：

```bash
# 1. 创建 .env 文件（如果还没有）
cp .env.template .env
nano .env  # 填入你的真实 API keys

# 2. 设置文件权限：只有所有者可以读写，其他人无法访问
chmod 600 .env

# 3. 验证权限设置
ls -la .env
# 应该显示: -rw------- 1 user user 1234 date .env

# 4. 确保 .env 的父目录权限也正确
chmod 755 $(dirname $(realpath .env))
```

**解释：**
- `chmod 600`: 只有文件所有者可以读写，其他人完全无法访问
- 这样即使有人能登录服务器，也不能读取你的密钥文件

#### 方法 2: 使用环境变量（生产环境推荐）

不需要 `.env` 文件，直接在系统环境中设置：

```bash
# 在服务器上设置环境变量
export BINANCE_API_KEY="your_real_key"
export BINANCE_API_SECRET="your_real_secret"
# DEEPSEEK_API_KEY — v49.0 mechanical mode 不再需要

# 或者使用 .bashrc 或 .bash_profile（仅限你的用户）
echo 'export BINANCE_API_KEY="your_real_key"' >> ~/.bashrc
echo 'export BINANCE_API_SECRET="your_real_secret"' >> ~/.bashrc
echo '# DEEPSEEK_API_KEY — v49.0 mechanical mode 不再需要' >> ~/.bashrc
source ~/.bashrc
```

**优点：**
- 不会在文件系统中留下密钥文件
- 更安全，因为密钥只在内存中

**注意：** 代码中的 `load_dotenv()` 会优先读取 `.env` 文件，如果没有 `.env` 文件或环境变量已设置，会自动使用系统环境变量。

#### 方法 3: 使用 Docker Secrets 或云服务密钥管理

如果使用 Docker 或云服务（AWS, GCP, Azure）：
- **Docker**: 使用 Docker secrets
- **AWS**: 使用 AWS Secrets Manager 或 Parameter Store
- **GCP**: 使用 Secret Manager
- **Azure**: 使用 Key Vault

#### 方法 4: 使用 systemd 服务（Linux）

如果你使用 systemd 运行策略：

```ini
# /etc/systemd/system/nautilus-trader.service
[Unit]
Description=AlgVex NautilusTrader Bot

[Service]
Type=simple
User=your_user
WorkingDirectory=/home/linuxuser/nautilus_AlgVex
Environment="BINANCE_API_KEY=your_key"
Environment="BINANCE_API_SECRET=your_secret"
Environment="DEEPSEEK_API_KEY=your_key"
ExecStart=/usr/bin/python3 main_live.py
Restart=always

[Install]
WantedBy=multi-user.target
```

### 📋 安全检查清单

在云服务器上部署前，确认：

- [ ] `.env` 文件权限设置为 600 (`chmod 600 .env`)
- [ ] `.env` 文件不在 Web 服务器可访问的目录
- [ ] 不要在代码、日志、注释中包含真实 API keys
- [ ] 定期轮换 API keys（每月或每季度）
- [ ] 在 Binance API 设置中限制 IP 访问（如果可能）
- [ ] 使用最小权限原则：API key 只授予必要的权限
- [ ] 不要在聊天、邮件中分享 API keys

### 🚨 如果 API Key 泄露了怎么办？

1. **立即撤销**旧的 API key
2. 在 Binance/DeepSeek 平台生成新 key
3. 更新云服务器上的 `.env` 文件
4. 检查是否有未授权的交易活动
5. 检查服务器日志，寻找可能的入侵痕迹

### 对比表格

| 方法 | 安全性 | 易用性 | 推荐场景 |
|------|--------|--------|----------|
| `.env` + `chmod 600` | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 个人服务器 |
| 系统环境变量 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 生产环境 |
| Docker Secrets | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | Docker 部署 |
| 云服务密钥管理 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 企业级部署 |

### 验证安全性

测试你的配置是否安全：

```bash
# 1. 检查 .env 文件权限
ls -la .env
# 应该显示: -rw------- (600)

# 2. 尝试用其他用户读取（应该失败）
sudo -u other_user cat .env
# 应该显示: Permission denied

# 3. 检查 .env 是否在 Git 中
git check-ignore .env
# 应该输出: .env（表示被忽略）

# 4. 检查是否意外提交
git status | grep .env
# 应该没有任何输出
```

---

**总结：**

- `.gitignore` 保护你的密钥不会上传到 GitHub
- 但云服务器上的 `.env` 文件需要通过 **文件权限** (`chmod 600`) 来保护
- 生产环境推荐使用系统环境变量或云服务密钥管理

