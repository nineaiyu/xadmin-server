#!/bin/bash
# 源码部署配置引导（对齐安装器 xadmin.sh install 的「自动生成配置」体验）：
#   bash ops/init_config.sh [--force] [--no-secrets]
#
# 做四件事（全部幂等）：
#   1. config.yml 缺失时从 config_example.yml 复制，并把 SECRET_KEY / DB_PASSWORD /
#      REDIS_PASSWORD 写成随机值（已有值完全不动）；
#   2. ALLOWED_HOSTS 为空时按本机 IP + 回环推导（5.0 fail-fast：缺失即拒绝启动）；
#   3. .env 缺失时从 config.yml 同步 DB_PASSWORD / REDIS_PASSWORD
#      （compose 变量插值读不了 YAML，缺 .env 时 docker compose up 直接报错拒启；
#      .env 是派生缓存——之后任何时候重跑本脚本或 dev_up.sh 都会以 config.yml 为准回写）；
#   4. 打印后续步骤。
#
# 已有配置想全部重新生成：mv config.yml config.yml.bak.$(date +%s) 后重跑（脚本不删除文件）。
# 只想补 .env、不动 config.yml：bash ops/init_config.sh --no-secrets。
set -euo pipefail

cd "$(dirname "$0")/.."

FORCE=0
NO_SECRETS=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;          # 预留：覆盖式重生成（当前实现保守，不覆盖既有文件）
    --no-secrets) NO_SECRETS=1 ;;
    -h|--help)
      sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "[init-config] 未知参数: $arg"; exit 1 ;;
  esac
done

random_str() {
  # 先限界读取再过滤：无界的 `tr </dev/urandom | head` 在 head 提前退出时会因
  # SIGPIPE 带出 141（set -o pipefail 下整个脚本报错）；512 字节原始熵可滤出
  # ~120 个字母数字，覆盖最大请求长度（49）绰绰有余
  local n=${1:-48}
  LC_ALL=C head -c 512 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | head -c "$n"
}

# 本机首个 IPv4（与安装器 ensure_allowed_hosts 同源逻辑）。
# 探测链 hostname -I（Linux）→ ip addr（Linux 备选）→ ifconfig（macOS/BSD），
# 全部落空（如纯回环环境）返回空 → 白名单退化为纯回环。
# 每段管道都带 || true / 2>/dev/null：hostname -I 在 BSD 下直接报错退出，
# set -e + pipefail 会把非零退出放大成整个脚本中止（退出码 1，输出被吞）
get_host_ip() {
  local ip=""
  ip="$(hostname -I 2>/dev/null | grep -Eo '(^|[[:space:]])[0-9]+(\.[0-9]+){3}' | head -n1 | tr -d '[:space:]' || true)"
  if [ -z "$ip" ] && command -v ip >/dev/null 2>&1; then
    ip="$(ip -4 addr 2>/dev/null | awk '/inet / && $2 !~ /^127\./ {print $2}' | head -n1 | cut -d/ -f1 || true)"
  fi
  if [ -z "$ip" ] && command -v ifconfig >/dev/null 2>&1; then
    ip="$(ifconfig 2>/dev/null | awk '/inet / && $2 !~ /^127\./ {print $2}' | head -n1 || true)"
  fi
  [ -n "$ip" ] && printf '%s\n' "$ip"
  return 0
}

# 从 .env 读派生缓存的凭据（config.yml 重建时回读用，避免换密码锁死存量库卷）。
# 文件存在守卫：set -e + pipefail 下 sed 读不存在文件的非零退出码不会被 2>/dev/null 吞掉
get_env_key() {
  [ -f .env ] || return 0
  sed -n -E "s/^${1}=//p" .env | head -n1
}

