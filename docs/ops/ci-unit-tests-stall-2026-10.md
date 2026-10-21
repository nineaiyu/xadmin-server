# CI「Unit Tests 跑到 92% 停摆后超时」排查记录（2026-10）

> 用途：把**已掌握的证据、已排除的方向、已做的处置与待判定的疑点**集中成一份可交接材料。
> **结论状态（2026-10-20 收口）**：机制已定谳并完成根治（§12.2 四项交付，见 §13）——
> 尾部种子测试逐用例全量装载，`loaddata` 对 m2m 逐行 `set()`（JOIN + DELETE + INSERT），
> 慢 runner 上被放大成分钟级；`--dist worksteal` + 种子 m2m 批量化 + 种子测试模块级装载
> 已落地，**剩余动作只有 CI 观察 2~3 次（需推送）**。
> 取证链路（看门狗 150s + 570s 到期转储 + 10min 上限）已在 run 39371081711/39374822846
> 验证；快照新增 `pg_stat_user_tables`（§12.3 P3）。
> 下文 §10 的「用例之外」结论已被 §11 推翻（当时复查转储用的格式串写错），保留原文留痕。

## 1. 现象

GitHub Actions 的 `Unit Tests` job（`xadmin-server/.github/workflows/test.yml`）间歇性地在进度点
**91~92% 处停止输出**，静默 17~33 分钟后被 job 超时杀掉（运行结论显示 `cancelled`）。
部分运行会在静默后「一次性爆发 3~5 行」，即约 360 个用例在 9 秒内完成，然后再次静默。

有代表性的三次运行（时间为日志行自身时间戳）：

| 运行 | 进度轨迹 |
|------|----------|
| 39350289726 | 92% @00:59:51 → 静默 30 分钟后 94% @01:29:37 → 96% @01:29:42 → 静默至 01:33 被杀（40m20s） |
| 39356342590 | 92% @02:04:55 → 静默 17 分钟后 93% @02:21:44 → 97% @02:21:53 → 静默至 02:34 被杀（36m35s） |
| 39357296222 | 92% @02:15:22 → **静默 33 分钟** → 02:48:50 被杀（40m24s） |

对照组：同一套用例多次正常完成（4m46s / 4m50s / 5m19s）；一次 `pull_request` 运行 **18m49s 但成功**
（只是慢，未死）。

发生频率：2026-10-17 起约六成 push 运行被取消；**纯文档提交（`docs(plans)`、`docs(notice)`）同样命中**，
说明与提交内容无关。

## 2. 运行环境与命令

| 项 | 值 |
|----|----|
| job | `test`，`ubuntu-latest`（4 vCPU），`timeout-minutes` 由 40 调整为 20 |
| services | `pgvector/pgvector:pg17`（宿主 55433 / 库 `xadmin_realtest` / user `server`）、`redis:8.10.2`（56379） |
| 设置模块 | `tests.settings_real`（真 PG + 真 Redis，xdist worker 各自独立库/逻辑库隔离） |
| 命令 | `pytest -n auto --cov --cov-report=term-missing --cov-fail-under=85 --junitxml=…` |
| pytest.ini | `addopts = --tb=short -q --maxprocesses=8`；**`faulthandler_timeout = 600`** |
| 规模 | 收集 6398 例；本机全量 6405 passed / 2 skipped（另 1 例并发 flaky），**约 130 秒** |

相关实现：

- `tests/conftest.py`：master 进程持有 **PG 会话级 advisory lock** 串行化整轮测试，避免两轮 pytest
  互踩测试库；`XADMIN_ALLOW_CONCURRENT_PYTEST=1` 可绕过（绕过会立刻复现「大面积 ERROR + 锁等待」，
  这是本机排障时的重要陷阱，见 §5）；
- `tests/pytest_lock.py`：上述锁的获取/释放与 fail-open 语义。

## 3. 已确认的证据

1. **卡点不在用例内**：`faulthandler_timeout=600` 从未触发（该看门狗按测试阶段计时），33 分钟静默
   期间没有任何线程栈转储；
2. **主进程在等 worker**：等价场景抓到的 master 栈为
   `xdist/dsession.py:154 loop_once → queue.get()`，即等待 worker 上报事件，而非自身阻塞在 I/O；
3. **数据库侧全程空闲**：静默期间 PG 容器照常每约 5 分钟 checkpoint，但业务写入为零
   （`pg_stat_activity` 无 `test_*` 会话）；
4. **磁盘疑似极慢**：该窗口内的 checkpoint 仅约 1200 缓冲（约 9 MB）却记录 `write=120~150s`；
   **（2026-10-20 已证伪，勿再沿此方向排查：`write=` 是 `checkpoint_completion_target`
   刻意摊匀的产物，真实磁盘信号 `sync=` 仅 0.02s；冻结窗口 WAL 生成塌陷 ~30 倍说明是
   「停」不是「慢」，见 §10.1.4/§10.1.5）**
5. **PG 日志中的 `FATAL: role "root" does not exist` 是噪音**：容器 healthcheck `pg_isready` 以 root
   身份每 10 秒探测一次，40 分钟正好约 238 条，与问题无关；
6. **本机同命令稳定通过**：`-n auto` 与 `-n 4` 各多轮，2m05~2m21 完成，无法本地复现。

## 4. 已排除的方向

