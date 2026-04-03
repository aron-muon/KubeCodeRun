# syntax=docker/dockerfile:1
# KubeCodeRun execution runner — static binary for all language containers.

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

FROM golang:1.24-alpine AS builder

WORKDIR /build
COPY go.mod *.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /runner .

FROM scratch

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Runner" \
      org.opencontainers.image.description="Execution runner binary for KubeCodeRun language containers" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

COPY --from=builder /runner /runner
