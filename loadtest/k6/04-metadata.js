// T3.1 性能基线 04：元数据接口（search-columns / search-fields / with_meta=1 内联）。
// TD-05 已知热点：页面首开依赖元数据。本脚本同时测三个面：
//   - search-columns / search-fields：分离请求（T3.2 优化前的旧路径）
//   - list?with_meta=1：内联元数据（T3.2 优化后的单请求路径），用于对比验证优化收益
import http from 'k6/http';
import {group} from 'k6';
import {Trend} from 'k6/metrics';
import {
    apiUrl,
    baseOptions,
    checkBusinessCode,
    jsonHeaders,
    LIST_PAGE,
    LIST_PATH,
    LIST_SIZE,
    loginOnce,
    makeSummary,
    summaryPath,
} from './lib.js';

export const options = baseOptions(20, '1m');

// 三个变体各自的耗时档位：k6 v2 移除 group 子指标（group:::）后，
// 只有自选 Trend 才能在 summary 中分变体登记（makeSummary 收进 trends 字段）。
// 基线回归按这三个 Trend 的 P95 分别判定（见 loadtest/baseline.json）。
const columnsTrend = new Trend('meta_columns_duration', true);
const fieldsTrend = new Trend('meta_fields_duration', true);
const withMetaTrend = new Trend('meta_with_meta_duration', true);

export function setup() {
    return {token: loginOnce()};
}

export default function (data) {
    group('04a search-columns', () => {
        const res = http.get(apiUrl(`${LIST_PATH}/search-columns`), {headers: jsonHeaders(data.token)});
        checkBusinessCode(res);
        columnsTrend.add(res.timings.duration);
    });
    group('04b search-fields', () => {
        const res = http.get(apiUrl(`${LIST_PATH}/search-fields`), {headers: jsonHeaders(data.token)});
        checkBusinessCode(res);
        fieldsTrend.add(res.timings.duration);
    });
    group('04c list-with-meta', () => {
        const res = http.get(
            apiUrl(`${LIST_PATH}?page=${LIST_PAGE}&size=${LIST_SIZE}&with_meta=1`),
            {headers: jsonHeaders(data.token)},
        );
        checkBusinessCode(res);
        withMetaTrend.add(res.timings.duration);
    });
}

export function handleSummary(data) {
    return {[summaryPath('04-metadata')]: JSON.stringify(makeSummary(data), null, 2)};
}