| 方向 | 排除依据 |
|------|----------|
| 断言/覆盖率真失败 | 失败运行在卡死前无任何 `F`，且卡死与提交内容无关（文档提交同样命中） |
| 单个用例挂死 | 按用例计时的 faulthandler 从未触发；PG 侧零会话 |
| 并发 pytest 互踩测试库 | CI 每个 job 独占 runner 与 services，不存在两轮并发；本机该场景已由 advisory lock 拦住 |
| 权限/种子/契约类业务缺陷 | 同类改动不同运行有成功案例；成功运行 4m46s 与超时运行内容几乎相同 |
| Lint 相关 | 另一 job；其中 5 个 mypy 错误已单独修复并转绿（与本次卡死无关） |

## 5. 本机排障陷阱（务必先看）

- **不要并发跑两轮 pytest**：即使 `XADMIN_ALLOW_CONCURRENT_PYTEST=1` 放开锁，两轮会互相 DROP/CREATE
  同名测试库，表现为「大量用例 ERROR + `loaddata` 卡在等锁」，极易被误判成线上问题的本地复现；
- **野循环脚本要清理**：排障用的后台循环（多轮 pytest）若未随会话结束退出，会持续制造上述并发；
  排查前先 `ps -eo pid,command | grep pytest`；
- **py-spy 在 macOS 需要 root**：本机无法无侵入 attach，故线程栈只能靠 `SIGABRT` + faulthandler。

## 6. 已做的处置（当前流程行为）

`.github/workflows/test.yml` 的测试步骤已改为「可定位 + 可自愈」：

1. pytest 输出重定向到 `test-reports/pytest.log`（不再经 stdout 管道），每 30 秒打印一行进度
   （进度行数 / 静默秒数 / 日志尾部 160 字节）；
2. **假死看门狗**：连续 240 秒日志无增长即判定假死 → 对 pytest 主进程与其全部 worker 发 `SIGABRT`，
   由 faulthandler 打印**全部线程栈**写入日志 → 结束本次尝试；
3. 单次尝试 **8 分钟**硬上限（正常 2~3 分钟）；假死/超时（退出码 124 或 `.stalled` 标记）自动重试一次，
   真失败（断言/覆盖率）原样退出不重试；job 上限 20 分钟；
4. 完整日志 + junit 同时作为 artifact 上传，下一次红灯可直接读取卡点栈。

> 注意：这是**取证与自愈**，不是根因修复。下一次复现时 `test-reports/pytest.log` 里的
> `Fatal Python error: Aborted` 段即为 worker 的真实阻塞点。
>
> **2026-10-20 复核：上述看门狗原参数（240s 阈值 + 8min 单次上限）在实测卡死形态下
> 永远无法触发**——断流点在 ~5.5-6min，静默只累计到 120~150s 就被 8min 上限先杀掉，
> 两次 attempt 均如此（run 39363451747）。已修：阈值 240→150s、首个进度行后才武装、
> 上限 8→10min、job 24min、转储前落 ps 快照、转储后先 TERM pytest 主进程再重试
> （避免孤儿持锁）。见 §10.3。

## 7. 待判定疑点（交接给后续排查）

1. master 停在 `dsession.queue.get()`、worker 不再上报、又不在用例范围内 —— 卡点是 worker 在
   **用例之间的 hook/夹具**里阻塞，还是 xdist/execnet 通道被卡住（worker 半死但未断开）？
2. 是否与 `--cov`（coverage 的 sqlite 数据文件、`.coverage.gwN` 合并）或 `--junitxml` 写入有关？
3. 磁盘 I/O 退化假说是否成立（checkpoint 写 9 MB 耗时 120~150 秒）？若是，CI 侧可选的缓解手段
   （PG `synchronous_commit=off` / `fsync=off`、coverage 数据目录放 tmpfs、`--reuse-db`、
   `-p no:cacheprovider`）哪个最有效？
4. 「360 例在 9 秒内完成」更像输出缓冲一起 flush，还是一批 worker 同时解封？这条决定后续排查方向。
5. 依赖栈（pytest 9 / pytest-xdist 3 / coverage 7 / Python 3.14）是否有已知的「worker 关闭或
   session 收尾卡死」问题？
6. 是否改用 `py-spy dump`（Linux runner 同用户可 attach）作为看门狗手段，以拿到比 SIGABRT 更准的
   阻塞点？
7. 结构性兜底：把全量拆成 2~3 个分片步骤，或改 `--dist loadscope` / `--loadfile`，能否把「一处卡死
   拖垮整轮」变成局部失败？

## 8. 复现与取证入口

```bash
# 1) 本地测试栈（真 PG 55433 / 真 Redis 56379）
docker compose -f xadmin-server/compose.test.yml up -d

# 2) 与 CI 同口径执行
cd xadmin-server
.venv/bin/python -m pytest -n 4 --cov --cov-report=term-missing \
  --junitxml=/tmp/junit.json

# 3) CI 侧取证（失败运行）
gh run list --workflow "Unit Tests"
gh run view <run-id> --log-failed
gh run download <run-id> -n pytest-junit      # 内含 pytest.log（含卡点线程栈）
```

关键文件：`.github/workflows/test.yml`、`pytest.ini`、`tests/conftest.py`、`tests/pytest_lock.py`、
`tests/settings_real.py`。

## 9. 并行结论（与本次卡死无直接因果，但同源并发隐患）

本机出现一次 flaky：`tests/unit/common/test_health_probes.py::TestProbeAll::test_storage_probe_concurrent_calls_all_succeed`
（8 路并发 `storage_probe()` 偶发失败），涉及「并发 + 共享存储目录」，与卡死可能共享相似诱因，建议一并关注。

## 10. 2026-10-20 下午续查：证据补强与机制收敛

