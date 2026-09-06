// T3.1 性能基线 01：登录接口（POST /api/system/login/basic）。
// 注意：登录有专属限流（默认 50/h/用户），压测环境需在 config.yml 放开：
//   DEFAULT_THROTTLE_RATES: { login: '100000/h' }
// 建议档位：低 VU（默认 5）短时测定；迭代内不做 sleep，测纯登录开销。
import http from 'k6/http';
import { check, group } from 'k6';
import { Counter } from 'k6/metrics';
import {
  apiUrl,
  baseOptions,
  checkBusinessCode,
  makeSummary,
  summaryPath,
  USERNAME,
  PASSWORD,
} from './lib.js';

const throttled = new Counter('login_throttled');

export const options = baseOptions(5, '30s');

export default function () {
  group('01 basic-login', () => {
    const res = http.post(
      apiUrl('/api/system/login/basic'),
      JSON.stringify({ username: USERNAME, password: PASSWORD }),
      { headers: { 'Content-Type': 'application/json' } },
    );
    if (res.status === 429 || res.json('code') === 999) {
      throttled.add(1); // 命中限流：本轮数据无效，检查压测环境限流配置
      return;
    }
    check(res, {
      'login HTTP 200': (r) => r.status === 200,
      'login issued access': (r) => !!r.json('data.access'),
    });
    checkBusinessCode(res);
  });
}

export function handleSummary(data) {
  return { [summaryPath('01-login')]: JSON.stringify(makeSummary(data), null, 2) };
}
