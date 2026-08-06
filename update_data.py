#!/usr/bin/env python3
"""
冻品情报站 - 数据自动采集脚本 v2.0
从多个 RSS 源 + 政府网站聚合冷冻食品行业情报，生成 data.json

支持的数据源：
  RSS 源（自动采集）：
    - FoodBev Media, FoodNavigator 系列, Dieline, Packaging of the World
    - Food Dive, BakeryandSnacks, Beverage Daily, Food Manufacture
    - 日本食品新闻等

  政府/合规数据源（网页抓取）：
    - 国家卫健委公告 (nhc.gov.cn)
    - 国家市监总局抽检/公告 (samr.gov.cn)
    - 国家标准全文公开系统 (openstd.samr.gov.cn)

  学术/配方数据源：
    - 中国知网 CNKI 搜索结果（关键词：冷冻食品、速冻面点、预制菜）
"""

import json
import os
import re
import hashlib
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

try:
    import feedparser
except ImportError:
    print("[ERROR] feedparser 未安装，请运行: pip install feedparser")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("[ERROR] requests 未安装，请运行: pip install requests")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("[INFO] beautifulsoup4 未安装，部分网页抓取将使用内置 HTMLParser")
    BeautifulSoup = None

# 禁用 SSL 警告（部分国内网站证书问题）
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# ===== 配置 =====
DATA_FILE = Path(__file__).parent / "data.json"
MAX_ITEMS_PER_SOURCE = 8   # 每个源最多取多少条
MAX_TOTAL_ITEMS = 150      # data.json 总条目上限
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

# ===== RSS 源配置 =====
RSS_SOURCES = [
    {
        "name": "FoodBev Media",
        "url": "https://www.foodbev.com/latest-news/",
        "type": "newproduct",
        "section": "newproduct",
        "category": "",
        "keyword_filter": ["frozen", "freezer", "ice cream", "ready meal", "convenience",
                           "snack", "plant-based", "protein", "dairy", "beverage",
                           "packaging", "launch", "new product", "innovation"],
        "source_url_base": "https://www.foodbev.com",
        "use_web": True,
    },
    {
        "name": "FoodNavigator-Asia",
        "url": "https://www.foodnavigator-asia.com/arc/outboundfeeds/rss/",
        "type": "report",
        "section": "data",
        "sub": "report",
        "category": "",
        "keyword_filter": ["frozen", "china", "asia", "trend", "innovation", "ingredient",
                           "regulation", "food safety", "plant-based", "dairy", "snack",
                           "beverage", "ready-to-eat", "convenience"],
        "source_url_base": "https://www.foodnavigator-asia.com",
    },
    {
        "name": "FoodNavigator-USA",
        "url": "https://www.foodnavigator-usa.com/arc/outboundfeeds/rss/",
        "type": "report",
        "section": "data",
        "sub": "report",
        "category": "",
        "keyword_filter": ["frozen", "freezer", "plant-based", "innovation", "ingredient",
                           "trend", "launch", "refrigerated", "ready meal"],
        "source_url_base": "https://www.foodnavigator-usa.com",
    },
    {
        "name": "Dieline",
        "url": "https://thedieline.com/feed/",
        "type": "packaging",
        "section": "packaging",
        "category": "",
        "keyword_filter": ["food packaging", "frozen", "sustainable", "design",
                           "redesign", "branding", "packaging", "material", "food"],
        "source_url_base": "https://thedieline.com",
    },
    {
        "name": "Packaging of the World",
        "url": "https://packagingoftheworld.com/feed/",
        "type": "packaging",
        "section": "packaging",
        "category": "",
        "keyword_filter": ["food", "frozen", "packaging", "beverage", "snack", "design"],
        "source_url_base": "https://packagingoftheworld.com",
    },
    {
        "name": "Food Dive",
        "url": "https://www.fooddive.com/feeds/news/",
        "type": "report",
        "section": "data",
        "sub": "report",
        "category": "",
        "keyword_filter": ["frozen", "freezer", "plant-based", "innovation", "ingredient",
                           "trend", "launch", "merger", "acquisition", "recall"],
        "source_url_base": "https://www.fooddive.com",
    },
    {
        "name": "BakeryandSnacks",
        "url": "https://www.bakeryandsnacks.com/arc/outboundfeeds/rss/",
        "type": "newproduct",
        "section": "newproduct",
        "category": "休闲零食",
        "keyword_filter": ["frozen", "snack", "bakery", "launch", "innovation",
                           "plant-based", "clean label", "sustainable"],
        "source_url_base": "https://www.bakeryandsnacks.com",
    },
    {
        "name": "Beverage Daily",
        "url": "https://www.beveragedaily.com/arc/outboundfeeds/rss/",
        "type": "newproduct",
        "section": "newproduct",
        "category": "饮品",
        "keyword_filter": ["frozen", "beverage", "drink", "launch", "innovation",
                           "tea", "coffee", "functional"],
        "source_url_base": "https://www.beveragedaily.com",
    },
    {
        "name": "Food Manufacture",
        "url": "https://www.foodmanufacture.co.uk/arc/outboundfeeds/rss/",
        "type": "report",
        "section": "data",
        "sub": "report",
        "category": "",
        "keyword_filter": ["frozen", "freezer", "manufacturing", "technology",
                           "ingredient", "innovation", "supply chain"],
        "source_url_base": "https://www.foodmanufacture.co.uk",
    },
]