> 方法：拉取 10-16 以来全部 40 余次运行的日志/工件（`gh run view --log` / `gh run download -n pytest-junit`，
> 对 run 39363451747 取得两次 attempt 的 junit 与 pytest.log；以 10-19 绿运行 39262182914 的 junit 为基线做
> 完成集差集），并读本仓 `.venv` 内实际安装的 xdist 3.8.0 / pytest 9.1.1 / execnet 源码核对机制。

### 10.1 新增硬证据

1. **卡点是「顺序结构性」的，不是随机基础设施冻结**。run 39363451747 的两次独立 attempt
   （全新进程、全新容器会话）都断流在 91-92%，被 SIGINT 打断时 junit 记录的**未完成用例集合几乎完全
   相同**（相对绿基线差集 414 vs 413 条，全部位于 `tests/unit/system/` 尾部：`test_task_execution`(43)、
   `test_pat`(15)、`test_seed_*`、`test_export_record`、`test_import_*`、`test_ldap_*`、`test_tag_center`
   等；两次 attempt 完成数 5919/5918 passed + 46 skipped 仅差 1）。两次到达断流点的墙钟时间不同
   （attempt 1 约 6.0min、attempt 2 约 5.5min），断流位置却一致 → **卡点由测试序列位置决定**。
   （缺口实测 433/434 条 = 差集 414 + 当日新增用例约 20 条无基线可比。）
2. **同一提交、相隔数秒的孪生运行一绿一卡**：10-19 00:06 的 39179235398（40min 取消）vs
   39179241649（4m50s 绿）；10-16 12:03 同样一对（38897896244 取消 vs 38897912623 5m24s 绿）。
   → 与提交内容无关、与 runner 实例相关；同代码不同结果 = 非确定性竞态被环境时序触发。
3. **起始日修正为 2026-10-16 ~11:00 UTC**（首个 40min 超时型取消运行 38891269659；此前全历史
   零超时型取消——10-16 05:03 的 9 次取消均为 Renovate 分支事件，3~6min 即终，与卡死无关；
   10-16 11:10 的 38892024195 也已显化变慢：14m29s 才绿）。该窗口仓内只有 5.0.1 版本号/文档/镜像 CI
   变更；**测试链依赖（pytest 9.1.1 / xdist 3.8.0 / coverage 7.16.1）9-23 就进了 uv.lock（8089ce47）
   且其后 CI 连续绿** → 排除依赖升级直接致因，指向 runner 池侧变化（镜像构建串绿卡一致：agent
   2.337.0 / Ubuntu 24.04.5，可见层无差异；宿主层不可见）把一个潜伏竞态的触发概率抬了起来。
4. **磁盘健康（证伪 §3.4）**：新运行窗口内 PG checkpoint `sync=0.020s`（1648 个文件）；
   `write=88s` 系 `checkpoint_completion_target` 按设计摊匀，不是慢盘信号。慢盘/IO 退化方向终结。
5. **冻结窗口业务写入塌陷 ~30 倍**：运行期 WAL 约 168MB/5min（≈34MB/min），冻结期掉到
   ~2.2MB/2.2min（≈1MB/min，且零 `test_*` 会话）→ worker 是**真的停了**，不是在慢速干活。
6. **worker 在静默期不在任何用例内（铁证）**：**〔2026-10-20 夜间修正：本段结论错误，
   已被 §11 推翻——复查用 grep 模式写错（faulthandler 实际输出为 `Timeout (0:10:00)!`，
   不含 "detected"），旧运行确有 600s 转储，worker 就在用例内。保留原文以留痕。〕**
   `faulthandler_timeout=600` 自 10-18 提交
   （18ac9548）起对全部 10-19/10-20 取消运行生效（已按各 run 的 headSha 核对 pytest.ini），
   对这些运行全量复查转储特征（`Timeout … detected` / `Current thread 0x` / `Fatal Python error`）
   **零命中**。17-33 分钟零完成 + 零转储，在「用例/夹具内阻塞」下不可能成立（任一 worker 在单个
   用例协议内 >600s 必打栈）→ **卡点在 `pytest_runtest_protocol` 之外**，即 xdist worker 主循环的
   用例间隙。今天两次 attempt 的静默只有 120-150s（<600s），不构成反例，机制与旧运行同源。
7. **master 在被打断时刻空闲在 `queue.get(timeout=2.0)`**：这次不是等价场景，是 CI 现场直证——
   SIGINT 的 KeyboardInterrupt traceback 落在 `xdist/dsession.py:154 loop_once`（pytest.log 可查）。
   master 没有积压事件要处理。SIGINT 后约 3 秒打出干净摘要并写出 junit → 双向通道与全部进程
   存活、可中断、非 D 状态。
8. **看门狗 shell 循环在冻结期间按 30.01s 精确节拍运行** → VM/CPU 没有冻结、没有抢占。
9. **坏 runner 上测试整体 ~2.2x 慢**（绿 junit 用例累计 215s vs 卡死 attempt 481s；个案：
   `test_dump_split_apps::test_only_selected_app_layers_are_written` 绿 7.23s / attempt2 66.97s），
   10-16 11:10（14m29s）与 10-18 08:48（39min）两次「慢但跑完」的绿运行说明冻结**会自行释放**
   （对应旧日志的「一次性爆发」），释放后能跑完——是活锁/背压，不是死锁。
   注意 attempt 内自动重试与 attempt 1 高度相关（同一 runner），39363451747 两次都卡；
   **当前最有效的止血是重跑失败 job（换 runner）**。

### 10.2 机制收敛（两个候选帧，都在 master 侧事件管线上）

> **〔已被 §11 推翻：现场栈证明卡点在 worker 的 fixture 内 PG 查询等待，候选 A/B 均排除。
> 本节保留以留痕。〕**

