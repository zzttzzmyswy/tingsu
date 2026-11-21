#!/bin/sh
# Compile against the same glibc baseline as the media helpers.
set -eu
dest=$(readlink -f "$1")
project=$(dirname "$(dirname "$(dirname "$(readlink -f "$0")")")")
mkdir -p "$(dirname "$dest")" "$(go env GOCACHE)"
go mod download
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e GOROOT=/go -e GOMODCACHE=/modules -e GOCACHE=/buildcache -e CGO_ENABLED=1 \
    -v "$(go env GOROOT):/go:ro" -v "$(go env GOMODCACHE):/modules:ro" -v "$(go env GOCACHE):/buildcache" \
    -v "$project:/src:ro" -w /src -v "$(dirname "$dest"):/output" tingsu-go-linux-builder:22.04 \
    /go/bin/go build -tags novulkan -trimpath -buildvcs=false -ldflags="-s -w" -o "/output/$(basename "$dest")" ./cmd/tingsu
