#!/usr/bin/env python3
"""
zfuye-youtube: YouTube副业视频字幕 → DeepSeek翻译 → zfuye.org发布
"""

import os, json, random, datetime, requests, re
import xml.etree.ElementTree as ET
from html import unescape
from base64 import b64encode

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY", "")
WP_USER      = os.environ.get("WP_USER", "")
WP_APP_PASS  = os.environ.get("WP_APP_PASS", "")
WP_BASE      = "https://www.zfuye.org/wp-json/wp/v2"

TODAY    = datetime.date.today().isoformat()
HOUR_U   = datetime.datetime.utcnow().hour
LOG_PATH = "data/log.json"
HEADERS  = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# ── YouTube频道配置 ──────────────────────────────────────────────────────────
# 全部是副业/赚钱/财务自由方向的英文频道
CHANNELS = [
    {"name": "Ali Abdaal",          "cat": "AI副业",   "channel_id": "UCnUYZLuoy1rq1aVMwx4aTzw"},
    {"name": "Graham Stephan",      "cat": "被动收入", "channel_id": "UCV6KDgJskWaEckne5aPA0aQ"},
    {"name": "Andrei Jikh",         "cat": "被动收入", "channel_id": "UCGy7SkBjcIAgTiwkXEtPnYg"},
    {"name": "Nate O'Brien",        "cat": "被动收入", "channel_id": "UCsXphBnzQgjyeKNRB5ZHGZQ"},
    {"name": "Mark Tilbury",        "cat": "被动收入", "channel_id": "UCBcRF18a7Qf58cCRy5xuWwQ"},
    {"name": "Ryan Scribner",       "cat": "AI副业",   "channel_id": "UC3mjMoJuFnjYRBLon_6njbQ"},
    {"name": "Codie Sanchez",       "cat": "海外接单", "channel_id": "UCFmG207_kBU5kx59N7bUkCQ"},
    {"name": "Humphrey Yang",       "cat": "信息差",   "channel_id": "UCFCEuCsyWP0YkP3CZ3Mr01Q"},
]

FAIL_PHRASES = ["未能取得", "无法逐句", "正文内容缺失", "未能获取", "无法翻译", "抓取失败"]


# ── 日志 ────────────────────────────────────────────────────────────────────
def load_log():
    try:
        with open(LOG_PATH) as f: return json.load(f)
    except: return {"used_ids": [], "published": []}

def save_log(log):
    os.makedirs("data", exist_ok=True)
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


# ── YouTube RSS抓取 ──────────────────────────────────────────────────────────
def fetch_channel_videos(channel, limit=10):
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['channel_id']}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        root = ET.fromstring(r.content)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt":   "http://www.youtube.com/xml/schemas/2015",
            "media":"http://search.yahoo.com/mrss/",
        }
        entries = root.findall("atom:entry", ns)
        videos = []
        for entry in entries[:limit]:
            title_el   = entry.find("atom:title", ns)
            vid_el     = entry.find("yt:videoId", ns)
            desc_el    = entry.find(".//media:description", ns)
            if title_el is None or vid_el is None: continue
            title    = (title_el.text or "").strip()
            video_id = (vid_el.text or "").strip()
            desc     = (desc_el.text or "")[:300].strip() if desc_el is not None else ""
            if title and video_id:
                videos.append({
                    "title":    title,
                    "video_id": video_id,
                    "url":      f"https://www.youtube.com/watch?v={video_id}",
                    "summary":  desc,
                    "source":   channel["name"],
                    "cat":      channel["cat"],
                })
        return videos
    except Exception as e:
        print(f"  [{channel['name']}] RSS失败: {e}")
        return []


