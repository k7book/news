// 날씨 페이지 '비구름 레이더' — 기상청 레이더 합성영상(공공데이터포털 RadarImgInfoService)
//  GET /api/radar           → 최근 2시간 영상 목록 {frames:[{t:"YYYYMMDDHHMM", src:"/api/radar?img=..."}]}
//  GET /api/radar?img=URL   → 기상청 영상 한 장을 그대로 전달 (http 영상을 https 페이지에 띄우기 위해)
// 열쇠는 Cloudflare Pages 비밀값 DATA_GO_KR_KEY (공공데이터포털 일반 인증키 Decoding). 화면에는 드러나지 않는다.
const API = "https://apis.data.go.kr/1360000/RadarImgInfoService/getCmpImg";
const IMG_OK = /^https?:\/\/[a-z0-9.-]*(kma|weather)\.go\.kr\/[\w./-]*RDR_CMP_WRC_\d{12}\.png$/i;
const SPAN_MIN = 120, STEP_MIN = 10;

const kstDate = (ms) => new Date(ms + 9 * 3600e3).toISOString().slice(0, 10).replace(/-/g, "");
const json = (o, status = 200, maxAge = 180) =>
  new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": `public, max-age=${status === 200 ? maxAge : 60}` } });

async function listDay(key, day) {
  const q = new URLSearchParams({ serviceKey: key, pageNo: "1", numOfRows: "10", dataType: "JSON", data: "CMP_WRC", time: day });
  const r = await fetch(`${API}?${q}`, { headers: { "User-Agent": "news-radar/1.0" } });
  const t = await r.text();
  if (!r.ok) throw new Error(`기상청 응답 ${r.status}`);
  if (!t.trim().startsWith("{")) {  // 키 오류 등은 XML로 온다
    const m = t.match(/<returnAuthMsg>([^<]+)|<resultMsg>([^<]+)/);
    throw new Error(m ? (m[1] || m[2]) : "알 수 없는 응답");
  }
  const d = JSON.parse(t), h = d?.response?.header || {};
  if (h.resultCode && h.resultCode !== "00") {
    if (h.resultCode === "03") return [];  // NO_DATA
    throw new Error(`${h.resultCode} ${h.resultMsg || ""}`.trim());
  }
  return [...t.matchAll(/https?:\\?\/\\?\/[^\s,\]"']+?RDR_CMP_WRC_\d{12}\.png/gi)].map((m) => m[0].replace(/\\\//g, "/"));
}

async function frames(env) {
  const key = (env.DATA_GO_KR_KEY || "").trim();
  if (!key) throw new Error("열쇠(DATA_GO_KR_KEY)가 없습니다");
  const now = Date.now();
  let urls = await listDay(key, kstDate(now));
  if (urls.length < SPAN_MIN / 5 + 2) urls = (await listDay(key, kstDate(now - 864e5)).catch(() => [])).concat(urls);
  const byT = new Map();
  for (const u of urls) byT.set(u.match(/(\d{12})\.png$/i)[1], u);
  const ts = [...byT.keys()].sort();
  if (!ts.length) return [];
  const toMin = (s) => Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8), +s.slice(8, 10), +s.slice(10, 12)) / 6e4;
  const last = toMin(ts[ts.length - 1]);
  const keep = ts.filter((s) => { const m = toMin(s); return last - m <= SPAN_MIN && (last - m) % STEP_MIN === 0; });
  return keep.map((t) => ({ t, src: `/api/radar?img=${encodeURIComponent(byT.get(t))}` }));
}

export async function onRequestGet({ request, env, waitUntil }) {
  const url = new URL(request.url), img = url.searchParams.get("img");
  const cache = caches.default, ck = new Request(url.toString());
  const hit = await cache.match(ck);
  if (hit) return hit;
  let res;
  if (img) {
    if (!IMG_OK.test(img)) return new Response("bad image", { status: 400 });
    const r = await fetch(img, { headers: { "User-Agent": "news-radar/1.0" } });
    if (!r.ok || !/image/.test(r.headers.get("content-type") || "")) return new Response("image not found", { status: 404 });
    res = new Response(r.body, { headers: { "content-type": "image/png", "cache-control": "public, max-age=86400, immutable" } });
  } else {
    try {
      res = json({ frames: await frames(env), source: "기상청 레이더 합성영상" });
    } catch (e) {
      return json({ frames: [], error: String(e.message || e) }, 200, 60);
    }
  }
  waitUntil(cache.put(ck, res.clone()));
  return res;
}