# ===== 政府/合规数据源 =====
GOV_SOURCES = [
    {
        "name": "国家卫健委",
        "type": "compliance",
        "section": "data",
        "sub": "compliance",
        "url": "http://www.nhc.gov.cn/",
        "list_url": "http://www.nhc.gov.cn/cms-search/xxgk/getManuscriptByXXGK.htm",
        "keyword_filter": ["食品", "原料", "添加剂", "公告", "标准"],
    },
    {
        "name": "国家市监总局",
        "type": "compliance",
        "section": "data",
        "sub": "compliance",
        "url": "https://www.samr.gov.cn/",
        "list_url": "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/",
        "keyword_filter": ["食品", "抽检", "合格", "不合格", "召回", "安全"],
    },
    {
        "name": "国家标准全文公开",
        "type": "compliance",
        "section": "data",
        "sub": "compliance",
        "url": "https://openstd.samr.gov.cn/",
        "list_url": "https://openstd.samr.gov.cn/bzgk/gb/index",
        "keyword_filter": ["食品", "速冻", "冷冻", "GB"],
    },
]

# ===== 学术/配方数据源 =====
ACADEMIC_SOURCES = [
    {
        "name": "中国知网-冷冻食品",
        "type": "formulation",
        "section": "formulation",
        "category": "",
        "url": "https://kns.cnki.net/kns8/defaultresult/index",
        "search_url": "https://kns.cnki.net/kns8/defaultresult/index?kw=%E5%86%B7%E5%86%BB%E9%A3%9F%E5%93%81&korder=SU",
        "keyword_filter": [],
    },
]


def extract_image_from_entry(entry):
    """从 RSS entry 中提取图片 URL"""
    if hasattr(entry, 'media_content') and entry.media_content:
        for m in entry.media_content:
            if m.get('url'):
                return m['url']
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        for m in entry.media_thumbnail:
            if m.get('url'):
                return m['url']
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for e in entry.enclosures:
            if e.get('href') and (e.get('type', '').startswith('image') or e.get('href', '').endswith(('.jpg', '.png', '.jpeg'))):
                return e['href']
    if hasattr(entry, 'content') and entry.content:
        for c in entry.content:
            if c.get('value', ''):
                match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', c['value'])
                if match:
                    return match[1]
    if hasattr(entry, 'summary'):
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', entry.summary)
        if match:
            return match[1]
    return None