读本仓 `.venv` 的 xdist 3.8.0 源码确认：worker 用例间隙只有两处可无限期阻塞，且都不在
faulthandler 计时范围内（`xdist/remote.py::run_one_test`）：

- **候选 A（饿死）**：`TestQueue.get()` 空队列 Event 等待。worker 是「先预取下一个索引、再执行
  当前用例」的模型；要饿死必须 master 的 `node2pending` 账目比 worker 实际队列多——即
  `runtest_protocol_complete` 事件丢失/未处理，master 以为 worker 手里还有活而停止派发
  （`check_schedule` 只在收到完成事件时才补发，无事件就永远不再检查）。
- **候选 B（发送背压）**：协议结束后 `sendevent("runtest_protocol_complete")`——execnet
  `Channel.send` 文档明示「**sender queue 满时可能阻塞**」。需要 master 侧对应 gateway 的
  receiver 线程停摆（4 条同时停摆的公共诱因未识别；GIL 被某长持不释的 C 调用占住是一种可能，
  但未找到候选调用）。

master 侧自身被排除：现场 traceback 证明它空闲在 `queue.get`（不是卡在事件处理中——若是，
SIGINT 前应先出现积压消化导致的 dots 爆发，实际是连续静默到被杀）。**两候选的裁决只能靠
下一次复现时的全线程栈**（worker 栈落在 `Event.wait` → A；落在 `Channel.send/gateway._send` → B）。

### 10.3 看门狗缺陷修复（2026-10-20，随本批 workflow 改动）

实测卡死形态 = 断流点 ~5.5-6min、静默只累计到 120~150s 就被 8min 上限先杀 → **240s 假死
阈值永远等不到，SIGABRT 转储从未有机会触发**（今天两次 attempt 均如此，artifact 里没有任何栈）。
另有两个连带缺陷一并修：转储后只 TERM `timeout` 包装会**孤儿化 pytest**（PG advisory lock 在
`pytest_unconfigure` 才释放，attempt 2 会撞「另一个 pytest 会话正在使用同一测试栈」快速失败）；
`pgrep -f | head -1` 在 uv 包装层存在时会取到 uv 进程而非 pytest 主进程（ABRT 打不到 worker）。
修复后参数：**假死阈值 150s + 首个进度行出现后才武装**（防 worker 建库期误判；测试期合法静默
最长 ~45s，阈值有 3 倍余量）、**单次尝试 10min**（> 断流点 + 150s 的转储窗口）、job 24min、
转储前先落 `ps STAT/wchan` 快照（区分内核态阻塞）、**先 ABRT 全部 worker 再 ABRT master**
（worker 命令行不含 pytest 字样，须按主进程直接子进程枚举）、TERM 主进程并等退出再重试。

### 10.4 对 §7 待判定疑点的回答

| §7 条目 | 结论 |
|---------|------|
| 1（用例间 hook/夹具 vs 通道） | **是用例间隙的 xdist 事件/调度机制**，不在夹具（夹具在 protocol 内，faulthandler 覆盖已被零转储排除） |
| 2（--cov / junitxml） | 无因果：冻结期 master 无积压、两看写盘都在会话收尾；`--cov` 与 junit 参数保留不动 |
| 3（磁盘假说与 PG 缓解手段） | **已证伪**（10.1.4/10.1.5），`synchronous_commit=off` 等缓解手段不需要做 |
| 4（爆发=缓冲 flush 还是解封） | **阻塞释放后积压事件的集中处理/补发**：输出改文件重定向后依然断流，排除 stdout 缓冲；「慢但跑完」的绿运行佐证阻塞会自行释放 |
| 5（依赖栈已知问题） | pytest 9.1.1 / xdist 3.8.0 / coverage 7.16.1 / Python 3.14.8；版本 9-23 起未变，检索无直接命中——待拿到卡点帧后按精确机制向上游检索/提报 |
| 6（py-spy） | 保留为增强项：SIGABRT+faulthandler 不依赖 GIL、可打全线程栈，先跑一轮看效果；若转储仍不触发再换 py-spy dump |
| 7（分片/改 dist） | 有效兜底，但**拿到卡点栈前不要动调度形态**——loadfile/loadscope/分片都会改变复现条件，先取证后优化 |

### 10.5 下一步（按序）

1. 推送本批 workflow 修复后，等下一次卡死复现（历史频率约 60%/push）：下载 `pytest-junit`
   artifact，读 `pytest.log` 的 `Fatal Python error: Aborted` 段（4 个 worker + master 各一份
   全线程栈）与进程快照——**worker 栈在 `Event wait`（`TestQueue.get`）还是 `Channel.send`，
   一次定谳**；快照里 `STAT=D` 则整个方向重评（内核态阻塞）。
2. 机制确认后：向上游（pytest-xdist / execnet）检索或提报「load 调度尾段 worker 静默饿死 /
   send 背压」类问题，附栈与复现条件（4 vCPU + 6400 用例 + 尾部重用例区）。
3. 若两周内转储仍未触发（阈值仍偏高或形态变化），改用 py-spy dump（Linux 同用户可 attach，
   不依赖进程自身信号处理）。
4. 业务侧不受影响：这是 CI 门禁链路问题，正式库/容器栈无关联改动。

## 11. 定谳（2026-10-20 run 39371081711：看门狗首次成功转储）

### 11.1 事件

推送 `7c48ad95`（看门狗参数修复）后 run 39371081711：两次 attempt 均在约 6min、
92-94% 处断流；**看门狗在 150s 静默处正常触发**（04:53:53 / 05:03:31），对全部
worker 与 master 发 SIGABRT 后 exit 134；job 于 05:03:43 失败（20min，符合预算）。
artifact 的 `pytest.log` 内含 `Fatal Python error: Aborted` 全线程栈。