# 在 config.yml 中把顶层键 KEY 的值替换为 $2。
# 优先级：已有未注释行原位改值 > 模板注释占位行（如 #DB_PASSWORD: <your-db-password>）
# 原位解注释填值（密码落在对应配置段里，而不是追加到文件末尾）> 都没有才追加末尾。
# 注释占位只取第一处：模板里 DB_PASSWORD 在「本地非 Docker 开发」备用注释块还有一份
# 占位，必须保持注释态，否则产生重复键（YAML 后者胜出，改主段会静默不生效）。
# 地址范围 1,/re/ 在 GNU 与 BSD sed 下语义一致（只到首次匹配），不能用 GNU 专有的 0,/re/。
#
# 块序列/块映射形态（KEY: 换行缩进子项，数组类配置常见的手工写法）不能只替换键行——
# 残留的缩进子项会直接产生非法 YAML（实测：ALLOWED_HOSTS 被手工配为块序列域名后
# 重跑本脚本，容器解析 config 崩溃）。键行存在但无内联值时先删块再追加标量值。
set_yaml_key() {
  local key=$1 value=$2
  if grep -qE "^${key}:[[:space:]]*[^[:space:]]" config.yml; then
    # 键行有内联值：原位改值，不动其他行
    sed -i.bak "s@^${key}:.*@${key}: ${value}@" config.yml && rm -f config.yml.bak
  elif grep -qE "^${key}:" config.yml; then
    if grep -A1 -E "^${key}:" config.yml | grep -qE '^[[:space:]]+[^[:space:]#]'; then
      # 块形态（键行下方紧跟缩进子项）：删键行与子项后追加标量值
      remove_yaml_key_block "$key" >config.yml.tmp
      printf '\n%s: %s\n' "${key}" "${value}" >>config.yml.tmp
      mv config.yml.tmp config.yml
    else
      # 真空值行（无子项）：原位改值，键保持在原配置段位置
      sed -i.bak "s@^${key}:.*@${key}: ${value}@" config.yml && rm -f config.yml.bak
    fi
  elif grep -qE "^#[[:space:]]*${key}:" config.yml; then
    sed -i.bak "1,/^#[[:space:]]*${key}:/s@^#[[:space:]]*${key}:.*@${key}: ${value}@" config.yml && rm -f config.yml.bak
  else
    printf '\n%s: %s\n' "${key}" "${value}" >>config.yml
  fi
}

get_yaml_key() {
  [ -f config.yml ] || return 0
  sed -n -E "s/^${1}:[[:space:]]*//p" config.yml | head -n1 | tr -d "\"'"
}

# 键是否已配置（块感知）：键行有内联值，或键行存在且下方紧跟缩进子项（块序列/块映射）。
# get_yaml_key 对块形态返回空串，直接判空会把用户已配置的块形态键误判为未配置
# （进而覆盖成推导值）——判定"是否动用户配置"必须用本函数。
yaml_key_configured() {
  [ -f config.yml ] || return 1
  if grep -qE "^${1}:[[:space:]]*[^[:space:]]" config.yml; then
    return 0
  fi
  # 键行存在但无内联值：仅当下方紧跟缩进子项（首个非空白字符非 #）才算已配置
  grep -A1 -E "^${1}:" config.yml | grep -qE '^[[:space:]]+[^[:space:]#]'
}

# 删除顶层键行及其连续缩进子项（块序列/块映射），输出到 stdout。
# awk 而非 sed：sed 的相对地址区间（,+N）与多行删除在 GNU/BSD 下行为不一致；
# awk 逐行状态机两端语义一致。首个非缩进行（含注释/空行/下一顶层键）即块结束。
remove_yaml_key_block() {
  awk -v key="^${1}:" '
    $0 ~ key { in_block = 1; next }
    in_block && /^[[:space:]]/ { next }
    in_block { in_block = 0 }
    { print }
  ' config.yml
}

# 从 docker-compose.yml 解析 ipam 固定子网（compose 网段的事实源）：网络 down/up 重建后
# 仍按声明创建该段，容器 IP 只在段内重排——TRUSTED_PROXY_IPS 按此网段信任永不失效。
# 无 compose 文件 / 未声明子网时返回空（调用方降级为提示，不猜值）。
get_compose_subnet() {
  [ -f docker-compose.yml ] || return 0
  sed -n 's/^[[:space:]]*- subnet:[[:space:]]*//p' docker-compose.yml | head -n1
}

echo "[init-config] 1/3 检查 config.yml ..."
if [ ! -f config.yml ]; then
  cp config_example.yml config.yml
  echo "[init-config]   已从 config_example.yml 生成 config.yml"
else
  echo "[init-config]   config.yml 已存在，保持不动（需要重新生成请先自行备份移走）"
fi

