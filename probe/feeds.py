import re, json, urllib.request, urllib.robotparser, urllib.parse, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
UA = "Mozilla/5.0 (compatible; news-trend-bot/1.0)"
SITES = {
 "뉴스타파":"https://newstapa.org","셜록":"https://www.neosherlock.com","뉴스톱":"https://www.newstof.com",
 "미디어오늘":"https://www.mediatoday.co.kr","시사IN":"https://www.sisain.co.kr","한겨레21":"https://h21.hani.co.kr",
 "주간경향":"https://weekly.khan.co.kr","프레시안":"https://www.pressian.com","북저널리즘":"https://www.bookjournalism.com",
 "뉴닉":"https://newneek.co","비마이너":"https://www.beminor.com","일다":"https://www.ildaro.com","뉴스민":"https://www.newsmin.co.kr",
 "뉴스하다":"https://www.newshada.org","뉴스어디":"https://www.newswhere.kr","코트워치":"https://www.courtwatch.kr",
 "단비뉴스":"https://www.danbinews.com","바이라인네트워크":"https://byline.network","더스쿠프":"https://www.thescoop.co.kr",
 "얼룩소":"https://alook.so","피렌체의식탁":"https://www.firenzedt.com","참세상":"https://www.newscham.net",
 "뉴스버스":"https://www.newsverse.kr","ㅍㅍㅅㅅ":"https://ppss.kr","뉴스페퍼민트":"https://newspeppermint.com",
 "어피티":"https://uppity.co.kr","코리아엑스포제":"https://koreaexpose.com","미디어스":"https://www.mediaus.co.kr",
}
def get(u, n=400000):
    r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=20)
    return r.geturl(), r.read(n).decode("utf-8", "replace")
def feedinfo(u):
    try:
        fu, t = get(u, 3000000)
    except Exception as e:
        return None
    if "<rss" not in t[:3000] and "<feed" not in t[:3000]: return None
    t2 = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", t); t2 = re.sub(r"&(?!#?\w+;)", "&amp;", t2)
    try: root = ET.fromstring(t2)
    except Exception as e: return {"url": fu, "err": "parse "+str(e)[:60]}
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    def tx(it, *tags):
        for tg in tags:
            e = it.find(tg)
            if e is not None and (e.text or "").strip(): return e.text.strip()
        return ""
    dates = [tx(i, "pubDate", "{http://purl.org/dc/elements/1.1/}date", "{http://www.w3.org/2005/Atom}updated") for i in items]
    full = max([len(tx(i, "{http://purl.org/rss/1.0/modules/content/}encoded")) for i in items] or [0])
    desc = max([len(re.sub("<[^>]+>", "", tx(i, "description"))) for i in items] or [0])
    return {"url": fu, "n": len(items), "first": dates[0][:25] if dates else "", "last": dates[-1][:25] if dates else "",
            "t0": tx(items[0], "title", "{http://www.w3.org/2005/Atom}title")[:40] if items else "", "full": full, "desc": desc}
def probe(name, base):
    out = {"name": name}
    try:
        fu, home = get(base)
        out["home"] = fu
        out["gen"] = (re.findall(r'name="generator" content="([^"]+)"', home) or [""])[0][:30]
        alts = re.findall(r'<link[^>]+type="application/(?:rss|atom)\+xml"[^>]*>', home)
        cands = [urllib.parse.urljoin(fu, h) for a in alts for h in re.findall(r'href="([^"]+)"', a) if "comment" not in h]
    except Exception as e:
        out["home_err"] = str(e)[:80]; cands = []; fu = base
    rp = urllib.robotparser.RobotFileParser()
    try:
        _, rt = get(urllib.parse.urljoin(base, "/robots.txt")); rp.parse(rt.splitlines()); out["robots"] = "ok"
        out["dis"] = [l.strip() for l in rt.splitlines() if l.lower().startswith("disallow")][:6]
    except Exception as e:
        rp.parse([]); out["robots"] = "none " + str(e)[:40]
    for p in ["/feed", "/rss/allArticle.xml", "/rss", "/rss.xml", "/feed.xml", "/index.xml"]:
        cands.append(urllib.parse.urljoin(fu, p))
    seen = set()
    for c in cands:
        if c in seen: continue
        seen.add(c)
        f = feedinfo(c)
        if f:
            f["allowed"] = rp.can_fetch(UA, c); out["feed"] = f; break
    return out
with ThreadPoolExecutor(8) as ex:
    res = list(ex.map(lambda kv: probe(*kv), SITES.items()))
for r in res: print("PROBE", json.dumps(r, ensure_ascii=False))
# rerun 1792893254