# ── 字幕抓取 ─────────────────────────────────────────────────────────────────
def fetch_transcript(video_id, max_chars=5000):
    import traceback
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        segs = None
        # 新版 >= 1.0：实例化后调用 fetch
        try:
            api  = YouTubeTranscriptApi()
            segs = api.fetch(video_id)        # 自动选最佳语言
        except Exception as e1:
            print(f"  [字幕] 新版API失败: {type(e1).__name__}: {e1}")
            # 旧版 < 1.0：类方法
            try:
                segs = YouTubeTranscriptApi.get_transcript(
                    video_id, languages=["en", "en-US", "en-GB"])
            except Exception as e2:
                print(f"  [字幕] 旧版API失败: {type(e2).__name__}: {e2}")

        if not segs:
            return ""
        text = " ".join(s["text"] if isinstance(s, dict) else s.text for s in segs)
        text = re.sub(r"\s+", " ", text).strip()
        print(f"  [字幕] {len(text)} 字符")
        return text[:max_chars]
    except Exception as e:
        print(f"  [字幕] 异常: {traceback.format_exc()}")
        return ""


# ── 选视频 ───────────────────────────────────────────────────────────────────
def pick_video(log):
    used = set(log.get("used_ids", []))
    random.shuffle(CHANNELS)
    for ch in CHANNELS:
        videos = fetch_channel_videos(ch)
        fresh  = [v for v in videos if v["video_id"] not in used]
        if fresh:
            return random.choice(fresh[:4])
    return None


# ── DeepSeek翻译 ─────────────────────────────────────────────────────────────
def translate_video(video, transcript):
    prompt = f"""以下是一个YouTube视频的英文字幕（逐字稿）：
频道：{video['source']}
视频标题：{video['title']}
字幕内容：{transcript}
视频链接：{video['url']}

【重要】请先判断这个视频是否适合发布：
- 如果内容涉及政治、军事、地缘冲突、政府批评、敏感社会议题，请直接回复"SKIP"
- 只发布科技、商业、副业、赚钱、理财、创业类内容

如果内容合适，请做三件事：
1. 把字幕内容整理成一篇流畅的中文文章（不是逐字翻译，而是提炼精华，去掉口语重复，保留核心观点和方法）
2. 文末加编者按（2-3句AI编辑点评）
3. 最后加3个FAQ问答，用中国读者会搜索的问题，格式：

<h2>常见问题</h2>
<h3>Q：[问题]</h3>
<p>A：[回答，2-3句]</p>
（重复3次）

格式要求：
- HTML格式，用<h2><p><ul><li>
- 不要写文章大标题
- 不要```html代码块标记
- 编者按：<blockquote style="border-left:3px solid #f0a500;padding:12px 16px;margin:24px 0;background:#fffbf0;color:#555">[编辑点评]</blockquote>
- 结尾：<p style="color:#999;font-size:13px">视频来源：<a href="{video['url']}" target="_blank" rel="nofollow">{video['source']} · YouTube</a></p>"""

    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "deepseek-v4-flash",
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.7},
            timeout=120,
        )
        content = r.json()["choices"][0]["message"]["content"].strip()
        if content.strip().upper().startswith("SKIP"):
            print("  [DeepSeek] 内容不合规，跳过")
            return None
        if any(p in content for p in FAIL_PHRASES):
            print("  [DeepSeek] 识别到失败词，跳过")
            return None
        print(f"  [DeepSeek] 完成 {len(content)} 字")
        return content
    except Exception as e:
        print(f"  [DeepSeek] 失败: {e}")
        return None


def gen_cn_title(video):
    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "deepseek-v4-flash",
                "messages": [{"role": "user", "content":
                    f"""把这个YouTube视频标题改写成中文搜索词风格标题：
- 用中国人会在搜索框输入的词，不要直译
- 带具体数字（金额/时间/步骤数）优先
- 10-18字，口语化
- 人名/频道名换成"一位美国人""一个理财博主"等
- 例子风格："月入5万的副业怎么做""美国小伙靠被动收入年赚100万""3个让钱自动增值的方法"
只输出标题，不加引号

英文标题：{video['title']}"""}],
                "temperature": 0.6
            },
            timeout=60,
        )
        title = r.json()["choices"][0]["message"]["content"].strip()
        cn = sum(1 for c in title if '一' <= c <= '鿿')
        if cn < 3:
            raise ValueError("非中文")
        print(f"  [标题] {title}")
        return title
    except Exception as e:
        print(f"  [标题] 失败: {e}，使用原标题")
        return video["title"]


