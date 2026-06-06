#!/usr/bin/env python3
"""
GitHub 每日开源简报生成器
GitHub Daily Open Source Briefing Generator

定时抓取 GitHub Trending 项目，AI 生成中文摘要，推送到 Email + 微信。
"""

import os
import sys
import json
import random
import smtplib
import hashlib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# ── 基础配置 ─────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
SEEN_FILE = BASE_DIR / "seen_projects.json"
load_dotenv(BASE_DIR / ".env")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}" if GITHUB_TOKEN else "",
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "daily-github-briefing",
}

# AI 摘要配置
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-4-6")

# 推送配置
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT") or "587")
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
TO_EMAIL = os.getenv("TO_EMAIL", "")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "")

# ── 去重系统 ─────────────────────────────────────────────

def load_seen():
    """加载已推送项目记录"""
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    return {"trending": {}, "classic": {}}


def save_seen(data):
    """保存已推送记录"""
    SEEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cleanup_seen(data, key, days):
    """清理超过 N 天的记录"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    data[key] = {k: v for k, v in data[key].items() if v >= cutoff}
    return data


def mark_seen(data, key, repo_full_name):
    """标记项目为已推送"""
    today = datetime.now().strftime("%Y-%m-%d")
    data[key][repo_full_name] = today
    return data


def is_seen(data, key, repo_full_name, days):
    """检查项目是否在 N 天内出现过"""
    last_seen = data[key].get(repo_full_name, "")
    if not last_seen:
        return False
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return last_seen >= cutoff


# ── GitHub 数据抓取 ──────────────────────────────────────

def github_search(query, sort="stars", order="desc", per_page=30):
    """GitHub Search API 封装"""
    url = "https://api.github.com/search/repositories"
    params = {"q": query, "sort": sort, "order": order, "per_page": per_page}
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    if resp.status_code == 403 and "rate limit" in resp.text.lower():
        print("⚠️ GitHub API 速率限制，尝试无认证模式...")
        resp = requests.get(url, headers={"User-Agent": "daily-github-briefing"}, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_trending_projects(limit=15):
    """
    抓取趋势项目：近 7 天创建的新星 + 近 7 天活跃的高星项目
    返回列表，按 star 增长速度估算排序
    """
    now = datetime.now(timezone.utc)
    seven_days_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    thirty_days_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    all_repos = []

    # 策略 1：近 7 天创建的项目，按 star 排序（新项目爆火）
    try:
        result = github_search(f"created:>={seven_days_ago}", sort="stars", per_page=20)
        for item in result.get("items", []):
            item["_source"] = "new"
            # 估算日均 star（创建天数）
            created_str = item.get("created_at", "")
            if created_str:
                created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                days_since = max(1, (now - created_dt).days)
                item["_daily_stars"] = round(item.get("stargazers_count", 0) / days_since)
            else:
                item["_daily_stars"] = item.get("stargazers_count", 0)
            all_repos.append(item)
    except Exception as e:
        print(f"⚠️ 策略1失败: {e}")

    # 策略 2：近 30 天创建 + 高 star（100+），按 star 排（爆发型老一些的项目）
    try:
        result = github_search(f"created:>={thirty_days_ago} stars:>50", sort="stars", per_page=20)
        for item in result.get("items", []):
            item["_source"] = "rising"
            created_str = item.get("created_at", "")
            if created_str:
                created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                days_since = max(1, (now - created_dt).days)
                item["_daily_stars"] = round(item.get("stargazers_count", 0) / days_since)
            else:
                item["_daily_stars"] = item.get("stargazers_count", 0)
            all_repos.append(item)
    except Exception as e:
        print(f"⚠️ 策略2失败: {e}")

    # 去重 & 按日均 star 排序
    seen_full_names = set()
    uniq_repos = []
    for r in all_repos:
        fn = r.get("full_name", "")
        if fn and fn not in seen_full_names:
            seen_full_names.add(fn)
            uniq_repos.append(r)

    uniq_repos.sort(key=lambda r: r.get("_daily_stars", 0), reverse=True)
    return uniq_repos[:limit]


def fetch_classic_project(seen_classic, limit=5):
    """
    抓取经典项目：总 star >= 5000，挑选出没有在近期简报中出现过的
    """
    # 使用多组查询词来获取不同领域的经典项目
    queries = [
        "stars:>5000",
    ]
    candidates = []

    for q in queries:
        try:
            result = github_search(q, sort="stars", per_page=50)
            for item in result.get("items", []):
                fn = item.get("full_name", "")
                if fn and not is_seen(seen_classic, "classic", fn, 30):
                    candidates.append(item)
        except Exception as e:
            print(f"⚠️ 经典项目搜索失败: {e}")

    # 去重并随机挑选
    seen_fns = set()
    uniq = []
    for r in candidates:
        fn = r.get("full_name", "")
        if fn not in seen_fns:
            seen_fns.add(fn)
            uniq.append(r)

    if len(uniq) <= limit:
        return uniq

    return random.sample(uniq, limit)


def build_repo_info(item):
    """从 GitHub API 返回项中提取统一字段"""
    return {
        "full_name": item.get("full_name", "unknown/repo"),
        "url": item.get("html_url", ""),
        "stars": item.get("stargazers_count", 0),
        "language": item.get("language") or "多语言",
        "description": item.get("description") or "暂无描述",
        "topics": item.get("topics", []),
        "daily_stars": item.get("_daily_stars", 0),
        "forks": item.get("forks_count", 0),
        "created_at": (item.get("created_at", "")[:10] if item.get("created_at") else ""),
        "license": (item.get("license", {}) or {}).get("spdx_id", ""),
        "_source": item.get("_source", ""),
    }


# ── AI 摘要生成 ──────────────────────────────────────────

def call_anthropic_api(prompt):
    """调用 Anthropic Claude API（支持自定义 Base URL）"""
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    model = AI_MODEL
    if not model.startswith("claude") and not model.startswith("deepseek"):
        model = "claude-sonnet-4-6"
    payload = {
        "model": model,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = requests.post(
        f"{base_url.rstrip('/')}/messages",
        headers=headers,
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]


def call_openai_compatible_api(prompt):
    """调用 OpenAI 兼容接口"""
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": "你是一个专业的技术编辑，擅长用通俗易懂的中文解释开源项目。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2048,
        "temperature": 0.7,
    }
    resp = requests.post(
        f"{OPENAI_BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def generate_summaries(repos, is_weekly=False):
    """
    为项目列表生成中文摘要
    返回格式：{"full_name": "中文简介", ...}
    """
    if not repos:
        return {}

    # 构建 prompt
    repo_list = []
    for r in repos:
        info = build_repo_info(r)
        repo_list.append(
            f"- {info['full_name']}\n"
            f"  ⭐ {info['stars']} | 语言: {info['language']} | 描述: {info['description']}\n"
            f"  话题标签: {', '.join(info['topics'][:5]) if info['topics'] else '无'}"
        )

    mode = "周度深度版" if is_weekly else "日常轻量版"

    prompt = f"""你是开源资讯编辑。请为以下 GitHub 项目各写一段通俗易懂的中文简介。