def clean_html(text):
    """去除 HTML 标签"""
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    clean = re.sub(r'\[CDATA\[|\]\]', '', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def truncate_text(text, max_len=300):
    """截断文本"""
    if len(text) <= max_len:
        return text
    return text[:max_len-3] + "..."


def classify_entry(title, description, source_config):
    """智能分类条目"""
    title_lower = (title + " " + description).lower()
    source_type = source_config["type"]
    
    packaging_kw = ['packaging', 'package', 'pack', 'bottle', 'label', 'design',
                    '包装', '包装设计', '瓶', '标签', '设计']
    formulation_kw = ['ingredient', 'formulation', 'recipe', 'enzyme', 'protein',
                      'starch', 'fiber', 'extract', 'fermentation', '配方', '原料',
                      '添加剂', '酶', '蛋白', '淀粉', '提取物']
    compliance_kw = ['regulation', 'fda', 'efsa', 'food safety', 'standard',
                     '法规', '标准', '食品安全', '批准', '公告']
    ecommerce_kw = ['tmall', 'douyin', 'jd', 'e-commerce', '电商', '天猫', '抖音', '京东']
    
    if source_type == "packaging":
        return "packaging"
    if source_type == "formulation":
        return "formulation"
    if source_type == "newproduct":
        return "newproduct"
    if source_type == "report":
        if any(k in title_lower for k in compliance_kw):
            return "compliance"
        if any(k in title_lower for k in ecommerce_kw):
            return "ecommerce"
    return source_type


def should_include(title, description, keyword_filter):
    """判断条目是否应该被收录"""
    if not keyword_filter:
        return True
    text = (title + " " + description).lower()
    return any(kw.lower() in text for kw in keyword_filter)


def generate_id(source_name, title, date_str):
    """生成稳定的条目 ID"""
    raw = f"{source_name}:{title}:{date_str}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def parse_rss_date(pub_date):
    """解析 RSS 日期"""
    if not pub_date:
        return datetime.now().strftime('%Y-%m-%d')
    try:
        dt = parsedate_to_datetime(pub_date)
        return dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    # 尝试常见格式
    for fmt in ['%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S']:
        try:
            return datetime.strptime(pub_date[:len(fmt)+6].strip(), fmt).strftime('%Y-%m-%d')
        except Exception:
            continue
    return datetime.now().strftime('%Y-%m-%d')


def fetch_rss_feed(source_config):
    """获取单个 RSS/网页 源的数据"""
    items = []
    url = source_config['url']
    name = source_config['name']
    
    # 网页抓取模式
    if source_config.get('use_web'):
        return fetch_web_articles(source_config)
    
    try:
        print(f"  [FETCH RSS] {name}")
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
            timeout=30,
            allow_redirects=True
        )
        resp.raise_for_status()
        
        feed = feedparser.parse(resp.content)
        
        if feed.bozo and not feed.entries:
            print(f"  [WARN] {name}: RSS 解析异常 - {feed.bozo_exception}")
            return items
        
        count = 0
        for entry in feed.entries[:MAX_ITEMS_PER_SOURCE]:
            title = clean_html(entry.get('title', ''))
            description = clean_html(entry.get('description', entry.get('summary', '')))
            link = entry.get('link', '')
            pub_date = entry.get('published', entry.get('updated', ''))
            
            if not title:
                continue
            
            if not should_include(title, description, source_config.get('keyword_filter', [])):
                continue
            
            date_str = parse_rss_date(pub_date)
            image_url = extract_image_from_entry(entry)
            item_type = classify_entry(title, description, source_config)
            
            emoji_map = {
                "newproduct": "🆕",
                "packaging": "📦",
                "formulation": "🧪",
                "report": "📊",
                "compliance": "⚖️",
                "ecommerce": "🛒",
            }
            image_emoji = emoji_map.get(item_type, "📰")
            
            item = {
                "id": generate_id(source_config['name'], title, date_str),
                "type": item_type,
                "section": source_config.get("section", "data"),
                "sub": source_config.get("sub", "report"),
                "date": date_str,
                "title": truncate_text(title, 80),
                "source": source_config['name'],
                "sourceUrl": link,
                "image": image_emoji,
                "category": source_config.get("category", ""),
                "desc": truncate_text(description, 280),
                "tags": auto_tags(title, description),
                "_imageUrl": image_url or "",
            }
            items.append(item)
            count += 1
        
        print(f"  [OK] {name}: {count} 条")
    except requests.RequestException as e:
        print(f"  [ERR] {name}: 网络错误 - {e}")
    except Exception as e:
        print(f"  [ERR] {name}: {e}")
    
    return items