if [ "$NO_SECRETS" != "1" ]; then
  echo "[init-config] 2/3 写入密钥与凭据（已有值保留，空值才生成随机值）..."
  if [ -z "$(get_yaml_key SECRET_KEY)" ]; then
    set_yaml_key SECRET_KEY "$(random_str 49)"
    echo "[init-config]   SECRET_KEY: 已生成（随机 49 位）"
  else
    echo "[init-config]   SECRET_KEY: 已有值，保留"
  fi

  # DB/Redis 密码取值链：config.yml > .env（派生缓存回读）> 随机生成。
  # .env 回读是关键：PG 数据卷以首装密码初始化，config.yml 意外丢失后重生成新密码
  # 会让应用连不上旧库；从 .env 回读才与既有卷一致。
  generated_db=0
  db_password="$(get_yaml_key DB_PASSWORD)"
  if [ -z "$db_password" ]; then
    db_password="$(get_env_key DB_PASSWORD)"
    if [ -n "$db_password" ]; then
      set_yaml_key DB_PASSWORD "$db_password"
      echo "[init-config]   DB_PASSWORD: config.yml 缺失，从 .env 回读（保护存量数据卷）"
    else
      db_password="$(random_str 24)"
      set_yaml_key DB_PASSWORD "$db_password"
      generated_db=1
    fi
  fi
  generated_redis=0
  redis_password="$(get_yaml_key REDIS_PASSWORD)"
  if [ -z "$redis_password" ]; then
    redis_password="$(get_env_key REDIS_PASSWORD)"
    if [ -n "$redis_password" ]; then
      set_yaml_key REDIS_PASSWORD "$redis_password"
      echo "[init-config]   REDIS_PASSWORD: config.yml 缺失，从 .env 回读（保护存量数据卷）"
    else
      redis_password="$(random_str 24)"
      set_yaml_key REDIS_PASSWORD "$redis_password"
      generated_redis=1
    fi
  fi
  if [ "$generated_db" = "1" ] || [ "$generated_redis" = "1" ]; then
    echo "[init-config]   DB_PASSWORD / REDIS_PASSWORD: 空值已生成随机密码（并同步进 .env，见下）"
  else
    echo "[init-config]   DB_PASSWORD / REDIS_PASSWORD: 已有值，保留"
  fi
else
  echo "[init-config] 2/3 跳过密钥写入（--no-secrets）"
fi

# 服务端 5.0 起 DEBUG=false 且 ALLOWED_HOSTS 为空即拒绝启动（fail-fast）；
# config_example.yml 中该键是注释占位， fresh 部署若不补上，容器一起来就重启循环。
# 与安装器 ensure_allowed_hosts 同口径：本机 IP + 回环推导 JSON 数组；已配置则不覆盖
# （块感知判定：手工配置的块序列域名形态 get_yaml_key 返回空，不能据此判未配置）。
if [ "$NO_SECRETS" != "1" ] && ! yaml_key_configured ALLOWED_HOSTS; then
  host_ip="$(get_host_ip)"
  entries="\"127.0.0.1\", \"localhost\""
  if [ -n "$host_ip" ]; then
    entries="\"${host_ip}\", ${entries}"
  fi
  set_yaml_key ALLOWED_HOSTS "[${entries}]"
  echo "[init-config]   ALLOWED_HOSTS: 已按本机 IP / 回环推导（[${entries}]），域名部署时改为 [\"你的域名\"]"
fi

# TRUSTED_PROXY_IPS：compose 固定子网是网段事实源（ipam.config.subnet，网络重建后恒定），
# 解析到即可安全自动写入——宿主机 nginx 反代容器时 REMOTE_ADDR（docker 网关）必在该段内，
# 零手工即取到访客真实 IP；解析不到子网（无 compose 文件 / 自定义网络）时不猜值，
# 降级为漏配提示（代理在别的网段时需手工补充其地址）。
compose_subnet="$(get_compose_subnet)"
if [ "$NO_SECRETS" != "1" ] && [ -n "$compose_subnet" ] && ! yaml_key_configured TRUSTED_PROXY_IPS; then
  set_yaml_key TRUSTED_PROXY_IPS "[\"${compose_subnet}\"]"
  echo "[init-config]   TRUSTED_PROXY_IPS: 已按 compose 固定子网写入 [\"${compose_subnet}\"]（网络重建不失效）"
elif ! yaml_key_configured TRUSTED_PROXY_IPS; then
  echo "[init-config]   TRUSTED_PROXY_IPS: 未配置（默认不信任 X-Forwarded-For；直连部署无需配置）"
  echo "[init-config]   ↳ 若通过 HTTP 反代（宿主机 nginx / xadmin-web 容器）访问，请在 config.yml 配置"
  echo "[init-config]     该项为后端看到的直连地址（如 docker 网桥网段），否则审计日志记录代理内网 IP"
fi

echo "[init-config] 3/3 同步 .env（compose 凭据派生缓存）..."
# compose_env.sh 的 sync_compose_credentials：config.yml 同名键为准 > 既有 .env > 随机生成；
# 随机生成只发生在 config.yml 也没值时（上方已保证有值，因此这里不会生成与 config.yml
# 不一致的密码）。生成/回写结果持久化到 .env，权限 0600。
. ops/compose_env.sh
sync_compose_credentials >/dev/null
chmod 600 .env 2>/dev/null || true

echo
echo "[init-config] 配置就绪。后续步骤（幂等，可重复执行）："
echo "  docker compose build        # 首次或依赖变更后；仅改代码可跳过"
echo "  docker compose up -d        # 启动后端全栈"
echo "  docker exec -i xadmin-server python ops/init_data.py   # 初始化数据（管理员+种子，密码仅打印一次）"
echo "  前端: cd ../xadmin-client && pnpm build（产物托管/反代见文档「容器化部署 · 前端部署」）"
