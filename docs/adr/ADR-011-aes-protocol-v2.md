# ADR-011：前端凭证加密协议升级 v2（WebCrypto PBKDF2 + AES-GCM 双格式过渡）

- 状态：已接受
- 日期：2026-09-11
- 关联：ADR-010（crypto-es 替换决策，本 ADR 落地其第 2 条「可选增强」）；
  遗留缺口 L6；N3 五期后 N2/N5 窗口

## 背景

前端凭证加密（登录/注册/重置/改密/验证码目标）长期使用 crypto-js 系的
OpenSSL `Salted__` 兼容格式：EVP_BytesToKey(MD5) 派生密钥 + AES-256-CBC，
无认证标签（无完整性校验），且 MD5 KDF 迭代一次、强度弱。该格式是
crypto-js 的历史包袱。前端凭证加密本身是纵深防御层（TLS 为主防线），
但既然要动协议，应一步到位换现代原语。

## 决策

1. **v2 协议**：浏览器 WebCrypto `subtle` 产出——
   - PBKDF2-HMAC-SHA256，100,000 迭代，16 字节随机盐，派生 AES-256-GCM 密钥；
   - 12 字节随机 IV，密文格式 `v2:` + base64(salt[16] | iv[12] | ct | tag[16])。
   GCM 自带认证标签，篡改可检出；WebCrypto 为浏览器原生实现，crypto-es
   降为旧格式兼容依赖。
2. **双格式过渡，不做破坏性切换**：
   - 前端 `AesEncrypted()` 优先 v2；非安全上下文（http 非 localhost，无
     `crypto.subtle`）或 WebCrypto 异常时自动回退旧格式；
   - 服务端 `AESCipherV2.decrypt()` 按 `v2:` 前缀分流（GCM 路径 / 旧
     Salted__ 路径），认证失败或格式非法统一返回空串交业务层处理；
   - 存量客户端 / 旧版本前端零影响。
3. **调用点 async 化**（`AesEncrypted` 变为 `Promise<string>`）：
   - 9 个调用点（登录×2、注册、重置密码、验证码发送、store 登录、个人改密
     ×2、用户管理新增/重置）全部 await 化；
   - RePlusPage `beforeSubmit` 契约放开 `Promise` 返回（handle-dialog await）。
4. **测试**：客户端 `aes.spec.ts` 10 例（v2 往返/篡改/错钥/回退/旧格式兼容）；
   服务端单测 12 例，其中跨端互操作向量由 Node WebCrypto（与浏览器同源实现）
   预生成固化；集成层 `test_user_api` / `test_mfa_api` 以 v2 密文走真实 API
   解密链路（reset-password / 创建用户 / userinfo 改密）。

## 后果

- 正面：密码传输获得认证加密（完整性 + 抗篡改）；KDF 强度从单次 MD5 提升到
  PBKDF2-SHA256 100k；浏览器原生实现，无第三方密码库参与新协议。
- 负面：前端加密首次引入异步语义（9 个调用点 + 1 处框架契约）；PBKDF2 100k
  迭代在低端移动设备上有可感知（<100ms）加密耗时，登录链路一次性成本可接受。
- 中性：`v2:` 前缀协议常量、盐长/IV 长/迭代次数在前后端各自常量化（前端
  `src/utils/aes.ts`，服务端 `common/base/utils.py`），变更需两侧同步并发版；
  旧格式解密路径保留至存量客户端自然消亡后再评估移除。