def fetch_web_articles(source_config):
    """通用网页抓取方案（适用于没有 RSS 的网站，如 FoodBev）"""
    items = []
    try:
        url = source_config['url']
        print(f"  [FETCH WEB] {source_config['name']}")
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        resp.encoding = 'utf-8'
        
        soup = BeautifulSoup(resp.text, 'html.parser') if BeautifulSoup else None
        if not soup:
            print(f"  [WARN] {source_config['name']}: 未安装 BeautifulSoup，跳过网页抓取")
            return items
        
        count = 0
        base_url = source_config.get('source_url_base', url)
        keyword_filter = source_config.get('keyword_filter', [])
        
        selectors = [
            'article h2 a', 'article h3 a', '.post-title a', '.entry-title a',
            '.latest-news__item a', '.news-item a', '.article-list a',
            'h2 a', 'h3 a'
        ]
        
        seen = set()
        for selector in selectors:
            for a in soup.select(selector)[:MAX_ITEMS_PER_SOURCE*2]:
                title = clean_html(a.get_text())
                href = a.get('href', '')
                if not title or len(title) < 12 or len(title) > 120:
                    continue
                if title in seen:
                    continue
                if not href.startswith('http'):
                    href = urljoin(base_url, href)
                parsed = urlparse(href)
                path = parsed.path.lower()
                if not any(x in path for x in ['/post', '/article', '/news', '/blog', '/latest-news', '/categories', '/tags']):
                    if not any(kw in title.lower() for kw in keyword_filter):
                        continue
                
                item_type = classify_entry(title, '', source_config)
                emoji_map = {"newproduct":"🆕","packaging":"📦","formulation":"🧪","report":"📊","compliance":"⚖️","ecommerce":"🛒"}
                
                items.append({
                    "id": generate_id(source_config['name'], title, datetime.now().strftime('%Y-%m-%d')),
                    "type": item_type,
                    "section": source_config.get("section", "data"),
                    "sub": source_config.get("sub", "report"),
                    "date": datetime.now().strftime('%Y-%m-%d'),
                    "title": truncate_text(title, 80),
                    "source": source_config['name'],
                    "sourceUrl": href,
                    "image": emoji_map.get(item_type, "📰"),
                    "category": source_config.get("category", ""),
                    "desc": f"来自 {source_config['name']} 的最新报道：{title[:50]}...",
                    "tags": auto_tags(title, ''),
                })
                seen.add(title)
                count += 1
                if count >= MAX_ITEMS_PER_SOURCE:
                    break
            if count >= MAX_ITEMS_PER_SOURCE:
                break
        
        print(f"  [OK] {source_config['name']}: {count} 条")
    except Exception as e:
        print(f"  [ERR] {source_config['name']}: {e}")
    return items


def auto_tags(title, description):
    """自动生成标签"""
    text = (title + " " + description).lower()
    tags = []
    if 'frozen' in text or '冷冻' in text:
        tags.append('冷冻食品')
    if 'china' in text or '中国' in text:
        tags.append('中国市场')
    if 'japan' in text or '日本' in text:
        tags.append('日本')
    if 'plant-based' in text or '植物' in text:
        tags.append('植物基')
    if 'sustainable' in text or '可降解' in text or '环保' in text:
        tags.append('可持续')
    if 'innovation' in text or '创新' in text:
        tags.append('创新')
    if 'packaging' in text or '包装' in text:
        tags.append('包装')
    if 'ingredient' in text or '原料' in text:
        tags.append('原料')
    return tags


# ===== 政府网站抓取 =====

def fetch_gov_nhc():
    """抓取国家卫健委公告"""
    items = []
    try:
        print("  [FETCH GOV] 国家卫健委")
        # 卫健委检索接口
        url = "http://www.nhc.gov.cn/cms-search/xxgk/getManuscriptByXXGK.htm"
        params = {"page": 1, "title": "食品", "source": "", "startTime": "", "endTime": ""}
        resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        for row in data.get('list', [])[:5]:
            title = clean_html(row.get('title', ''))
            link = row.get('link', '')
            if not link.startswith('http'):
                link = urljoin('http://www.nhc.gov.cn/', link)
            date_str = row.get('publishTime', '')[:10] if row.get('publishTime') else datetime.now().strftime('%Y-%m-%d')
            items.append({
                "id": generate_id('国家卫健委', title, date_str),
                "type": "compliance",
                "section": "data",
                "sub": "compliance",
                "date": date_str,
                "title": truncate_text(title, 80),
                "source": "国家卫健委",
                "sourceUrl": link,
                "image": "⚖️",
                "category": "",
                "desc": f"国家卫健委最新公告：{title[:60]}...",
                "tags": ["卫健委", "公告"],
            })
        print(f"  [OK] 国家卫健委: {len(items)} 条")
    except Exception as e:
        print(f"  [ERR] 国家卫健委: {e}")
    return items


