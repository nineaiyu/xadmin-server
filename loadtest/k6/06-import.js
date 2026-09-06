// T3.1 性能基线 06：导入接口（POST {LIST_PATH}/import-data?action=...&task=false 同步链路）。
// 默认 update 模式：setup 拉取一页种子用户主键，每迭代更新 IMPORT_ROWS 行 nickname（不增长数据）。
// create 模式（IMPORT_MODE=create）每迭代新增行，仅适合短时测定，跑完需重置种子数据。
// task=false 固定走同步链路（异步 Celery 链路的耗时受 worker 影响不入基线）。
import http from 'k6/http';
import exec from 'k6/execution';
import { check, group } from 'k6';
import {
  apiUrl,
  baseOptions,
  IMPORT_MODE,
  IMPORT_ROWS,
  jsonHeaders,
  LIST_PATH,
  loginOnce,
  makeSummary,
  summaryPath,
} from './lib.js';

export const options = baseOptions(5, '1m');

export function setup() {
  const token = loginOnce();
  const pks = [];
  if (IMPORT_MODE === 'update') {
    const res = http.get(apiUrl(`${LIST_PATH}?page=1&size=100`), {
      headers: jsonHeaders(token),
    });
    const rows = (res.json('data.results') || []).map((row) => row.pk);
    if (!rows.length) {
      throw new Error(
        'update 模式需要已有数据：先执行 python loadtest/seed_users.py 创建种子用户，或改用 IMPORT_MODE=create',
      );
    }
    pks.push(...rows);
  }
  return { token, pks };
}

export default function (data) {
  group('06 import-data', () => {
    let payload;
    let url;
    if (IMPORT_MODE === 'update') {
      const iter = exec.scenario.iterationInTest;
      payload = [];
      for (let i = 0; i < IMPORT_ROWS; i++) {
        const pk = data.pks[(iter * IMPORT_ROWS + i) % data.pks.length];
        payload.push({ pk, nickname: `perf-upd-${iter}-${i}` });
      }
      url = apiUrl(`${LIST_PATH}/import-data?action=update&task=false`);
    } else {
      const iter = exec.scenario.iterationInTest;
      payload = [];
      for (let i = 0; i < IMPORT_ROWS; i++) {
        payload.push({ username: `perf_${iter}_${i}`, nickname: '压测用户' });
      }
      url = apiUrl(`${LIST_PATH}/import-data?action=create&task=false`);
    }
    const res = http.post(url, JSON.stringify(payload), { headers: jsonHeaders(data.token) });
    check(res, {
      'import HTTP 200': (r) => r.status === 200,
      'import code 1000': (r) => r.json('code') === 1000,
    });
  });
}

export function handleSummary(data) {
  return { [summaryPath('06-import')]: JSON.stringify(makeSummary(data), null, 2) };
}
