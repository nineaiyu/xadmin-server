// T3.1 性能基线 04：元数据接口（search-columns / search-fields / with_meta=1 内联）。
//
// 变体口径（VARIANT 环境变量，见 docs/ops/performance-baseline.md §二/§六）：
//   - columns / fields：纯元数据端点。**元数据目标（P95 < 60ms）以这两个变体为准**——
//     混跑时 list?with_meta=1（实为「列表 + 内联元数据」，与 03-list 同域、成本高一个量级）
//     会在同一队列里把纯元数据的 P95 拉高，目标口径失真；
//   - with_meta：页面首开的单请求路径（列表 + 内联元数据），单独登记、不与纯元数据同目标；
//   - all（默认）：三请求混跑的历史口径，仅用于人工对比「首开三请求」体验，不进标准跑批。
//
// 结果文件按变体落盘（04-metadata-<variant>.json），基线快照逐变体独立判定。
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

const VARIANTS = ['all', 'columns', 'fields', 'with_meta'];
const VARIANT = (__ENV.VARIANT || 'all').toLowerCase();

if (!VARIANTS.includes(VARIANT)) {
    throw new Error(`VARIANT 非法：${VARIANT}（可选：${VARIANTS.join(' / ')}）`);
}

export const options = baseOptions(20, '1m');

// 三个变体各自的耗时档位：k6 v2 移除 group 子指标（group:::）后，
// 只有自选 Trend 才能在 summary 中分变体登记（makeSummary 收进 trends 字段）。
// 仅混跑档（all）需要 Trend 分档——单变体跑批用例级 P95 就是该变体口径。
const columnsTrend = new Trend('meta_columns_duration', true);
const fieldsTrend = new Trend('meta_fields_duration', true);
const withMetaTrend = new Trend('meta_with_meta_duration', true);

export function setup() {
    return {token: loginOnce()};
}

function fetchColumns(token) {
    const res = http.get(apiUrl(`${LIST_PATH}/search-columns`), {headers: jsonHeaders(token)});
    checkBusinessCode(res);
    return res;
}

function fetchFields(token) {
    const res = http.get(apiUrl(`${LIST_PATH}/search-fields`), {headers: jsonHeaders(token)});
    checkBusinessCode(res);
    return res;
}

function fetchWithMeta(token) {
    const res = http.get(
        apiUrl(`${LIST_PATH}?page=${LIST_PAGE}&size=${LIST_SIZE}&with_meta=1`),
        {headers: jsonHeaders(token)},
    );
    checkBusinessCode(res);
    return res;
}

export default function (data) {
    if (VARIANT === 'columns') {
        group('04a search-columns', () => columnsTrend.add(fetchColumns(data.token).timings.duration));
        return;
    }
    if (VARIANT === 'fields') {
        group('04b search-fields', () => fieldsTrend.add(fetchFields(data.token).timings.duration));
        return;
    }
    if (VARIANT === 'with_meta') {
        group('04c list-with-meta', () => withMetaTrend.add(fetchWithMeta(data.token).timings.duration));
        return;
    }
    group('04a search-columns', () => columnsTrend.add(fetchColumns(data.token).timings.duration));
    group('04b search-fields', () => fieldsTrend.add(fetchFields(data.token).timings.duration));
    group('04c list-with-meta', () => withMetaTrend.add(fetchWithMeta(data.token).timings.duration));
}

export function handleSummary(data) {
    return {[summaryPath(`04-metadata${VARIANT === 'all' ? '' : `-${VARIANT}`}`)]: JSON.stringify(makeSummary(data), null, 2)};
}
