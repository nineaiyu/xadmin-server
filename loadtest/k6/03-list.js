// T3.1 性能基线 03：列表页接口（GET {LIST_PATH}?page=&size=，默认 /api/system/user）。
// 代表元数据驱动 CRUD 的表格热路径：数据权限过滤 + 分页 + 整页序列化（含在线状态/锁定状态批量查询）。
// 以超级管理员压测：跳过菜单授权差异，聚焦框架本身的列表开销。
import http from 'k6/http';
import { group } from 'k6';
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

export function setup() {
  return { token: loginOnce() };
}

export default function (data) {
  group('03 list-page', () => {
    const res = http.get(
      apiUrl(`${LIST_PATH}?page=${LIST_PAGE}&size=${LIST_SIZE}`),
      { headers: jsonHeaders(data.token) },
    );
    checkBusinessCode(res);
  });
}

export function handleSummary(data) {
  return { [summaryPath('03-list')]: JSON.stringify(makeSummary(data), null, 2) };
}
