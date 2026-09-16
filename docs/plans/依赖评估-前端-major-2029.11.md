# 前端依赖 major 适配评估（2029-11 窗口）

- 日期：2026-09-16（第四年度 2029-11 按需插槽一，T2 触发评估）
- 关联：[年度开发计划-2029.10-2030.09.md](./年度开发计划-2029.10-2030.09.md)（T2）、
  [年度开发计划-2028.10-2029.09.md](./年度开发计划-2028.10-2029.09.md)（2029-05 依赖复核登记）
- 背景：第三年度 D 窗口（2029-05）`pnpm outdated` 复核登记两项 major 待评估——`@iconify/vue 4→5`、
  `cropperjs 1→2`；本窗口完成评估与处置。

## 一、@iconify/vue 4.2.0 → 5.0.1：✅ 已升级（本轮执行）

**用法盘点**（全仓 3 文件，均在 `src/components/ReIcon/` 内部封装）：
- `offlineIcon.ts` / `iconifyIconOffline.ts`：`import { Icon, addIcon } from "@iconify/vue/dist/offline"`
- `iconifyIconOnline.ts`：`import { Icon } from "@iconify/vue"`

**兼容性核对**（v5.0.1 npm 元数据实证）：

| 项 | v4.2.0 | v5.0.1 | 结论 |
|----|--------|--------|------|
| `./dist/offline` 子路径导出 | ✅ | ✅（保留，另含 `./offline` 别名）| **项目用法零破坏** |
| `"./*"` 通配导出 | ✅ | ❌（移除）| 项目未使用 ✓ |
| peerDependencies | vue >=3 | vue >=3.0 | 满足（Vue 3.5+）✓ |
| 运行时依赖 | @iconify/types | @iconify/types ^2.0.0 | 满足 ✓ |

**升级动作**：`pnpm add @iconify/vue@5.0.1`（dependencies 精确锁版；`@iconify/json` 图标数据不变）。

**验证**：vue-tsc 零错误 ✓ / vitest 41 文件 250 用例 ✓ / eslint ✓ / **生产构建成功**（v5 模块解析正常）/
bundle-size 首屏 **519.7 KB**（+0.1 KB，预算 557.5 KB 内）/ E2E smoke **9 passed**（双浏览器，图标全站渲染）✓

## 二、cropperjs 1.6.3 → 2.2.0：⛔ 维持 1.x（评估不通过）

**v2 是彻底重写**（命令式 API → Web Components）：`new Cropper()` 及 v1 全部 options/methods/events 失效，
改为 `<cropper-canvas>` / `<cropper-image>` / `<cropper-selection>` / `<cropper-handle>` 等自定义元素 +
`$` 前缀方法（`$move` / `$toCanvas` 等）+ 元素自定义事件（`action` / `actionend` 等）。

**项目适配面**：`ReCropper` 封装组件（v1 命令式 API）+ 使用点 2 处（`RePictureUpload`、`UserInfoAvatar`）。

**不通过的理由**：

1. **能力倒退**：v2 废弃 `checkOrientation`（EXIF 方向自动处理）——官方建议引入第三方库
   （JavaScript-Load-Image）自行处理，否则**手机照片上传方向错乱**（用户可见缺陷，回归风险高）；
2. **重写成本**：`ReCropper` 需按 Web Components 范式重写（属性/`$` 方法/事件全量改造）+ 交互与
   视觉回归（裁剪为交互密集组件，E2E 覆盖有限），估算 ≥0.5 窗口；
3. **收益不足**：v1.6.x 稳定在用，无安全/兼容性问题驱动（非 CVE 驱动升级）。

**处置**：维持 `cropperjs@^1.6.3`——**重开条件**：① v1 出现安全/兼容性问题，或 ② 明确需要 v2 新能力
（更细粒度选区 / Web Components 集成）。重开时按本评估的适配面执行（含 EXIF 方向方案选型）。

## 三、顺带对账（插槽滚动债）

- 2029-05 复核的 patch/minor 可升清单（vite / vitest / prettier / sass / vue-tippy 等）维持
  例行维护窗口处理（非本轮范围，无安全驱动）；
- 本轮升级 1 项、维持 1 项；评估出口与年度台账同步（2029-11 行 ✅）。