def fetch_gov_samr():
    """抓取国家市监总局食品相关公告"""
    items = []
    try:
        print("  [FETCH GOV] 国家市监总局")
        url = "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/"
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser') if BeautifulSoup else None
        
        if soup:
            links = soup.select('a')[:50]
            count = 0
            for a in links:
                title = clean_html(a.get_text())
                href = a.get('href', '')
                if not title or '食品' not in title:
                    continue
                if len(title) < 8 or len(title) > 80:
                    continue
                if not href.startswith('http'):
                    href = urljoin(url, href)
                items.append({
                    "id": generate_id('国家市监总局', title, datetime.now().strftime('%Y-%m-%d')),
                    "type": "compliance",
                    "section": "data",
                    "sub": "compliance",
                    "date": datetime.now().strftime('%Y-%m-%d'),
                    "title": truncate_text(title, 80),
                    "source": "国家市监总局",
                    "sourceUrl": href,
                    "image": "✅",
                    "category": "",
                    "desc": f"市监总局食品相关公告：{title[:60]}...",
                    "tags": ["市监总局", "食品安全"],
                })
                count += 1
                if count >= 5:
                    break
        print(f"  [OK] 国家市监总局: {len(items)} 条")
    except Exception as e:
        print(f"  [ERR] 国家市监总局: {e}")
    return items


def fetch_gov_gb():
    """抓取国家标准更新"""
    items = []
    try:
        print("  [FETCH GOV] 国家标准全文公开")
        url = "https://openstd.samr.gov.cn/bzgk/gb/index"
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser') if BeautifulSoup else None
        if soup:
            count = 0
            for a in soup.select('a')[:80]:
                title = clean_html(a.get_text())
                href = a.get('href', '')
                if not title or '食品' not in title:
                    continue
                if 'GB' not in title:
                    continue
                if not href.startswith('http'):
                    href = urljoin(url, href)
                items.append({
                    "id": generate_id('国家标准', title, datetime.now().strftime('%Y-%m-%d')),
                    "type": "compliance",
                    "section": "data",
                    "sub": "compliance",
                    "date": datetime.now().strftime('%Y-%m-%d'),
                    "title": truncate_text(title, 80),
                    "source": "国家标准全文公开",
                    "sourceUrl": href,
                    "image": "📋",
                    "category": "",
                    "desc": f"国家标准更新：{title[:60]}...",
                    "tags": ["国标", "GB"],
                })
                count += 1
                if count >= 5:
                    break
        print(f"  [OK] 国家标准: {len(items)} 条")
    except Exception as e:
        print(f"  [ERR] 国家标准: {e}")
    return items


def fetch_gov_sources():
    """抓取所有政府数据源"""
    items = []
    items.extend(fetch_gov_nhc())
    time.sleep(0.5)
    items.extend(fetch_gov_samr())
    time.sleep(0.5)
    items.extend(fetch_gov_gb())
    return items


# ===== 学术/配方数据 =====

