import json, re, urllib.request, urllib.parse, urllib.robotparser
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trend_site/0.2 (personal)"
def get(u, t=20):
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ko"})
    with urllib.request.urlopen(req, timeout=t) as r:
        return r.status, r.read().decode("utf-8", "ignore")
out = {"google": [], "robots": {}}
for t in json.load(open("titles.json"))[:30]:
    try:
        st, b = get("https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode({"q": "intitle:" + t, "langRestrict": "ko", "maxResults": 3, "orderBy": "newest"}))
        d = json.loads(b); it = (d.get("items") or [{}])
        v = it[0].get("volumeInfo", {}) if it else {}
        out["google"].append([t, d.get("totalItems"), v.get("title"), v.get("authors"), v.get("publisher"), v.get("publishedDate"), (v.get("imageLinks") or {}).get("thumbnail")])
    except Exception as e:
        out["google"].append([t, "ERR " + str(e)[:60]])
for site, path in [("https://www.aladin.co.kr", "/search/wsearchresult.aspx?SearchWord=x"), ("https://www.yes24.com", "/Product/Search?query=x"),
                   ("https://search.kyobobook.co.kr", "/search?keyword=x"), ("https://www.nl.go.kr", "/seoji/"), ("https://seoji.nl.go.kr", "/"), ("https://search.daum.net", "/search?w=book&q=x")]:
    try:
        st, b = get(site + "/robots.txt", 15)
        rp = urllib.robotparser.RobotFileParser(); rp.parse(b.splitlines())
        out["robots"][site] = [rp.can_fetch("*", site + path), b[:500]]
    except Exception as e:
        out["robots"][site] = ["ERR", str(e)[:80]]
json.dump(out, open("probe_out.json", "w"), ensure_ascii=False, indent=1)
