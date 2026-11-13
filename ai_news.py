"""지금 뜨는 뉴스 — Claude(Haiku) 단계 (2026-11-13)

1) cluster_summaries(pages): 분야별 상위 사건 묶음마다 "무슨 일인가" 한 줄 요약 → 묶음["ai"]
   근거는 그 묶음에 든 언론사별 기사 제목뿐. 평가·전망·추측은 쓰지 않는다.
2) mark_books(rows): 책 기사마다 제목·요약에서 '소개된 책 이름'을 뽑아 row["ai_books"] 에 넣는다.
   trend_site.book_titles_of() 는 ai_books 가 있으면 정규식 대신 이것을 쓴다(예스24에서 찾은 책만 서가에 오르는 건 그대로).

키: 환경변수 ANTHROPIC_API_KEY (GitHub Secrets). 없거나 실패하면 아무것도 하지 않는다(사이트는 그대로).
캐시: history/ai_clusters.json (대표 제목별, 3일), history/ai_books.json (기사 주소별, 40일) — 같은 것은 다시 묻지 않는다.
한 번 실행에 Claude 호출은 최대 MAX_CALLS 번.
"""
import hashlib, json, os, re, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, "history")
C_CACHE = os.path.join(HIST, "ai_clusters.json")
B_CACHE = os.path.join(HIST, "ai_books.json")
MODEL = "claude-haiku-5-5"
MAX_CALLS = 10
BOOK_CALLS = 3   # 책 이름 뽑기는 한 번 실행에 3번까지만(첫 실행 밀린 몫은 다음 시간으로) — 사건 요약 몫을 남긴다
KST = timezone(timedelta(hours=9))
STATE = {"calls": 0, "tin": 0, "tout": 0, "err": ""}

SYS_CLUSTER = ("너는 통신사 데스크다. 사건 묶음마다 화면에 이미 보이는 '대표 제목'과, 같은 사건을 다룬 다른 언론사 기사의 제목·요약을 받는다. "
               "독자는 대표 제목을 이미 읽었다. 대표 제목에 없는 사실 가운데 가장 중요한 것 하나를 한 줄(45자 이내)로 보탠다: "
               "숫자·결과·이유·다음 일정·당사자 반응·배경 같은 것. 대표 제목의 낱말을 되풀이하거나 바꿔 말하지 않는다. "
               "여러 기사에 공통으로 나온 사실만 쓰고, 평가·전망·추측은 쓰지 않는다. 명사형이나 담백한 서술로. "
               '보탤 새 사실이 없으면 s 를 빈 문자열로. JSON 배열로만: [{"i": 0, "s": "..."}]')
SYS_BOOKS = ("너는 신문 책 지면 편집자다. 기사마다 제목과 요약을 받는다. 그 기사가 소개·서평·인터뷰로 다룬 책의 제목만 뽑는다. "
             "저자의 예전 책, 수상 경력으로만 나온 책, 문학상·행사·영화·전시 이름, 기사 제목의 일부 문구는 넣지 않는다. "
             "책 제목은 기사에 적힌 표기 그대로(부제는 빼고, 낫표·따옴표는 떼고). 책이 없으면 빈 배열. "
             'JSON 배열로만: [{"i": 0, "b": ["제목1", "제목2"]}]')


def _key():
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def available():
    return bool(_key()) and STATE["calls"] < MAX_CALLS and not STATE["err"].startswith("HTTP 401")