要求：
- 每个项目 2~3 句话
- 第 1 句：这项目是干什么的（让小白也能懂）
- 第 2 句：为什么最近火了 / 为什么值得关注
- 第 3 句：适合什么人用、有什么实用价值
- 语言口语化，避免专业黑话，必要时附带通俗解释
- 每个简介控制在 80 字以内

以下是项目列表：

{chr(10).join(repo_list)}

请按以下 JSON 格式返回（只返回 JSON，不要其他内容）：
{{"项目完整名称": "中文简介", ...}}"""

    # 尝试 AI 调用
    summary_text = ""
    try:
        if CLAUDE_API_KEY:
            summary_text = call_anthropic_api(prompt)
        elif OPENAI_API_KEY:
            summary_text = call_openai_compatible_api(prompt)
        else:
            print("⚠️ 未配置 AI API Key，使用项目自带描述作为摘要")
            return fallback_summaries(repos)
    except Exception as e:
        print(f"⚠️ AI 摘要生成失败: {e}，降级为项目自带描述")
        return fallback_summaries(repos)

    # 解析 JSON
    try:
        # 提取 JSON 块（可能被 markdown 包裹）
        if "```" in summary_text:
            summary_text = summary_text.split("```")[1]
            if summary_text.startswith("json"):
                summary_text = summary_text[4:]
        return json.loads(summary_text.strip())
    except json.JSONDecodeError:
        print(f"⚠️ AI 返回格式解析失败，降级为项目自带描述")
        return fallback_summaries(repos)


def fallback_summaries(repos):
    """无 AI 时的降级方案：使用项目自带描述"""
    summaries = {}
    for r in repos:
        info = build_repo_info(r)
        desc = info["description"] if info["description"] != "暂无描述" else ""
        lang = info["language"]
        stars = info["stars"]

        if desc:
            summary = f"这是一个 {lang} 项目，{desc}。当前已获得 {stars} 个 Star。"
        else:
            summary = f"这是一个 {lang} 项目，当前已获得 {stars} 个 Star，近期社区关注度较高。"

        summaries[info["full_name"]] = summary
    return summaries


# ── 简报渲染 ─────────────────────────────────────────────

def render_daily_briefing(trending, classic, summaries):
    """渲染日常版简报（周一~周六）"""
    today_str = datetime.now().strftime("%Y年%m月%d日")
    weekday = ["一", "二", "三", "四", "五", "六", "日"][datetime.now().weekday()]

    lines = [
        f"📅 {today_str} 周{weekday} Github 开源日报｜精选近期暴涨星标优质项目",
        "",
    ]

    # 板块 1：趋势新秀
    lines.append("## 🔥 今日趋势新秀")
    lines.append("")
    for i, repo in enumerate(trending[:5], 1):
        info = build_repo_info(repo)
        summary = summaries.get(info["full_name"], info["description"])
        lines.append(f"### {i}. [{info['full_name']}]({info['url']})")
        lines.append(f"⭐ {info['stars']:,} Stars｜本周新增 +{info['daily_stars'] * 7:,} 估")
        lines.append(f"🛠️ {info['language']}")
        lines.append(f"💡 {summary}")
        lines.append("")

    # 板块 2：经典补位
    if classic:
        info = build_repo_info(classic[0])
        summary = summaries.get(info["full_name"], info["description"])
        lines.append("## 💎 今日经典补位")
        lines.append("")
        lines.append(f"### [{info['full_name']}]({info['url']})")
        lines.append(f"⭐ {info['stars']:,} Stars（总）")
        lines.append(f"🛠️ {info['language']}")
        lines.append(f"💡 {summary}")
        lines.append("")

    # 页脚
    lines.append("---")
    lines.append("📌 本简报由开源数据自动抓取 + AI 整编")

    return "\n".join(lines)


def render_weekly_briefing(trending, summaries):
    """渲染周度深度版简报（周日）"""
    today_str = datetime.now().strftime("%Y年%m月%d日")

    lines = [
        f"📅 {today_str} Github 开源周报｜本周 TOP10 热门新秀 + 五大领域盘点",
        "",
    ]

    # 板块 1：TOP 10 趋势新秀
    lines.append("## 🔥 本周 TOP 10 热门新秀")
    lines.append("")
    for i, repo in enumerate(trending[:10], 1):
        info = build_repo_info(repo)
        summary = summaries.get(info["full_name"], info["description"])
        lines.append(f"### {i}. [{info['full_name']}]({info['url']})")
        lines.append(f"⭐ {info['stars']:,} Stars｜日均 +{info['daily_stars']:,}")
        lines.append(f"🛠️ {info['language']}")
        lines.append(f"💡 {summary}")
        lines.append("")

    # 板块 2：领域分类盘点
    lines.append("## 📊 本周五大领域盘点")
    lines.append("")

    categories = {
        "🧠 AI / 大模型": [],
        "🎨 前端 / 全栈": [],
        "⚙️ 后端 / 基础设施": [],
        "🛡️ 运维 / DevOps": [],
        "🔧 开发者工具": [],
    }

    # 简单分类（按 topics 和 language 归类）
    for repo in trending:
        info = build_repo_info(repo)
        topics_str = " ".join(info["topics"]).lower()
        lang = info["language"].lower()

        if any(t in topics_str for t in ["ai", "llm", "gpt", "machine-learning", "deep-learning", "neural"]):
            categories["🧠 AI / 大模型"].append(info)
        elif any(t in topics_str for t in ["frontend", "react", "vue", "css", "ui", "web"]):
            categories["🎨 前端 / 全栈"].append(info)
        elif any(t in topics_str for t in ["devops", "docker", "kubernetes", "monitoring", "ci"]):
            categories["🛡️ 运维 / DevOps"].append(info)
        elif any(t in topics_str for t in ["cli", "tool", "sdk", "library", "api"]):
            categories["🔧 开发者工具"].append(info)
        else:
            categories["⚙️ 后端 / 基础设施"].append(info)

    for cat_name, cat_repos in categories.items():
        if cat_repos:
            lines.append(f"### {cat_name}（{len(cat_repos)} 个）")
            for info in cat_repos[:3]:
                lines.append(f"- [{info['full_name']}]({info['url']}) ⭐{info['stars']:,} — {info['description'][:60]}")
            lines.append("")

    # 板块 3：周趋势总结
    lines.append("## 📈 本周开源趋势总结")
    lines.append("")
    # 统计语言分布
    lang_count = {}
    for repo in trending:
        info = build_repo_info(repo)
        lang = info["language"]
        lang_count[lang] = lang_count.get(lang, 0) + 1
    top_langs = sorted(lang_count.items(), key=lambda x: x[1], reverse=True)[:5]

    lang_str = "、".join([f"{l}({c})" for l, c in top_langs])
    total_stars = sum(build_repo_info(r)["stars"] for r in trending)

    lines.append(f"本周热门新秀总星数超 **{total_stars:,}**，技术语言分布：{lang_str}。")
    lines.append(f"AI 相关项目持续占据主流，同时开发者工具和前端框架保持活跃。")
    lines.append("")

    # 页脚
    lines.append("---")
    lines.append("📌 本简报由开源数据自动抓取 + AI 整编")

    return "\n".join(lines)


# ── 推送渠道 ─────────────────────────────────────────────

def push_email(subject, content):
    """通过 SMTP 发送邮件"""
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, TO_EMAIL]):
        print("⚠️ 邮件配置不完整，跳过邮件推送")
        return False

    msg = MIMEText(content, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = TO_EMAIL

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [TO_EMAIL], msg.as_string())
        print("✅ 邮件发送成功")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        return False


def push_pushplus(title, content):
    """通过 PushPlus 推送到微信"""
    if not PUSHPLUS_TOKEN:
        print("⚠️ 未配置 PushPlus Token，跳过微信推送")
        return False

    try:
        resp = requests.post(
            "https://www.pushplus.plus/send",
            json={
                "token": PUSHPLUS_TOKEN,
                "title": title,
                "content": content,
                "template": "markdown",
            },
            timeout=30,
        )
        data = resp.json()
        if data.get("code") == 200:
            print("✅ PushPlus 微信推送成功")
            return True
        else:
            print(f"❌ PushPlus 推送失败: {data.get('msg', '未知错误')}")
            return False
    except Exception as e:
        print(f"❌ PushPlus 推送失败: {e}")
        return False


# ── 格式转换工具 ──────────────────────────────────────────

def markdown_to_html(md_text):
    """简易 Markdown → HTML（邮件兼容）"""
    # 这里用一个极简转换，生产环境可换成 markdown 库
    import re

    lines = md_text.split("\n")
    html_lines = ['<div style="max-width:680px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,sans-serif;color:#333;">']
    in_list = False

    for line in lines:
        # 标题
        if line.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = line[4:]
            html_lines.append(f'<h3 style="margin:20px 0 10px;color:#1a1a1a;">{text}</h3>')
        elif line.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = line[3:]
            html_lines.append(f'<h2 style="margin:24px 0 12px;color:#111;border-bottom:2px solid #eee;padding-bottom:8px;">{text}</h2>')
        # 无序列表项
        elif line.startswith("- "):
            if not in_list:
                html_lines.append('<ul style="padding-left:20px;">')
                in_list = True
            text = line[2:]
            # 加粗处理
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
            text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2" style="color:#0366d6;">\1</a>', text)
            html_lines.append(f'<li style="margin:4px 0;">{text}</li>')
        # 分隔线
        elif line.startswith("---"):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append('<hr style="border:none;border-top:1px solid #eee;margin:20px 0;">')
        # emoji 段落
        elif line.startswith("📅"):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
            text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2" style="color:#0366d6;">\1</a>', text)
            html_lines.append(f'<p style="font-size:18px;font-weight:bold;margin:16px 0;">{text}</p>')
        elif line.strip() == "":
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<br>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
            text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2" style="color:#0366d6;">\1</a>', text)
            html_lines.append(f'<p style="margin:6px 0;line-height:1.6;">{text}</p>')

    if in_list:
        html_lines.append("</ul>")
    html_lines.append("</div>")

    return "\n".join(html_lines)


# ── 主流程 ───────────────────────────────────────────────

def main():
    print("🚀 GitHub 每日开源简报生成器 启动...")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 检查配置
    if not PUSHPLUS_TOKEN and not TO_EMAIL:
        print("⚠️ 未配置任何推送渠道！请设置 PUSHPLUS_TOKEN 或 TO_EMAIL")
        print("   复制 .env.example 为 .env 并填写配置")

    # 检测是否周日
    is_sunday = datetime.now().weekday() == 6
    mode = "📊 周度深度版" if is_sunday else "📋 日常标准版"
    print(f"   模式: {mode}")

    # 加载去重记录
    seen = load_seen()
    seen = cleanup_seen(seen, "trending", 3)   # trending 3 天去重
    seen = cleanup_seen(seen, "classic", 30)   # classic 30 天去重

    # ── 抓取数据 ──
    print("📡 抓取 GitHub Trending 数据...")
    trending_raw = fetch_trending_projects(limit=20)

    if not trending_raw:
        print("❌ 未获取到趋势项目，请检查网络或 GitHub Token")
        sys.exit(1)

    # 过滤最近 3 天出现过的项目
    trending_raw = [r for r in trending_raw
                    if not is_seen(seen, "trending", r.get("full_name", ""), 3)]

    trending_count = 10 if is_sunday else 5
    trending = trending_raw[:trending_count]

    # 经典项目
    classic = []
    if not is_sunday:
        classic_raw = fetch_classic_project(seen, limit=3)
        classic = classic_raw[:1]  # 日常只取 1 个
        if not classic:
            print("⚠️ 未找到合适的经典项目（可能都已出现过），跳过")

    # ── AI 摘要 ──
    print("🤖 生成 AI 摘要...")
    all_repos = trending + classic
    summaries = generate_summaries(all_repos, is_weekly=is_sunday)

    # ── 渲染简报 ──
    print("📝 渲染简报...")
    if is_sunday:
        md_content = render_weekly_briefing(trending, summaries)
        subject = f"📊 GitHub 开源周报｜{datetime.now().strftime('%Y.%m.%d')}"
    else:
        md_content = render_daily_briefing(trending, classic, summaries)
        subject = f"📋 GitHub 开源日报｜{datetime.now().strftime('%Y.%m.%d')}"

    html_content = markdown_to_html(md_content)

    # ── 推送 ──
    print("📨 开始推送...")
    success_count = 0

    if push_pushplus(subject, md_content):
        success_count += 1

    if push_email(subject, html_content):
        success_count += 1

    # ── 记录去重 ──
    if success_count > 0:
        for r in trending:
            seen = mark_seen(seen, "trending", r.get("full_name", ""))
        for r in classic:
            seen = mark_seen(seen, "classic", r.get("full_name", ""))
        save_seen(seen)
        print("💾 已更新去重记录")
    else:
        print("⚠️ 所有推送渠道均失败，不更新去重记录（下次重试）")

    # ── 本地输出 ──
    print("\n" + "=" * 60)
    print(md_content)
    print("=" * 60)
    print(f"\n✅ 完成！成功推送到 {success_count} 个渠道")


if __name__ == "__main__":
    main()