### 11.2 卡点栈（决定性证据）

worker gw0 的主线程（`Current thread`）：

```
psycopg/connection.py:484 wait
psycopg/cursor.py:113 execute
django/db/backends/utils.py:105 _execute        （... results_iter / Query.__iter__）
django/db/models/fields/related_descriptors.py:1309 set
identity/builtin.py:185 _ensure_role_fields
identity/builtin.py:133 sync_builtin_roles
system/management/commands/load_init_json.py:228 _sync_builtin_roles_after_seed
system/management/commands/load_init_json.py:113 handle
tests/unit/system/test_dump_split_apps.py:30 seeded        ← 测试 fixture
_pytest/fixtures.py:1005 call_fixture_func
（...→ xdist/remote.py:227 run_one_test）
```

C 栈同时给出 `libc poll+0x4d`——**客户端在 socket 上等 PostgreSQL 响应**。
master 栈：`queue.get(timeout=2.0)`（空转等事件）。

结论：**卡点 = 尾部种子测试 fixture 内的 PG 查询等待**。不是 xdist 调度饿死、
不是 send 背压、不是磁盘/VM/CPU 冻结。

### 11.3 对 §10 的修正（留痕）

1. **「faulthandler 从未触发」不成立**：`dump_traceback_later` 的真实输出是
   `Timeout (0:10:00)!`（**无 "detected" 字样**），§10 复查用的模式串写错。
   用正确模式重扫旧运行：`39179235398` 在 2026-10-19T00:21:54 即有 600s 转储
   （另有 3 个旧运行同样命中）。旧运行的静默期 worker 就在用例内，与今天同源。
2. §10.2 的候选 A（worker 空队列饿死）/ B（协议后 send 背压）均被现场栈排除。

### 11.4 完整机制

1. 尾部区（约 92-94%，`tests/unit/system/` 的种子测试组）**每个测试的 fixture 都跑
   完整 `load_init_json`**：`sync_builtin_roles` → `_ensure_role_fields` 对角色授权的
   **每个菜单**执行「查/建 FieldPermission → `permission.field.set(all_fields)`」
   （每菜单一次 M2M DELETE+INSERT），收尾还有 `fill_field_permissions()` 全量补缺。
2. 4 个 worker 并发进入同一区域 → 4 vCPU runner 上的 PG service 容器被 permission-sync
   写风暴饱和；单条查询从 ms 级劣化到几十秒~分钟级（客户端观测即 psycopg `poll` 等待），
   单个 fixture 可超 10 分钟。
3. 期间无任何 pytest 事件 → master 空转、进度冻结。旧 40min 上限下靠「偶尔跑完」收尾
   （14m29s / 39min 的绿运行与「爆发式进度」），8/10min 上限下必然失败。
4. 每次 attempt 约 6min 处的「4 条连接被 `administrator command` 终止 + 立即
   checkpoint（DROP DATABASE 特征）」= **某个跑得快的 worker 已清空自己的队列、正常
   session 收尾**：conftest 的 `_terminate_other_sessions` 终止自己池上的连接，
   pytest-django 随后销毁自己的测试库——是「其它 worker 还在磨」的症状而非原因；
   attempt 2 的 `test_xadmin_realtest_gw0 already exists` = attempt 1 的 gw0 被 ABRT
   未收尾的遗留库（pytest-django 建库重试会 DROP 重建，无害）。
5. runner 间差异 = **PG 吞吐的宿主级差异**（同规格实例的 CPU steal / 数据文件读延迟等）：
   同一提交双实例一绿一卡；绿 4m46s、慢跑 14m/39m；本次 attempt 1 前 92% 只用 6min
   （约 1.3x），进入尾巴后崩塌到单测试 10min+。

### 11.5 处置建议（按性价比）

1. **根治：砍掉种子测试的重复全量装载**——65+ 个测试每个都 `call_command("load_init_json")`；
   改为 session 级 seed（每 worker 一次）+ 测试内事务回滚，或至少用 `--apps` 限定装载面。
2. **批量写权限同步**：`_ensure_role_fields` / `fill_field_permissions` 的 per-menu
   `.set()` 收敛为「收集缺口 → bulk_create / 按角色一次 set」，削掉峰值。
3. CI 兜底（本批已具备）：看门狗 150s + 10min 上限（已验证可转储）；新增步骤在测试前
   执行 `ALTER SYSTEM SET log_min_duration_statement='10s'` 与 `log_lock_waits=on`
   并 `pg_reload_conf()`（service 的 `options` 只接受 docker create 参数，不能直传
   postgres 启动参数）——下次复现时容器日志（失败时由 Stop containers 步骤倾出）
   直接给出服务端慢查询/锁等待视角。
4. 短期止血：失败 job 重跑换 runner（同 runner 内两次 attempt 高度相关，attempt 内重试
   帮助有限）。

### 11.6 第三次运行（39374822846）的新证据：服务端不是瓶颈

在 `474335eb` 上运行 39374822846（首次带 PG 慢查询取证）：

1. **attempt 1 是「慢磨」而非冻结**：`5958 passed, 46 skipped in 600.95s`，进度到 93% 后
   被 10 分钟上限 SIGINT——全程无 150s 静默，看门狗未触发（旧脚本没有到期转储）。
2. **attempt 2 在 91% 出现新症状**：日志尾部反复出现
   `reentrant lock watchdog exit: ('xadmin:lock:test:lock:<uuid>', LockNotOwnedError)`
   ——阻塞期间 Redis 锁 TTL 过期、看门狗线程退出（是「慢」的次生症状，不是原因）；
   之后仍每 30~60s 零星出一个点，最后 150s 静默时看门狗与 10min 上限**同时刻**触发，
   timeout 的 SIGINT 先把 worker 优雅带走，只留下 master 栈（`pytest_sessionfinish`）。
