FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
COPY apt-packages.txt /tmp/tingsu-apt-packages
RUN apt-get update && xargs apt-get install -y --no-install-recommends </tmp/tingsu-apt-packages && rm -rf /var/lib/apt/lists/*