def fetch_cnki():
    """抓取知网冷冻食品相关学术文章标题（仅作入口）"""
    items = []
    try:
        print("  [FETCH ACADEMIC] 中国知网")
        url = "https://kns.cnki.net/kns8/defaultresult/index"
        params = {"kw": "冷冻食品", "korder": "SU"}
        resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=20, verify=False)
        resp.raise_for_status()
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser') if BeautifulSoup else None
        if soup:
            count = 0
            for a in soup.select('a')[:60]:
                title = clean_html(a.get_text())
                href = a.get('href', '')
                if not title or len(title) < 10:
                    continue
                if '冷冻' not in title and '速冻' not in title:
                    continue
                if not href.startswith('http'):
                    href = urljoin(url, href)
                items.append({
                    "id": generate_id('知网', title, datetime.now().strftime('%Y-%m-%d')),
                    "type": "formulation",
                    "section": "formulation",
                    "date": datetime.now().strftime('%Y-%m-%d'),
                    "title": truncate_text(title, 80),
                    "source": "中国知网 CNKI",
                    "sourceUrl": href,
                    "image": "🧪",
                    "category": "",
                    "desc": f"学术文献：{title[:60]}... 点击查看详情",
                    "tags": ["学术", "冷冻食品"],
                })
                count += 1
                if count >= 5:
                    break
        print(f"  [OK] 中国知网: {len(items)} 条")
    except Exception as e:
        print(f"  [ERR] 中国知网: {e}")
    return items


# ===== 数据合并与保存 =====

def load_existing_data():
    """加载已有的 data.json"""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                return data.get('items', data.get('data', []))
        except (json.JSONDecodeError, IOError):
            pass
    return []


def merge_data(existing, new_items):
    """合并新旧数据，去重，保留人工编辑内容"""
    existing_map = {}
    for item in existing:
        eid = item.get('id', '')
        if eid:
            existing_map[eid] = item
    
    merged = list(existing)
    added = 0
    
    for new_item in new_items:
        nid = new_item.get('id', '')
        if nid and nid in existing_map:
            old = existing_map[nid]
            if new_item.get('sourceUrl'):
                old['sourceUrl'] = new_item['sourceUrl']
            if new_item.get('_imageUrl') and not old.get('_imageUrl'):
                old['_imageUrl'] = new_item['_imageUrl']
        else:
            merged.append(new_item)
            existing_map[nid] = new_item
            added += 1
    
    merged.sort(key=lambda x: x.get('date', ''), reverse=True)
    
    if len(merged) > MAX_TOTAL_ITEMS:
        curated = [i for i in merged if any(k in i for k in ['innovation', 'material', 'formula', 'notes'])]
        auto = [i for i in merged if i not in curated]
        merged = curated + auto
        merged = merged[:MAX_TOTAL_ITEMS]
    
    return merged, added


def save_data(items):
    """保存 data.json"""
    output = {
        "meta": {
            "updated": datetime.now().strftime('%Y-%m-%d'),
            "version": int(time.time()),
            "total": len(items),
            "auto_generated": True,
            "sources": [s['name'] for s in RSS_SOURCES] + [s['name'] for s in GOV_SOURCES] + [s['name'] for s in ACADEMIC_SOURCES],
        },
        "items": items,
    }
    if DATA_FILE.exists():
        backup = DATA_FILE.with_suffix('.json.bak')
        try:
            backup.write_text(DATA_FILE.read_text(encoding='utf-8'), encoding='utf-8')
        except Exception:
            pass
    
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  [SAVED] {DATA_FILE} ({len(items)} 条目)")


def main():
    print("=" * 60)
    print("  冻品情报站 - 数据自动采集 v2.0")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    print("\n[1/4] 加载已有数据...")
    existing = load_existing_data()
    print(f"  已有 {len(existing)} 条数据")
    
    print("\n[2/4] 采集 RSS 源...")
    all_new = []
    for source in RSS_SOURCES:
        items = fetch_rss_feed(source)
        all_new.extend(items)
        time.sleep(0.5)
    print(f"  RSS 共采集 {len(all_new)} 条")
    
    print("\n[3/4] 采集政府/合规数据...")
    gov_items = fetch_gov_sources()
    all_new.extend(gov_items)
    print(f"  政府数据共采集 {len(gov_items)} 条")
    
    print("\n[4/4] 采集学术/配方数据...")
    academic_items = fetch_cnki()
    all_new.extend(academic_items)
    print(f"  学术数据共采集 {len(academic_items)} 条")
    
    print(f"\n  本次共采集 {len(all_new)} 条新数据")
    
    print("\n[合并数据...]")
    merged, added = merge_data(existing, all_new)
    print(f"  新增 {added} 条，总计 {len(merged)} 条")
    
    save_data(merged)
    
    print("\n" + "=" * 60)
    print("  采集完成!")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
