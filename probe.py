import json, re, urllib.request, urllib.parse
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trend_site/0.2 (personal)"
def get(u):
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ko"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")
out = {}
for t in ["빛으로 쓴 편지", "인생의 효율", "푸드테크 수익의 조건", "평행우주"]:
    b = get("https://search.kyobobook.co.kr/search?" + urllib.parse.urlencode({"keyword": t, "gbCode": "TOT", "target": "total"}))
    i = b.find('data-bid="97')
    blk = b[i:i+16000]
    j = blk.find("prod_author")
    out[t] = {"names": re.findall(r'data-bid="(97\d{11})" data-name="([^"]+)"', b)[:5],
              "author_chunk": re.sub(r"\s+", " ", blk[max(0, j-300):j+1800]) if j >= 0 else "",
              "img": re.findall(r'https://contents\.kyobobook\.co\.kr/[^"\']+', b)[:3]}
    y = get("https://www.yes24.com/Product/Search?" + urllib.parse.urlencode({"domain": "BOOK", "query": t}))
    out[t]["yes24_names"] = [re.sub(r"<[^>]+>", "", x) for x in re.findall(r'class="gd_name"[^>]*>(.*?)</a>', y, re.S)[:5]]
json.dump(out, open("probe_out.json", "w"), ensure_ascii=False, indent=1)
