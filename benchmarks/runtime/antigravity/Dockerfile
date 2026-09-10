# Pinned to specific Debian Bookworm Python 3.11-slim digest
FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 agy && \
    useradd -u 1000 -g agy -m -s /bin/bash agy

# Download and verify official Antigravity CLI binary
# Source: https://antigravity.google/cli/install.sh
# Manifest: https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_amd64.json
ARG AGY_URL="https://storage.googleapis.com/antigravity-public/antigravity-cli/1.2.0-5210873191596032/linux-x64/cli_linux_x64.tar.gz"
ARG AGY_TAR_SHA512="d190b25a04ed2a03b0587838476494ae59d0d324ee6373263cabe584352c254f9051f3689c79ac4add8817ff6eb34b11694acd77002c55e91bd1d28b6dfdae22"
ARG AGY_BIN_SHA256="195bf11b249deebe67028305a9b7b1d19ac38e9ab281b786a163a7d2fc8ff428"

RUN curl -fsSL "$AGY_URL" -o /tmp/agy.tar.gz && \
    echo "$AGY_TAR_SHA512  /tmp/agy.tar.gz" | sha512sum -c - && \
    tar -xzf /tmp/agy.tar.gz -C /tmp antigravity && \
    echo "$AGY_BIN_SHA256  /tmp/antigravity" | sha256sum -c - && \
    mv /tmp/antigravity /usr/local/bin/agy && \
    chmod +x /usr/local/bin/agy && \
    rm -f /tmp/agy.tar.gz

RUN mkdir -p /workspace /home/agy/.gemini && \
    chown -R agy:agy /workspace /home/agy

USER agy
WORKDIR /workspace
ENV HOME=/home/agy
ENV PATH="/usr/local/bin:$PATH"

ENTRYPOINT ["agy"]
CMD ["--help"]
