FROM python:3.12.1-slim

# Fixes some weird terminal issues such as broken clear / CTRL+L
ARG PIP_MIRROR=https://mirrors.aliyun.com/pypi/simple

RUN apt update && apt-get install libmariadb-dev g++ pkg-config git -y && rm -rf /var/lib/apt/lists/*

# install pip
COPY requirements.txt /opt/requirements.txt
RUN cd /opt/ && pip install -U setuptools pip -i ${PIP_MIRROR} --ignore-installed && pip install --no-cache-dir -r requirements.txt -i ${PIP_MIRROR}

#RUN rm -rf /var/cache/yum/

WORKDIR /data/xadmin-server/
RUN addgroup --system --gid 1001 nginx \
    && adduser --system --disabled-login --ingroup nginx --no-create-home --home /nonexistent --gecos "nginx user" --shell /bin/false --uid 1001 nginx


#ENTRYPOINT ["python", "manage.py", "start", "all","-u","nginx"]
ENTRYPOINT ["/bin/bash", "entrypoint.sh"]
