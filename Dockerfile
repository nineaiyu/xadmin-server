FROM python:3.12.7-slim

# add pip cn mirrors
ARG PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
ARG APT_MIRROR=http://mirrors.tuna.tsinghua.edu.cn

# set apt cn mirrors
RUN sed -i "s@http://.*.debian.org@${APT_MIRROR}@g" /etc/apt/sources.list.d/debian.sources

RUN apt update  \
    && apt-get install gettext libmariadb-dev g++ pkg-config curl --no-install-recommends -y  \
    && apt-get clean all  \
    && rm -rf /var/lib/apt/lists/*

# install pip
WORKDIR /opt/
COPY requirements.txt requirements.txt
RUN pip install -U setuptools pip --ignore-installed -i ${PIP_MIRROR}  \
    && pip install --no-cache-dir -r requirements.txt -i ${PIP_MIRROR}

WORKDIR /data/xadmin-server/
RUN addgroup --system --gid 1001 nginx \
    && adduser --system --disabled-login --ingroup nginx --no-create-home --home /nonexistent --gecos "nginx user" --shell /bin/false --uid 1001 nginx

USER 1001
ENTRYPOINT ["/bin/bash", "entrypoint.sh"]

CMD ["start", "all"]