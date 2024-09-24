FROM python:3.12.6-slim

# add pip cn mirrors
ARG PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple

# set apt cn mirrors
RUN sed -i s@deb.debian.org@mirrors.tuna.tsinghua.edu.cn@ /etc/apt/sources.list.d/debian.sources
RUN apt update && apt-get install gettext libmariadb-dev g++ pkg-config -y && rm -rf /var/lib/apt/lists/*

# install pip
COPY requirements.txt /opt/requirements.txt
RUN cd /opt/ && pip install -U setuptools pip -i ${PIP_MIRROR} --ignore-installed && pip install --no-cache-dir -r requirements.txt -i ${PIP_MIRROR}

#RUN rm -rf /var/cache/yum/

WORKDIR /data/xadmin-server/
RUN addgroup --system --gid 1001 nginx \
    && adduser --system --disabled-login --ingroup nginx --no-create-home --home /nonexistent --gecos "nginx user" --shell /bin/false --uid 1001 nginx

USER 1001
ENTRYPOINT ["/bin/bash", "entrypoint.sh"]

CMD ["start", "all"]