3. **PG 侧决定性反转**：本运行 `ALTER SYSTEM SET log_min_duration_statement='10s'` 与
   `log_lock_waits=on` 已生效（步骤输出 `ALTER SYSTEM`×2 + `pg_reload_conf = t`），
   但整个 20 分钟里 **零条 `duration:` 慢语句、零条 `still waiting for` 锁等待**；
   只有 3 次 worker 正常收尾的 `terminating connection` + 立即 checkpoint（§11.4.4 的解释成立）。
   → **「PG 被写风暴压饱和到单语句几十秒」不成立**：服务端从未执行 ≥10s 的语句。
4. **修正后的机制判断**：worker 栈里的 `psycopg wait → poll()` 只说明「采样时在等
   socket」，不代表单次等待很长；服务端既没长语句也没锁等待、PG 为空闲态，
   最符合证据的是 **runner 的 CPU 吞吐塌陷**（宿主 steal/配额），使尾部区域成千上万次
   小往返被拉长成分钟级——与「同一提交双实例一绿一卡」「绿 4m46s vs 慢跑 14m/39m」
   的宿主级差异一致。
5. **本批新增取证**（下次复现即可定案）：
   - 看门狗触发条件增加「已运行 ≥570s」的到期转储（解决本案中「上限与看门狗同时刻、
     worker 被 SIGINT 先带走」的竞态）；
   - 快照增加 `/proc/loadavg`、`top`（含 `st` steal）、`vmstat`、以及
     `pg_stat_activity`（state / wait_event / query_start / query 摘要）——
     可直接区分「服端执行/锁等待」与「backend 空闲（ClientRead）→ 客户端侧」；
   - 修复看门狗里 `grep -c` 无匹配时输出两行导致的 `[: 0 0: integer expression expected`。

## 12. 根治方案、排期与交接（2026-10-20，交给下一棒）

### 12.1 一句话现状

- **卡死已定性**：worker 停在种子测试 fixture 内的 PG 查询等待（`test_dump_split_apps::seeded`
  → `call_command(load_init_json)` → `_load_seed` 的 `loaddata` M2M `.set()` → `psycopg wait`，
  见 §11.2）；master 空转在 `queue.get`。
- **失败结构** = 尾部重查询 × 尾部集中到最后一个 worker × 10 分钟上限。run 39376962747 的
  快照显示转储时**只剩 1 个 worker**，其余 3 个已跑完退出；CPU 72~76% 空闲、steal=0、
  内存充足；PG 里该 worker 正在跑的 M2M JOIN 查询 **active 1.18~1.43s**、无锁等待。
- **本机复现不了「1.2s/条」**：探针库 `EXPLAIN (ANALYZE)` = 0.19~0.26ms（Bitmap Index Scan +
  Hash Join，索引正常）；CI 侧的 1.2s 属环境/规模放大，待补测（§12.7）。
- 当前 CI 仍红（39376962747 两次 attempt 均在静默 150s 处转储后失败，15 分钟）。

### 12.2 已拍板的根治路线

| 批次 | 内容 | 状态 |
|------|------|------|
| P0 | pytest 加 `--dist worksteal`：空闲 worker 偷活，尾部不再堆到最后一个 worker | **已落地**（工作区，未提交）；本地 `-n 4` exit 0 |
| P1 | **种子装载 M2M 写入批量化**（主项）：fixtures 的 M2M 列表从 `loaddata` 抽出，装载后按表 `bulk_create(ignore_conflicts=True)` | **已交付**（§13.2），语句数 980 → 26 |
| P2 | **种子测试装载瘦身（取 B1）**：`seeded` 改为模块级装载一次 + 每测事务回滚；仅「重复装载幂等」用例保留 function 级 | **试点已交付**（§13.3，`test_dump_split_apps` 8 次 → 1 次）；其余模块按需 |
| P3 | 护栏与观测：SQL 条数守护断言、快照补 `pg_stat_user_tables`、保留看门狗 | **已交付**（§13.4） |
| P4 | 触发制：种子装载收尾 `ANALYZE`（仅当 P1 后仍有慢查询） | 备选（未触发，保留） |

### 12.3 任务清单（文件级定位与验收）

**P0（1 行）**

- 文件：`.github/workflows/test.yml` 的 pytest 命令，`-n auto` 后加 `--dist worksteal`（已改）。
- 验收：本地 `-n 4 --dist worksteal` exit 0（已做）；CI 观察 2~3 次。

**P1（主项）**

- 代码点：
  - `system/management/commands/load_init_json.py::_load_seed`（约 138 行，`super().handle(...)` 即 loaddata）；
  - `system/services/seeding/`（装配层，`prepare/plan/ownership/grants`）；
  - `identity/builtin.py::_ensure_role_fields`、`_fill_builtin_role_fields`；
  - `system/services/permission_sync/`（`fill_field_permissions`）。
- 做法：装配阶段把 fixtures 里的 M2M 列表剥离（与现有「剥离角色菜单授权字段」同思路，见
  `[种子装配] 剥离 N 处角色菜单授权字段` 日志），装载后用 through 表
  `bulk_create(..., ignore_conflicts=True)` 一次写入；保留幂等语义（与库内已有关系做差异同步）。
- 验收：`test_seed_*` / `test_builtin_seed` / `test_dump_split_apps` 全绿；新增「批量写入与逐行写入
  等价」「二次装载幂等」守护测试；本地串行尾组耗时对比（当前 ~50s）。