def _ask(system, text, max_tokens=3000):
    body = {"model": MODEL, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": text}],
            "thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}
    for attempt in range(3):
        STATE["calls"] += 1
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), method="POST",
                                     headers={"x-api-key": _key(), "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.load(r)
            u = d.get("usage") or {}
            STATE["tin"] += u.get("input_tokens") or 0
            STATE["tout"] += u.get("output_tokens") or 0
            t = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text").strip()
            m = re.search(r"\[.*\]", t, re.S)
            return json.loads(m.group(0)) if m else []
        except urllib.error.HTTPError as e:
            if e.code == 400 and "thinking" in body:
                body.pop("thinking", None); body.pop("output_config", None); continue
            STATE["err"] = f"HTTP {e.code}"
            if e.code in (429, 500, 529):
                time.sleep(6); continue
            return None
        except Exception as e:  # noqa: BLE001
            STATE["err"] = type(e).__name__
            return None
    return None


def _load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def _save(p, d):
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _ck(c):
    return "v2:" + hashlib.sha1(re.sub(r"\W", "", c["title"]).encode()).hexdigest()[:14]   # v2: 제목에 없는 사실만 보태는 방식


def cluster_summaries(pages, per_page=None, descs=None):
    """pages[c]['clusters'] 의 상위 묶음에 c['ai'] 를 붙인다. 돌려주는 값: (붙인 수, 새로 물은 수)"""
    per_page = per_page or {"전체": 10}
    cache = _load(C_CACHE)
    now = datetime.now(KST).isoformat()
    targets, seen = [], set()
    for name, p in pages.items():
        for c in (p.get("clusters") or [])[:per_page.get(name, 3)]:
            k = _ck(c)
            if k not in seen:
                seen.add(k); targets.append((k, c))
    todo = [(k, c) for k, c in targets if k not in cache]
    for s in range(0, len(todo), 12):
        if not available():
            break
        part = todo[s:s + 12]
        descs = descs or {}
        def one(i, c):
            ls = c["links"][:7]
            body = "\n".join(f"- {l['outlet']}: {l['title']}" + (f" | 요약: {descs[l['url']][:150]}" if descs.get(l.get("url")) else "") for l in ls)
            return f"[{i}] 대표 제목: {c['title']}\n{body}"
        text = "\n\n".join(one(i, c) for i, (k, c) in enumerate(part))
        res = _ask(SYS_CLUSTER, text, max_tokens=8000)
        if res is None:
            break
        got = {r.get("i"): str(r.get("s") or "").strip() for r in res if isinstance(r, dict)}
        for i, (k, c) in enumerate(part):
            if i in got:   # 빈 문자열도 저장(보탤 게 없다는 답 — 다시 묻지 않음)
                cache[k] = {"s": got[i][:70], "t": now}
    n = 0
    for name, p in pages.items():
        for c in p.get("clusters") or []:
            s = (cache.get(_ck(c)) or {}).get("s")
            if s:
                c["ai"] = s; n += 1
    lo = (datetime.now(KST) - timedelta(days=3)).isoformat()
    _save(C_CACHE, {k: v for k, v in cache.items() if v.get("t", "") >= lo})
    return n, len(todo)


def mark_books(rows, max_new=150):
    """책 기사 rows 에 row['ai_books'] (Claude가 뽑은 책 제목 목록)를 붙인다. 돌려주는 값: (붙인 기사 수, 새로 물은 수)"""
    cache = _load(B_CACHE)
    now = datetime.now(KST).isoformat()
    todo = [x for x in rows if x.get("url") and x["url"] not in cache][:max_new]
    start_calls = STATE["calls"]
    for s in range(0, len(todo), 30):
        if not available() or STATE["calls"] - start_calls >= BOOK_CALLS:
            break
        part = todo[s:s + 30]
        text = "\n".join(f"[{i}] {x['outlet']} | {x['title']} | {(x.get('desc') or '')[:200]}" for i, x in enumerate(part))
        res = _ask(SYS_BOOKS, text, max_tokens=10000)
        if res is None:
            break
        got = {r.get("i"): r.get("b") for r in res if isinstance(r, dict)}
        for i, x in enumerate(part):
            b = got.get(i)
            if isinstance(b, list):
                cache[x["url"]] = {"b": [str(t).strip()[:40] for t in b if 2 <= len(str(t).strip()) <= 40][:8], "t": now}
    n = 0
    for x in rows:
        c = cache.get(x.get("url") or "")
        if c is not None:
            x["ai_books"] = c["b"]; n += 1
    lo = (datetime.now(KST) - timedelta(days=40)).isoformat()
    _save(B_CACHE, {k: v for k, v in cache.items() if v.get("t", "") >= lo})
    return n, len(todo)


def status_line():
    cost = (STATE["tin"] * 0.10 + STATE["tout"] * 0.50) / 1e6
    return f"호출 {STATE['calls']}번 · 토큰 {STATE['tin']:,}/{STATE['tout']:,} · 약 ${cost:.4f}" + (f" · 오류 {STATE['err']}" if STATE["err"] else "")