# ── WordPress发布 ────────────────────────────────────────────────────────────
def get_or_create_category(name, auth_h):
    try:
        r = requests.get(f"{WP_BASE}/categories?search={name}&per_page=5",
                         headers=auth_h, timeout=10)
        for c in r.json():
            if c["name"] == name: return c["id"]
        r2 = requests.post(f"{WP_BASE}/categories",
                           headers={**auth_h, "Content-Type": "application/json"},
                           json={"name": name}, timeout=10)
        return r2.json().get("id")
    except: return None


def get_related_posts(cat_name, exclude_id, auth_h):
    try:
        r = requests.get(f"{WP_BASE}/categories?search={cat_name}&per_page=5",
                         headers=auth_h, timeout=8)
        cats = [c for c in r.json() if c["name"] == cat_name]
        if not cats: return ""
        cat_id = cats[0]["id"]
        r2 = requests.get(
            f"{WP_BASE}/posts?categories={cat_id}&exclude={exclude_id}&per_page=3&orderby=date&order=desc",
            headers=auth_h, timeout=8)
        posts = r2.json()
        if not posts: return ""
        items = "".join(
            f'<li><a href="{p["link"]}">{p["title"]["rendered"]}</a></li>'
            for p in posts)
        return f"""
<hr style="margin:32px 0 16px;border:none;border-top:1px solid #eee">
<div style="background:#f5f5f5;border-radius:8px;padding:16px 20px;font-size:14px">
  <p style="margin:0 0 10px;font-weight:bold;color:#333">📖 相关阅读</p>
  <ul style="margin:0;padding-left:18px;line-height:2;color:#555">{items}</ul>
</div>"""
    except Exception as e:
        print(f"  [相关文章] 失败: {e}")
        return ""


def build_faq_schema(content, title):
    import json as _j
    qs = re.findall(r'<h3>Q[：:]\s*(.+?)</h3>\s*<p>A[：:]\s*(.+?)</p>', content, re.S)
    if not qs: return ""
    entities = [{"@type": "Question", "name": q.strip(),
                 "acceptedAnswer": {"@type": "Answer",
                                    "text": re.sub(r'<[^>]+>', '', a).strip()}}
                for q, a in qs[:5]]
    schema = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": entities}
    return f'\n<script type="application/ld+json">{_j.dumps(schema, ensure_ascii=False)}</script>\n'


AD_FOOTER = """
<hr style="margin:40px 0 24px;border:none;border-top:1px solid #eee">
<div style="border:2px solid #f0a500;border-radius:10px;background:#fffbf0;padding:20px 24px;font-size:14px;line-height:1.9">
  <p style="margin:0 0 4px;font-size:13px;color:#c47f00;font-weight:bold;letter-spacing:1px">🏷️ 限时推荐</p>
  <p style="margin:0 0 12px;font-weight:bold;font-size:16px;color:#333">📌 关于本站</p>
  <p style="margin:0 0 14px;color:#555">内容整理自海外YouTube视频字幕，仅供个人学习参考。</p>
  <p style="margin:0 0 8px;font-weight:bold;color:#333">🛠️ 站长的同款工具</p>
  <ul style="margin:0 0 16px;padding-left:20px;color:#555">
    <li>主机：<a href="https://zfuye.org/3528.html" target="_blank" rel="nofollow" style="color:#c47f00;font-weight:bold">Hostinger</a>（$2.99/月起）</li>
    <li>域名：<a href="https://www.namecheap.com" target="_blank" rel="nofollow" style="color:#c47f00;font-weight:bold">Namecheap</a></li>
    <li>AI工具：GitHub Copilot（<a href="https://zfuye.org/3528.html" target="_blank" rel="nofollow" style="color:#c47f00;font-weight:bold">操作方法：在这里</a>）</li>
  </ul>
  <p style="margin:0;color:#c47f00;font-weight:bold">你也可以做一台自动赚钱的网站机器 🚀</p>
</div>"""


