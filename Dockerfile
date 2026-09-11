FROM nineaiyu/xadmin-server-base:20260911_090140 AS stage-build
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
    PATH=/data/py3/bin:$PATH

ARG APT_MIRROR=http://deb.debian.org

ARG DEPENDENCIES="                    \
        gettext                       \
        curl                          \
        libmariadb-dev"

RUN set -ex \
    && sed -i "s@http://.*.debian.org@${APT_MIRROR}@g" /etc/apt/sources.list.d/debian.sources \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && apt-get update > /dev/null \
    && apt-get -y install --no-install-recommends ${DEPENDENCIES} \
    && echo "no" | dpkg-reconfigure dash \
    && apt-get clean all \
    && rm -rf /var/lib/apt/lists/*

COPY --from=stage-build /data /data
COPY --from=stage-build /usr/local/bin /usr/local/bin

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
