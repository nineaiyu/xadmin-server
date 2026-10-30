// k6 验证码挑战压测（发布前在独立环境执行，不进 CI）
//
//   k6 run -e BASE_URL=https://example.com -e SCENE=login -e ACTION=identity.login.basic loadtest/captcha_k6.js
//
// 只压 challenge（出图/缓存路径）；verify 需要真实答案，不在本脚本内。
// 期望：0 个 5xx；429 是 30/min 限流，需按并发/来源 IP 评估是否命中。
import http from "k6/http";
import { check } from "k6";
import { Rate } from "k6/metrics";

const base = __ENV.BASE_URL || "http://127.0.0.1:8896";
const scene = __ENV.SCENE || "login";
const action = __ENV.ACTION || "identity.login.basic";
const errors = new Rate("captcha_5xx");

export const options = {
  scenarios: {
    challenge: {
      executor: "ramping-vus",
      startVUs: 1,
      stages: [
        { duration: "30s", target: 10 },
        { duration: "1m", target: 50 },
        { duration: "30s", target: 0 }
      ]
    }
  },
  thresholds: {
    captcha_5xx: ["rate==0"],
    http_req_failed: ["rate<0.05"]
  }
};

export default function () {
  const response = http.post(
    `${base}/api/identity/auth/captcha/challenge`,
    JSON.stringify({ scene, action }),
    { headers: { "Content-Type": "application/json" }, tags: { endpoint: "challenge" } }
  );
  errors.add(response.status >= 500);
  check(response, {
    "status is 200 or throttled 429": r => r.status === 200 || r.status === 429
  });
}