def publish_post(title_cn, raw_content, video):
    cred   = b64encode(f"{WP_USER}:{WP_APP_PASS}".encode()).decode()
    auth_h = {"Authorization": f"Basic {cred}"}
    cat_id = get_or_create_category(video["cat"], auth_h)
    payload = {
        "title":   {"raw": title_cn},
        "content": {"raw": raw_content},
        "status":  "publish",
        "format":  "standard",
    }
    if cat_id:
        payload["categories"] = [cat_id]
    try:
        r = requests.post(f"{WP_BASE}/posts",
                          headers={**auth_h, "Content-Type": "application/json"},
                          json=payload, timeout=20)
        data = r.json()
        if "id" in data:
            print(f"  ✅ 发布成功: {data['link']}")
            return data["id"], data["link"]
        print(f"  ❌ 发布失败: {data}")
        return None, None
    except Exception as e:
        print(f"  ❌ 异常: {e}")
        return None, None


def update_post(post_id, raw_content):
    cred   = b64encode(f"{WP_USER}:{WP_APP_PASS}".encode()).decode()
    auth_h = {"Authorization": f"Basic {cred}", "Content-Type": "application/json"}
    try:
        requests.post(f"{WP_BASE}/posts/{post_id}",
                      headers=auth_h,
                      json={"content": {"raw": raw_content}},
                      timeout=20)
    except Exception as e:
        print(f"  ⚠️ 更新失败: {e}")


# ── 主流程 ───────────────────────────────────────────────────────────────────
def main():
    print(f"🎬 zfuye-youtube — {TODAY} UTC+{HOUR_U}h")
    log = load_log()

    video = pick_video(log)
    if not video:
        print("  ⚠️ 没有新视频，跳过")
        return

    print(f"  频道: {video['source']} [{video['cat']}]")
    print(f"  原标题: {video['title'][:60]}")

    transcript = fetch_transcript(video["video_id"])
    if len(transcript) < 500:
        print("  ⚠️ 字幕太短或无字幕，跳过")
        log.setdefault("used_ids", []).append(video["video_id"])
        save_log(log)
        return

    title_cn = gen_cn_title(video)
    content  = translate_video(video, transcript)
    if not content:
        print("  ⚠️ 翻译失败或内容不合规，跳过")
        log.setdefault("used_ids", []).append(video["video_id"])
        save_log(log)
        return
    if len(content) < 300:
        print(f"  ⚠️ 内容过短（{len(content)}字），跳过")
        return

    content += AD_FOOTER
    post_id, link = publish_post(title_cn, content, video)

    if post_id and link:
        cred   = b64encode(f"{WP_USER}:{WP_APP_PASS}".encode()).decode()
        auth_h = {"Authorization": f"Basic {cred}"}
        related = get_related_posts(video["cat"], post_id, auth_h)
        faq_schema = build_faq_schema(content, title_cn)
        if related or faq_schema:
            update_post(post_id, content + faq_schema + related)

    if link:
        log.setdefault("used_ids", []).append(video["video_id"])
        log.setdefault("published", []).append({
            "date":   TODAY,
            "title":  title_cn,
            "source": video["source"],
            "url":    link,
        })
        if len(log["used_ids"]) > 1000:
            log["used_ids"] = log["used_ids"][-1000:]
        save_log(log)

    print("✅ 完成")


if __name__ == "__main__":
    main()
