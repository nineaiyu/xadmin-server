#!/bin/bash
# 源码部署配置引导（对齐安装器 xadmin.sh install 的「自动生成配置」体验）：
#   bash ops/init_config.sh [--force] [--no-secrets]
#
# 做三件事（全部幂等）：
#   1. config.yml 缺失时从 config_example.yml 复制，并把 SECRET_KEY / DB_PASSWORD /
#      REDIS_PASSWORD 写成随机值（已存在的 config.yml 完全不动）；
#   2. .env 缺失时从 config.yml 同步 DB_PASSWORD / REDIS_PASSWORD
#      （compose 变量插值读不了 YAML，缺 .env 时 docker compose up 直接报错拒启；
#      .env 是派生缓存——之后任何时候重跑本脚本或 dev_up.sh 都会以 config.yml 为准回写）；
#   3. 打印后续步骤。
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
      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
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

# 从 .env 读派生缓存的凭据（config.yml 重建时回读用，避免换密码锁死存量库卷）。
# 文件存在守卫：set -e + pipefail 下 sed 读不存在文件的非零退出码不会被 2>/dev/null 吞掉
get_env_key() {
  [ -f .env ] || return 0
  sed -n -E "s/^${1}=//p" .env | head -n1
}

# 在 config.yml 中把顶层键 KEY 的值替换为 $2（无该行时在文件末尾追加）。
# 只匹配行首的 KEY（config.yml 无缩进语法），带 # 前缀的注释行不动。
set_yaml_key() {
  local key=$1 value=$2
  if grep -qE "^${key}:" config.yml; then
    sed -i.bak "s@^${key}:.*@${key}: ${value}@" config.yml && rm -f config.yml.bak
  else
    printf '\n%s: %s\n' "${key}" "${value}" >>config.yml
  fi
}

get_yaml_key() {
  [ -f config.yml ] || return 0
  sed -n -E "s/^${1}:[[:space:]]*//p" config.yml | head -n1 | tr -d "\"'"
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
