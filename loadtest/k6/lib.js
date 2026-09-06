// T3.1 性能基线：k6 公共库。
// 约定：全部参数经环境变量注入（BASE_URL/USERNAME/PASSWORD/VUS/DURATION...），
// 脚本本身不含环境相关常量；每个脚本只定义「测什么、多大负载、结果写哪里」。
// 结果统一写入 results/<脚本名>.json（目录由 run-all.sh 创建，或 RESULT_DIR 指定）。
import http from 'k6/http';
import { check } from 'k6';

export const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8896';
export const USERNAME = __ENV.USERNAME || 'admin';
export const PASSWORD = __ENV.PASSWORD || '';

// 列表页与元数据的目标模块（默认用户管理，可用 LIST_PATH 切换到其他 ViewSet 前缀）
export const LIST_PATH = __ENV.LIST_PATH || '/api/system/user';
export const LIST_PAGE = __ENV.LIST_PAGE || '1';
export const LIST_SIZE = __ENV.LIST_SIZE || '20';

// 导出：xlsx 为前端真实路径；EXPORT_FILTER 可绑定 rows 数量（如 `&username=perf_`），
// 导出不分页，数据规模直接决定耗时，必须固定种子规模才有可比性
export const EXPORT_TYPE = __ENV.EXPORT_TYPE || 'xlsx';
export const EXPORT_FILTER = __ENV.EXPORT_FILTER || '';

// 导入：update 模式（默认）只改 nickname 不增长数据；create 模式每迭代新增 IMPORT_ROWS 行，
// 仅适合短时测定并在跑完后重置种子数据
export const IMPORT_MODE = __ENV.IMPORT_MODE || 'update';
export const IMPORT_ROWS = Number(__ENV.IMPORT_ROWS || 5);

export const RESULT_DIR = __ENV.RESULT_DIR || 'results';

// 统计口径：P50/P95 为主登记指标（docs/ops/performance-baseline.md）
const TREND_STATS = ['avg', 'min', 'med', 'max', 'p(50)', 'p(90)', 'p(95)', 'p(99)'];

// 各脚本通用负载档位：VUS/DURATION 环境变量覆盖
export function baseOptions(defaultVus, defaultDuration) {
  return {
    vus: Number(__ENV.VUS || defaultVus),
    duration: __ENV.DURATION || defaultDuration,
    summaryTrendStats: TREND_STATS,
    thresholds: {
      // 宽松护栏：只用于标记异常波动（如被限流/500），不是 SLO；SLO 待基线回填后另行评审
      http_req_failed: ['rate<0.01'],
      http_req_duration: ['p(95)<2000'],
    },
  };
}

export function apiUrl(path) {
  return `${BASE_URL}${path}`;
}

export function jsonHeaders(token) {
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
    'User-Agent': 'k6-baseline/1.0',
  };
}

// setup() 中调用：压测开始前获取一次 access token（1h 有效，远长于单轮压测时长）
export function loginOnce() {
  const res = http.post(
    apiUrl('/api/system/login/basic'),
    JSON.stringify({ username: USERNAME, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } },
  );
  const ok = check(res, {
    'login HTTP 200': (r) => r.status === 200,
    'login code 1000': (r) => r.json('code') === 1000,
    'login issued access': (r) => !!r.json('data.access'),
  });
  if (!ok) {
    throw new Error(`setup 登录失败：HTTP ${res.status} ${res.body}（检查压测环境 config.yml 是否关闭验证码/加密/临时 token）`);
  }
  return res.json('data.access');
}

export function checkBusinessCode(res, expected = 1000) {
  return check(res, {
    [`business code ${expected}`]: (r) => {
      try {
        return r.json('code') === expected;
      } catch (e) {
        return false;
      }
    },
  });
}

// handleSummary 通用提取：按 group 拆分 http_req_duration / http_req_failed，
// 输出可回填 metrics.md 的紧凑 JSON
function pct(values) {
  return {
    avg: values.avg,
    p50: values['p(50)'],
    p90: values['p(90)'],
    p95: values['p(95)'],
    p99: values['p(99)'],
    max: values.max,
  };
}

function groupOf(metricName) {
  const m = metricName.match(/group:::([^,}]+)/);
  return m ? m[1] : '_all';
}

export function makeSummary(data) {
  const durations = {};
  const failedRates = {};
  for (const [name, metric] of Object.entries(data.metrics)) {
    if (name === 'http_req_duration' || name.startsWith('http_req_duration{')) {
      durations[groupOf(name)] = pct(metric.values);
    } else if (name === 'http_req_failed' || name.startsWith('http_req_failed{')) {
      failedRates[groupOf(name)] = metric.values.rate;
    }
  }
  return {
    recorded_at: new Date().toISOString(),
    base_url: BASE_URL,
    iterations: data.metrics.iterations ? data.metrics.iterations.values.count : 0,
    http_req_duration: durations,
    http_req_failed_rate: failedRates,
  };
}

export function summaryPath(name) {
  return `${RESULT_DIR}/${name}.json`;
}
