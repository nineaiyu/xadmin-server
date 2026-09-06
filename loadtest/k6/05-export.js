// T3.1 性能基线 05：导出接口（GET {LIST_PATH}/export-data?type=xlsx）。
// 导出不分页，耗时随数据规模线性增长：必须先按 docs/ops/performance-baseline.md 固定种子规模，
// 并用 EXPORT_FILTER（如 `&username=perf_`）把导出范围绑定到种子数据，保证多轮结果可比。
import http from 'k6/http';
import { check, group } from 'k6';
import {
  apiUrl,
  baseOptions,
  EXPORT_FILTER,
  EXPORT_TYPE,
  jsonHeaders,
  LIST_PATH,
  loginOnce,
  makeSummary,
  summaryPath,
} from './lib.js';

export const options = baseOptions(5, '1m');

export function setup() {
  return { token: loginOnce() };
}

export default function (data) {
  group('05 export-data', () => {
    const res = http.get(
      apiUrl(`${LIST_PATH}/export-data?type=${EXPORT_TYPE}${EXPORT_FILTER}`),
      { headers: jsonHeaders(data.token) },
    );
    check(res, {
      'export HTTP 200': (r) => r.status === 200,
      'export body not empty': (r) => r.body && r.body.length > 0,
    });
  });
}

export function handleSummary(data) {
  return { [summaryPath('05-export')]: JSON.stringify(makeSummary(data), null, 2) };
}
