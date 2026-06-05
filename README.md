# 📰 GitHub 每日开源简报

每天早上 8:00，自动推送 GitHub 热门开源项目到微信 + 邮箱。

## ✨ 功能

- 🔥 **每日趋势新秀** — 近 7 天 Star 增速最快的新项目（5 个）
- 💎 **经典项目补位** — 高 Star 老牌项目，每月不重样（1 个）
- 📊 **周日深度周报** — TOP 10 + 五大领域分类盘点 + 趋势总结
- 🤖 **AI 中文摘要** — 每个项目 2-3 句通俗解读：干嘛的、为什么火、适合谁
- 📨 **双渠道推送** — 微信（PushPlus）+ Email

## 🚀 快速开始

### 1. Fork 本仓库 / 创建你自己的仓库

```bash
git clone https://github.com/YOUR_USERNAME/daily-github-briefing.git
cd daily-github-briefing
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填写下面的密钥
```

### 3. 获取必需的 Token

| 配置项 | 获取方式 | 必须？ |
|--------|----------|--------|
| `GITHUB_TOKEN` | [GitHub Settings → Tokens](https://github.com/settings/tokens) 创建，勾选 `public_repo` | ✅ 推荐 |
| `PUSHPLUS_TOKEN` | [pushplus.plus](https://www.pushplus.plus/) 注册即得，免费 | 微信推送必选 |
| `CLAUDE_API_KEY` | [Anthropic Console](https://console.anthropic.com/) | AI 摘要必选 |
| `SMTP_*` + `TO_EMAIL` | QQ 邮箱 → 设置 → 账户 → POP3/SMTP 服务 | 邮件推送必选 |

### 4. 设置 GitHub Secrets

在仓库 `Settings → Secrets and variables → Actions` 中添加：

```
GITHUB_TOKEN     # GitHub Token
CLAUDE_API_KEY   # Claude API Key（或 OPENAI_API_KEY）
PUSHPLUS_TOKEN   # PushPlus Token
SMTP_HOST        # 邮件 SMTP（如 smtp.qq.com）
SMTP_PORT        # 587
SMTP_USER        # 发件邮箱
SMTP_PASS        # SMTP 授权码
TO_EMAIL         # 收件邮箱
```

### 5. 手动测试

在 Actions 页面点击 `Run workflow` → 选择 `Daily GitHub Briefing`。

## 📋 简报示例

```
📅 2026年06月06日 周六 Github 开源日报｜精选近期暴涨星标优质项目

## 🔥 今日趋势新秀

### 1. [microsoft/garnet](https://github.com/microsoft/garnet)
⭐ 11,200 Stars｜本周新增 +350 估
🛠️ C#
💡 微软开源的远程缓存存储系统，性能比 Redis 快 10 倍。因为微软出品加上性能炸裂，一周涨了 3000+ Star。适合做高性能缓存、会话存储的后端开发者。

### 2. ...
```

## 🛠️ 技术栈

- **数据源**：GitHub Search API + GitHub Trending 页面
- **AI 摘要**：Anthropic Claude API / OpenAI 兼容接口
- **定时调度**：GitHub Actions (cron: 每天 8:00 北京时间)
- **推送渠道**：PushPlus (微信) + SMTP (邮件)

## 📁 项目结构

```
daily-github-briefing/
├── .github/workflows/daily.yml  # GitHub Actions 定时任务
├── main.py                      # 主脚本（全部逻辑）
├── seen_projects.json           # 去重记录（自动生成）
├── requirements.txt
├── .env.example
└── README.md
```

## 🔧 本地运行

```bash
pip install -r requirements.txt
cp .env.example .env  # 编辑填好密钥
python main.py
```

## 📌 许可

MIT — 随便用，随便改。
