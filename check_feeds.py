#!/usr/bin/env python3
"""v0.2 점검 스크립트 — 분야별 RSS 후보 검증 + 포털 인기뉴스 robots.txt 확인.
표준 라이브러리만 사용. 실행: python check_feeds.py  → feeds_checked.json, feeds_report.md 생성
"""
import json, re, sys, time, urllib.request, urllib.robotparser, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from trend_site import item_date

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trend_site/0.2 (personal, +hourly)"
TIMEOUT = 15

# 언론사 × 분야 후보. 공식 RSS 안내 페이지에서 확인한 것은 (confirmed), 과거 목록·관행 주소는 (guess)
CANDIDATES = {
 "연합뉴스": {
  "전체": "https://www.yna.co.kr/rss/news.xml", "정치": "https://www.yna.co.kr/rss/politics.xml",
  "경제": "https://www.yna.co.kr/rss/economy.xml", "사회": "https://www.yna.co.kr/rss/society.xml",
  "스포츠": "https://www.yna.co.kr/rss/sports.xml", "연예": "https://www.yna.co.kr/rss/entertainment.xml"},
 "한겨레": {
  "전체": "https://www.hani.co.kr/rss/", "정치": "https://www.hani.co.kr/rss/politics/",
  "경제": "https://www.hani.co.kr/rss/economy/", "사회": "https://www.hani.co.kr/rss/society/",
  "스포츠": "https://www.hani.co.kr/rss/sports/", "연예": "https://www.hani.co.kr/rss/culture/"},
 "조선일보": {
  "전체": "https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml",
  "정치": "https://www.chosun.com/arc/outboundfeeds/rss/category/politics/?outputType=xml",
  "경제": "https://www.chosun.com/arc/outboundfeeds/rss/category/economy/?outputType=xml",
  "사회": "https://www.chosun.com/arc/outboundfeeds/rss/category/national/?outputType=xml",
  "스포츠": "https://www.chosun.com/arc/outboundfeeds/rss/category/sports/?outputType=xml",
  "연예": "https://www.chosun.com/arc/outboundfeeds/rss/category/entertainments/?outputType=xml"},
 "동아일보": {
  "전체": "https://rss.donga.com/total.xml", "정치": "https://rss.donga.com/politics.xml",
  "경제": "https://rss.donga.com/economy.xml", "사회": "https://rss.donga.com/national.xml",
  "스포츠": "https://rss.donga.com/sports.xml", "연예": "https://rss.donga.com/culture.xml"},
 "경향신문": {  # confirmed 2026 (khan.co.kr/help/help_rss.html)
  "전체": "https://www.khan.co.kr/rss/rssdata/total_news.xml", "정치": "https://www.khan.co.kr/rss/rssdata/politic_news.xml",
  "경제": "https://www.khan.co.kr/rss/rssdata/economy_news.xml", "사회": "https://www.khan.co.kr/rss/rssdata/society_news.xml",
  "스포츠": "https://www.khan.co.kr/rss/rssdata/kh_sports.xml", "연예": "https://www.khan.co.kr/rss/rssdata/kh_entertainment.xml"},
 "매일경제": {
  "전체": "https://www.mk.co.kr/rss/30000001/", "정치": "https://www.mk.co.kr/rss/30200030/",
  "경제": "https://www.mk.co.kr/rss/30100041/", "사회": "https://www.mk.co.kr/rss/50400012/",
  "스포츠": "https://www.mk.co.kr/rss/50600001/", "연예": "https://www.mk.co.kr/rss/30000023/"},
 "한국경제": {  # confirmed 2026 (hankyung.com/feed)
  "전체": "https://www.hankyung.com/feed/all-news", "정치": "https://www.hankyung.com/feed/politics",
  "경제": "https://www.hankyung.com/feed/economy", "사회": "https://www.hankyung.com/feed/society",
  "스포츠": "https://www.hankyung.com/feed/sports", "연예": "https://www.hankyung.com/feed/entertainment"},
 "SBS": {
  "전체": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=01&plink=RSSREADER",
  "정치": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=01&plink=RSSREADER",
  "경제": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=02&plink=RSSREADER",
  "사회": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=03&plink=RSSREADER",
  "스포츠": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=09&plink=RSSREADER",
  "연예": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=14&plink=RSSREADER"},
 "JTBC": {
  "전체": "https://fs.jtbc.co.kr/RSS/newsflash.xml", "정치": "https://fs.jtbc.co.kr/RSS/politics.xml",
  "경제": "https://fs.jtbc.co.kr/RSS/economy.xml", "사회": "https://fs.jtbc.co.kr/RSS/society.xml",
  "스포츠": "https://fs.jtbc.co.kr/RSS/sports.xml", "연예": "https://fs.jtbc.co.kr/RSS/entertainment.xml"},
 "노컷뉴스": {
  "전체": "https://rss.nocutnews.co.kr/nocutnews.xml", "정치": "https://rss.nocutnews.co.kr/nocutpolitics.xml",
  "경제": "https://rss.nocutnews.co.kr/nocuteconomy.xml", "사회": "https://rss.nocutnews.co.kr/nocutsocial.xml",
  "스포츠": "https://rss.nocutnews.co.kr/nocutsports.xml", "연예": "https://rss.nocutnews.co.kr/nocutenter.xml"},
 "오마이뉴스": {
  "전체": "https://rss.ohmynews.com/rss/ohmynews.xml", "정치": "https://rss.ohmynews.com/rss/politics.xml",
  "경제": "https://rss.ohmynews.com/rss/economy.xml", "사회": "https://rss.ohmynews.com/rss/society.xml",
  "스포츠": "https://rss.ohmynews.com/rss/sports.xml", "연예": "https://rss.ohmynews.com/rss/ent.xml"},
 "국민일보": {  # confirmed (kmib.co.kr/rss)
  "전체": "https://www.kmib.co.kr/rss/data/kmibRssAll.xml", "정치": "https://www.kmib.co.kr/rss/data/kmibPolRss.xml",
  "경제": "https://www.kmib.co.kr/rss/data/kmibEcoRss.xml", "사회": "https://www.kmib.co.kr/rss/data/kmibSocRss.xml",
  "스포츠": "https://www.kmib.co.kr/rss/data/kmibSpoRss.xml", "연예": "https://www.kmib.co.kr/rss/data/kmibEntRss.xml"},
 "세계일보": {  # confirmed (segye.com/rssInfo)
  "전체": "https://www.segye.com/Articles/RSSList/segye_recent.xml", "정치": "https://www.segye.com/Articles/RSSList/segye_politic.xml",
  "경제": "https://www.segye.com/Articles/RSSList/segye_economy.xml", "사회": "https://www.segye.com/Articles/RSSList/segye_society.xml",
  "스포츠": "https://www.segye.com/Articles/RSSList/segye_sports.xml", "연예": "https://www.segye.com/Articles/RSSList/segye_entertainment.xml"},
 "구글뉴스": {
  "전체": "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko",
  "정치": "https://news.google.com/rss/headlines/section/topic/NATION?hl=ko&gl=KR&ceid=KR:ko",
  "경제": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko",
  "사회": "https://news.google.com/rss/headlines/section/topic/NATION?hl=ko&gl=KR&ceid=KR:ko",
  "스포츠": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=ko&gl=KR&ceid=KR:ko",
  "연예": "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=ko&gl=KR&ceid=KR:ko"},
 "구글트렌드": {"전체": "https://trends.google.com/trending/rss?geo=KR"},
}

PORTALS = {  # 네이트·네이버는 2026-10-19 robots 전체 차단 확인 → 제외. 줌 랭킹 주소 후보들
 "줌 /ranking": "https://news.zum.com/ranking",
 "줌 /rankings": "https://news.zum.com/rankings",
 "줌 /ranking/all": "https://news.zum.com/ranking/all",
 "줌 메인": "https://news.zum.com/",
}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status, r.read(), r.headers.get("Content-Type", "")


def check_feed(outlet, cat, url):
    row = {"outlet": outlet, "category": cat, "url": url, "ok": False}
    try:
        t0 = time.time(); status, body, ctype = get(url); row["ms"] = int((time.time() - t0) * 1000)
        row["status"] = status
        root = ET.fromstring(body)
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        row["items"] = len(items)
        title = root.find(".//channel/title")
        row["title"] = (title.text or "").strip()[:60] if title is not None else ""
        dates = [d for d in (item_date(it) for it in items[:30]) if d]
        if items and not dates:  # 날짜를 못 읽은 피드: 첫 item 원문을 남겨 둔다
            row["sample"] = ET.tostring(items[0], encoding="unicode")[:400]
        row["with_date"] = len(dates)
        row["latest"] = max(dates).isoformat() if dates else None
        row["ok"] = len(items) > 0 and len(dates) > 0
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {str(e)[:80]}"
    return row


def check_portal(name, url):
    row = {"name": name, "url": url}
    from urllib.parse import urlsplit
    base = "{0.scheme}://{0.netloc}".format(urlsplit(url))
    try:
        st, body, _ = get(base + "/robots.txt")
        txt = body.decode("utf-8", "ignore")
        rp = urllib.robotparser.RobotFileParser(); rp.parse(txt.splitlines())
        row["robots_allows"] = rp.can_fetch("*", url)
        row["robots_excerpt"] = "\n".join(l for l in txt.splitlines() if l.strip() and not l.startswith("#"))[:600]
    except Exception as e:
        row["robots_error"] = f"{type(e).__name__}: {str(e)[:80]}"
    try:
        st, body, ctype = get(url)
        row["page_status"] = st; row["page_bytes"] = len(body)
        row["has_ranking_markup"] = bool(re.search(r"rank|ranking|많이 본|화제", body.decode("utf-8", "ignore"), re.I))
    except Exception as e:
        row["page_error"] = f"{type(e).__name__}: {str(e)[:80]}"
    return row


def main():
    jobs = [(o, c, u) for o, cats in CANDIDATES.items() for c, u in cats.items()]
    with ThreadPoolExecutor(8) as ex:
        feeds = list(ex.map(lambda j: check_feed(*j), jobs))
    portals = [check_portal(n, u) for n, u in PORTALS.items()]
    json.dump({"checked": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "feeds": feeds, "portals": portals},
              open("feeds_checked.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    ok = [f for f in feeds if f["ok"]]
    lines = [f"# RSS·포털 점검 결과 ({time.strftime('%Y-%m-%d %H:%M')})", "",
             f"피드 {len(ok)}/{len(feeds)} 성공", "", "| 언론사 | 분야 | 결과 | 건수 | 날짜有 | 최신 | 채널명/오류 |", "|---|---|---|---|---|---|---|"]
    for f in feeds:
        res = "OK" if f["ok"] else "FAIL"
        lines.append(f"| {f['outlet']} | {f['category']} | {res} | {f.get('items','')} | {f.get('with_date','')} | {str(f.get('latest') or '')[:16]} | {f.get('title') or f.get('error','')} |")
    lines += ["", "## 포털 인기뉴스", "", "| 포털 | robots 허용 | 페이지 | 비고 |", "|---|---|---|---|"]
    for p in portals:
        lines.append(f"| {p['name']} | {p.get('robots_allows', p.get('robots_error'))} | {p.get('page_status', p.get('page_error'))} | {'랭킹 마크업 있음' if p.get('has_ranking_markup') else ''} |")
    for p in portals:
        lines += ["", f"### {p['name']} robots.txt", "```", p.get("robots_excerpt", p.get("robots_error", "")), "```"]
    bad = [f for f in feeds if f.get("sample")]
    if bad:
        lines += ["", "## 날짜를 못 읽은 피드의 item 원문 (파서 수정용)"]
        for f in bad:
            lines += ["", f"### {f['outlet']}·{f['category']}", "```xml", f["sample"], "```"]
    open("feeds_report.md", "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))
    # 성공한 피드로 feeds.json 초안
    draft = [{"name": f"{f['outlet']}·{f['category']}", "outlet": f["outlet"], "category": f["category"], "url": f["url"]} for f in ok]
    json.dump(draft, open("feeds.draft.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nfeeds.draft.json: {len(draft)}개 피드")


if __name__ == "__main__":
    main()
