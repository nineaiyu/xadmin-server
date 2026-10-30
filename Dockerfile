FROM nineaiyu/xadmin-server-base:20261027_002336 AS stage-build
ARG VERSION

WORKDIR /data/xadmin-server

COPY . .

RUN echo > config.yml \
    && \
    if [ -n "${VERSION}" ]; then \
        sed -i "s@VERSION = .*@VERSION = '${VERSION}'@g" server/const.py; \
    fi

FROM python:3.14.7-slim

ENV LANG=en_US.UTF-8 \
    PATH=/data/py3/bin:$PATH \
    PYTHONPATH=/data/xadmin-server/packages/xadmin-common

ARG APT_MIRROR=http://deb.debian.org

ARG DEPENDENCIES="                    \
        gettext                       \
        curl                          \
        procps                        \
        flite                         \
        sox                           \
        libmariadb-dev"

RUN set -ex \
    && sed -i "s@http://.*.debian.org@${APT_MIRROR}@g" /etc/apt/sources.list.d/debian.sources \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && apt-get update > /dev/null \
    && apt-get -y install --no-install-recommends ${DEPENDENCIES} \
    && apt-get -y upgrade \
    && echo "no" | dpkg-reconfigure dash \
    && apt-get clean all \
    && rm -rf /var/lib/apt/lists/*

COPY --from=stage-build /data /data
COPY --from=stage-build /usr/local/bin /usr/local/bin

# 基础镜像（python:3.x-slim）自带的 setuptools 滞后于修复线（CVE-2025-47273 等），
# 运行镜像内这一份不受项目依赖管理覆盖，故构建期显式升级。
# 必须用 /usr/local/bin/python 绝对路径：ENV PATH 前置了 /data/py3/bin，
# 裸 `python` 会解析到 venv 解释器，升级落在 venv 而非基础镜像目录。
ARG PIP_MIRROR=https://pypi.org/simple
RUN /usr/local/bin/python -m pip install --no-cache-dir --upgrade "setuptools>=78.1.1" -i ${PIP_MIRROR}

# base 镜像预编译的 pycache 不进运行镜像：瘦身 + 消除 secret 扫描对 autobahn
# docstring 示例私钥（cryptosign.py 的说明文本）的误报来源
RUN find /data/py3 -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# 语言包：.mo 为 gitignore 项（不随仓库分发），构建期编译进镜像，
# 避免部署遗漏 compilemessages 时界面文案回退英文（仅项目 po，不扫描 venv）
RUN cd /data/xadmin-server \
    && for po in locale/*/LC_MESSAGES/django.po; do \
        [ -f "${po}" ] && msgfmt -o "${po%.po}.mo" "${po}"; \
    done

# 以非 root 运行：容器仅需代码目录下的 tmp/（pid 文件）与 data/（日志/上传/sqlite）可写。
# 注意：bind mount 覆盖这两个目录时，宿主目录属主需与这里一致（1001），
# 否则容器内写入会失败；使用 named volume 时新卷会继承此处的属主。
RUN addgroup --system --gid 1001 xadmin \
    && adduser --system --disabled-login --ingroup xadmin --no-create-home --home /nonexistent --gecos "xadmin user" --shell /bin/false --uid 1001 xadmin \
    && mkdir -p /data/xadmin-server/tmp /data/xadmin-server/data \
    && chown -R 1001:1001 /data

WORKDIR /data/xadmin-server

VOLUME /data/xadmin-server/data

USER 1001

ENTRYPOINT ["/bin/bash", "entrypoint.sh"]

EXPOSE 8896

STOPSIGNAL SIGQUIT

CMD ["start", "all"]
