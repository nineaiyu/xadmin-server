#!/bin/bash
# docker compose 凭据解析共享模块（由 dev_up.sh / dev_down.sh source，勿直接执行）。
#
# 语义：config.yml 是凭据的唯一定义处（用户只需编辑这一个文件）；
# 本模块把同名键的值同步为 .env 派生缓存——.env 由脚本自动维护（勿手工编辑、
# 已 gitignore），存在后用户绕过脚本直接操作 `docker compose <任意命令>`
# （restart / logs / down 等）也无需再传环境变量——compose 解析文件即需要这些值。
# 优先级：config.yml 同名键 > 既有 .env > 随机生成（生成值必须落盘持久化，
# 否则重启后与已初始化的 PG 数据卷密码错位）。

# 项目根自定位：不依赖调用时的 cwd（xadmin.sh 可从任意目录调用）。
# 调用方可注入 XADMIN_PROJECT_DIR 覆盖；缺省按本文件位置上跳一级，兜底当前目录
_compose_env_root="${XADMIN_PROJECT_DIR:-}"
if [ -z "$_compose_env_root" ]; then
  _compose_env_self="${BASH_SOURCE[0]:-$0}"
  _compose_env_root="$(cd "$(dirname "$_compose_env_self")/.." 2>/dev/null && pwd)"
fi
: "${_compose_env_root:=$PWD}"

sync_compose_credentials() {
  local db_pass redis_pass
  db_pass="$(sed -n -E 's/^DB_PASSWORD:[[:space:]]*//p' "$_compose_env_root/config.yml" 2>/dev/null | head -n1 | tr -d "\"'")"
  redis_pass="$(sed -n -E 's/^REDIS_PASSWORD:[[:space:]]*//p' "$_compose_env_root/config.yml" 2>/dev/null | head -n1 | tr -d "\"'")"

  local env_db="" env_redis=""
  if [ -f "$_compose_env_root/.env" ]; then
    env_db="$(sed -n -E 's/^DB_PASSWORD=//p' "$_compose_env_root/.env" | head -n1)"
    env_redis="$(sed -n -E 's/^REDIS_PASSWORD=//p' "$_compose_env_root/.env" | head -n1)"
  fi

  if [ -z "$db_pass" ]; then
    db_pass="$env_db"
    if [ -z "$db_pass" ]; then
      db_pass="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)"
      echo "[compose-env] config.yml 未定义 DB_PASSWORD：已随机生成并持久化到 .env"
      echo "[compose-env] （在 config.yml 定义同名键后即可单文件管理，届时 .env 会自动跟随）"
    fi
  fi
  if [ -z "$redis_pass" ]; then
    redis_pass="$env_redis"
    if [ -z "$redis_pass" ]; then
      redis_pass="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)"
      echo "[compose-env] config.yml 未定义 REDIS_PASSWORD：已随机生成并持久化到 .env"
    fi
  fi

  # 同步 .env 派生缓存（config.yml 为准；仅内容变化时改写）
  local need_write=0
  [ "$env_db" != "$db_pass" ] && need_write=1
  [ "$env_redis" != "$redis_pass" ] && need_write=1
  [ ! -f "$_compose_env_root/.env" ] && need_write=1
  if [ "$need_write" = "1" ]; then
    umask 077
    printf 'DB_PASSWORD=%s\nREDIS_PASSWORD=%s\n' "$db_pass" "$redis_pass" >"$_compose_env_root/.env"
  fi

  # 进程内导出：compose 插值中 shell 环境变量优先于 .env，本脚本内以 config.yml 为准
  export DB_PASSWORD="$db_pass" REDIS_PASSWORD="$redis_pass"

  if [ -n "$env_db" ] && [ "$env_db" != "$db_pass" ]; then
    echo "[compose-env] 提示：.env 已按 config.yml 更新 DB_PASSWORD。"
    echo "[compose-env] 注意：修改已初始化 PG 数据卷的密码需特殊流程，直接改配置不会改变卷内密码。"
  fi
}
