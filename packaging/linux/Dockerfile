FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
COPY apt-packages.txt /tmp/tingsu-apt-packages
RUN apt-get update && xargs apt-get install -y --no-install-recommends </tmp/tingsu-apt-packages && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir --only-binary=:all: --target /opt/tingsu-python \
    'requests>=2.32,<3' 'httpx==0.28.1' 'pypdf>=6,<7'