- 基线量化（先做）：见 §12.5 脚本（统计 `load_init_json` 的 SQL 条数、M2M 相关条数与耗时）。

**P2（B1）**

- 文件：`tests/unit/system/test_dump_split_apps.py`（`seeded` fixture）、`test_seed_*.py`、
  `test_builtin_seed.py`、`test_modelfield.py`、`test_sync_field_permissions.py`。
- 做法：session 级 fixture 装载一次；需要「重复装载」语义的用例显式再跑；其余靠事务回滚隔离。
- 验收：本地 `-n 0` 与 `-n 4` 双跑全绿；抽样打乱顺序验证无顺序依赖；耗时对比。

**P3**

- 快照补：`select relname, n_live_tup from pg_stat_user_tables where relname in
  ('system_fieldpermission_field','system_modellabelfield','system_fieldpermission','system_menu');`
- 守护：给 `load_init_json` 加 `CaptureQueriesContext` 条数上限断言（防止批量化成果回退）。

### 12.4 排期（按「本地可验证」粒度，尽量不烧 CI）

| 时间 | 产出 | 验证 | 状态 |
|------|------|------|------|
| D1 | P1 基线数字（SQL 条数/M2M 占比）+ 批量化实现点确认 | 探针库脚本（30s） | 完成（§13.1） |
| D2 | P1 实现 + 等价性守护测试 | 本地 `-n 0` 尾组耗时对比 | 完成（§13.2） |
| D3 | P2-B1 试点 1~2 个文件并确认语义 | 本地 `-n 0` / `-n 4` 双跑 | 完成（§13.3） |
| D4 | P2 推广 + P3 守护断言 | 本地全量 pytest + 相关 E2E 子集 | P3 完成；P2 其余模块按需 |
| D5 | P0 推送 + 观察 CI 2~3 次；本文档定稿 | CI 与本地双向核对 | 待推送后观察 |

### 12.5 本地迭代手册（不占 CI）

```bash
# ① 模拟「只剩一个 worker」——每次改动后的主回归（当前基线 ~50s）
.venv/bin/python -m pytest -n 0 -q --durations=25 \
  tests/unit/system/test_dump_split_apps.py tests/unit/system/test_builtin_seed.py \
  tests/unit/system/test_seed_*.py tests/unit/system/test_modelfield.py \
  tests/unit/system/test_sync_field_permissions.py

# ② 调度修复验证
.venv/bin/python -m pytest -n 4 --dist worksteal -q <同一组>

# ③ 装载成本探针（独立库，先建 pk=1 超管，否则 menumeta creator_id 外键失败）
psql "host=127.0.0.1 port=55433 user=server password=pgtest dbname=postgres" \
  -c 'DROP DATABASE IF EXISTS xadmin_seedprobe' -c 'CREATE DATABASE xadmin_seedprobe'
DB_DATABASE=xadmin_seedprobe .venv/bin/python - <<'PY'
import os; os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings_real")
import django; django.setup()
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from identity.models import UserInfo
call_command("migrate", verbosity=0)
UserInfo.objects.create_superuser(username="probe", password="Probe@2026!")
with CaptureQueriesContext(connection) as ctx:
    call_command("load_init_json", verbosity=0)
qs = ctx.captured_queries
print("queries:", len(qs), "M2M(SQL含 through 表):",
      sum(1 for q in qs if "fieldpermission_field" in q["sql"]))
PY
psql "host=127.0.0.1 port=55433 user=server password=pgtest dbname=postgres" \
  -c 'DROP DATABASE IF EXISTS xadmin_seedprobe'
```

### 12.6 交接注意（坑，按优先级）

1. **并发会话 + 提交污染**：本仓库可能有另一会话同时改代码并 `git add`；本仓库的 pre-commit 钩子
   会对暂存 py 文件跑 `ruff format` 并**自动重新 add**。`5b6ba40d` 就是这样混入了 12 个非本任务
   文件（字段类型方案）。**提交前必须 `git diff --cached --stat` 核对清单**，或先让对方提交。
2. **本次会话约定不提交**：所有改动留工作区（P0 的 workflow 改动也在工作区未暂存/未提交）。
3. macOS 无 `timeout`：长跑用 `nohup ... &` + 轮询日志（单次 sleep ≤30s）。
4. faulthandler 输出格式是 `Timeout (0:10:00)!`（**没有 "detected"**），复查日志别用错模式。
5. 看门狗参数：150s 静默 + 570s 到期转储 + 10min 单次上限 + 24min job；产物
   `test-reports/pytest.log` 与 `snapshot-N.log`（快照写独立文件，避开 pytest 非 append 文件
   描述符与追加写的偏移竞争）。
6. 探针库/测试库注意：`load_init_json` 前必须有 pk=1 超管；测试栈是 `compose.test.yml`
   （PG 55433 / Redis 56379），与开发栈（8896）互不影响；`tests/conftest.py` 的 advisory lock
   会把并发 pytest 串行化。

### 12.7 未解问题（2026-10-20 收口后）

1. ~~CI 单条 M2M JOIN 1.2s 的成因~~ → **已量化**：本地同语句 0.2~1.2ms，CI 的 1.2s 是宿主级
   放大（同提交双实例一绿一卡、`steal=0` 但 CPU 空闲）；处置不走「调优语句」，而是把这批
   语句**减到 26 条**（§13.2）。剩余对照数据由快照新增的 `pg_stat_user_tables` 提供。
2. ~~SQL 总条数与 M2M 占比尚未量化~~ → 见 §13.1（首次装载 8443 / m2m 60；重复装载
   4128 / m2m 26；改前重复装载 5088 / m2m ~980）。
