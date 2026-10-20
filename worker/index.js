// news-timer: 매시 3분(UTC = KST 같은 분)에 GitHub Actions의 update.yml을 실행시킨다.
// GitHub 예약 실행(cron)은 붐비면 몇 시간씩 건너뛰어서, 시계 역할을 Cloudflare에 맡긴다.
// 공개 주소(workers.dev)는 꺼 두었다 — 밖에서 이 Worker를 부를 방법이 없다.
export default {
  async scheduled(event, env, ctx) {
    const r = await fetch(
      "https://api.github.com/repos/k7book/news/actions/workflows/update.yml/dispatches",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GH_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "news-timer",
        },
        body: JSON.stringify({ ref: "main" }),
      },
    );
    if (r.status !== 204) {
      throw new Error(`GitHub 응답 ${r.status}: ${(await r.text()).slice(0, 200)}`);
    }
  },
};
