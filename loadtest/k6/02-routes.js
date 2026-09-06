// T3.1 性能基线 02：菜单/路由接口（GET /api/system/routes，白名单路由）。
// 该接口每次请求重建菜单树并使用 MagicCacheData 缓存，可同时观察缓存命中/未命中两档表现。
import http from 'k6/http';
import { group } from 'k6';
import {
  apiUrl,
  baseOptions,
  checkBusinessCode,
  jsonHeaders,
  loginOnce,
  makeSummary,
  summaryPath,
} from './lib.js';

export const options = baseOptions(20, '1m');

export function setup() {
  return { token: loginOnce() };
}

export default function (data) {
  group('02 routes', () => {
    const res = http.get(apiUrl('/api/system/routes'), { headers: jsonHeaders(data.token) });
    checkBusinessCode(res);
  });
}

export function handleSummary(data) {
  return { [summaryPath('02-routes')]: JSON.stringify(makeSummary(data), null, 2) };
}