3. ~~P2-B1 逐文件语义核对~~ → `test_dump_split_apps` 已试点（§13.3）；`test_seed_grants` /
   `test_builtin_seed` 验证的是「重复装载语义」与「装载前空库前提」，**必须保留** function 级
   装载，不纳入 B1。
4. `--dist worksteal` 的 CI 实测（待推送后观察 2~3 次，见 §13.5）。

## 13. 交付记录（2026-10-20 收口，改动留工作区未提交）

### 13.1 P1 基线量化（探针库：真 PG + `tests.settings_real`）

测量口径：独立库 `migrate` + 建 pk=1 超管，`CaptureQueriesContext` 统计整次
`load_init_json`（含 `migrate` 之后的所有语句；`queries_log` 上限 9000，需临时换成
无上限 deque 才拿得到全量）。

| 阶段 | 装载总语句 | 其中 m2m through 表语句 | 说明 |
|------|-----------|------------------------|------|
| 改前·首次（空库） | >9000（日志上限截断；按「每行 UPDATE+INSERT + 逐行 m2m」估算约 9.6k） | ~1500 | 每行 m2m 一次 JOIN + DELETE + INSERT |
| 改前·重复装载 | 5088 | ~980（menu_model 718 / fp_field 261 / dp_menu 14） | 行本身只发 UPDATE，m2m 仍逐行重写 |
| **改后·首次（空库）** | **8443** | **60** | 关系数 4901 条一次批量写入 |
| **改后·重复装载** | **4128** | **26** | 覆盖式重写但只有「查父行 + 清空 + 多值插入」 |

本地墙钟差别不大（2.7s / 1.8s，本地 PG 单语句亚毫秒）；**收益在语句数**——CI 的
1.2s/条是宿主级放大，减去近千条 JOIN 才是对卡死的直接作用。

### 13.2 P1 实现：种子 m2m 批量化

- 新增 `system/services/seeding/m2m.py`：`strip_m2m_fields`（授权型剥离，原在 `rows.py`）
  + `extract_m2m_relations`（属性型列表从 fixture 抽出）+ `apply_m2m_relations`（装载后批量落库）。
- `rows.build_fixtures` 返回 `(labels, notes, trimmed, m2m)`；`LoadReport.m2m` 承载载荷；
  `load_init_json._load_seed` 在 `loaddata` 之后调用一次，并打印
  `[种子装配] 批量写入 N 条多对多关系（M 组）`。
- **语义等价**：先按父行 `DELETE` 再 `bulk_create(ignore_conflicts=True)` = `loaddata` 的
  `set()`（覆盖式）；空列表同样记父行 ⇒ 清空关系；显式 `through` 模型（部门主管任命）
  不抽取，留给 `loaddata`；父行不存在时跳过（单元测试替换 `loaddata` 的标签捕获场景），
  子行缺失仍抛外键错误。
- 顺带把 `fill_field_permissions` 从「逐组一条 DELETE + 一条 INSERT」收敛成
  「全部缺口一条 DELETE + 一条多值 INSERT」（原分组去重逻辑保留，只合写语句）。
- 同时修复导出顺序缺陷：`dump_init_json` 的 m2m 预取加 `order_by("pk")`——模型默认排序是
  `-created_time`，种子行该列为 NULL，等值键排序不稳定会让「同一份数据连续两次导出」
  顺序漂移（`test_export_is_idempotent` 在多模块合跑时暴露）。副作用：下次人工
  `dump_init_json` 时各层种子文件的 m2m 数组会按主键重排（一次性、此后稳定）。

### 13.3 P2 试点：模块级种子装载

`tests/conftest.py` 新增 `seeded_module`（module 作用域）：在外层 `transaction.atomic()`
里装一次种子、模块结束 `set_rollback(True)` 整体回滚。用例自身的 `django_db` 事务以
savepoint 回滚，**种子不会泄漏到后续模块**（已验证：紧随其后的空库前提用例照常通过）。

试点 `test_dump_split_apps.py`（全部用例只读种子）：装载 8 次 → 1 次，该文件耗时
23.3s → 2.3s。`test_seed_grants` / `test_builtin_seed` 验证「重复装载语义」与「装载前空库
前提」，保留 function 级装载。

### 13.4 P3 护栏与观测

- 新增 `tests/unit/system/test_seed_m2m.py`（9 例）：抽取语义（空列表记父行、显式 through
  不抽取、非 m2m 列表不动）、落库语义（覆盖式、空列表清空、缺失父行跳过）、整体装载
  护栏（**库内关系 == 种子声明**、`fp_field` 子集、**重复装载幂等**、**through 语句数上限
  ≤8/≤60**——退回逐行写立即失败）。
- CI 快照新增 `pg_stat_user_tables`（`n_live_tup/seq_scan/idx_scan/n_tup_ins/n_tup_upd`），
  供下次复现区分「统计信息/规模」与「客户端侧」。

### 13.5 验证与待办

- 后端：`tests/unit` 4607 passed（100s）、`tests/integration` 1915 passed / 2 skipped（64s）、
  ruff check/format、mypy（seeding/permission_sync 全绿）、行数 / 函数行数 / 跨 app 门禁全绿。
- 本地尾组（§12.5 ①）：78.7s → 73.7s（`-n 0`，种子装载 23 次 → 16 次）；
  本地不是瓶颈，CI 才是观察面。
- **待办（需用户决定）**：推送后观察 CI 2~3 次；若仍出现慢磨，按快照的
  `pg_stat_user_tables` + `steal` 判断是否启用 P4（装载收尾 `ANALYZE`）。
