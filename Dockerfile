# ============================================================================
# Copyright 2026 Jon Sherlin (real-wimpSquad)
# SPDX-License-Identifier: MIT
# ============================================================================

FROM debian:trixie-slim

# Set architecture-aware variables
ARG TARGETARCH
ENV DOTNET_ARCH=${TARGETARCH}

# C#/.NET - Microsoft provides ARM64 builds
RUN apt-get update && apt-get install -y wget apt-transport-https ca-certificates libicu-dev
RUN if [ "$TARGETARCH" = "arm64" ]; then \
      wget https://dot.net/v1/dotnet-install.sh -O dotnet-install.sh && \
      chmod +x dotnet-install.sh && \
      ./dotnet-install.sh --channel 8.0 --install-dir /usr/share/dotnet && \
      ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet; \
    else \
      wget https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -O packages-microsoft-prod.deb && \
      dpkg -i packages-microsoft-prod.deb && \
      apt-get update && apt-get install -y dotnet-sdk-8.0; \
    fi

ENV DOTNET_TOOLS_PATH=/usr/local/bin
RUN dotnet tool install csharpier --tool-path ${DOTNET_TOOLS_PATH}
RUN dotnet tool install dotnet-format --tool-path ${DOTNET_TOOLS_PATH}
RUN chmod +x ${DOTNET_TOOLS_PATH}/*

# Python - works on both architectures
RUN apt-get install -y python3 python3-pip
RUN pip3 install --break-system-packages ruff black mypy pylint

# JavaScript/TypeScript - works on both
RUN apt-get install -y nodejs curl
ENV PNPM_HOME="/usr/local/pnpm"
ENV SHELL="sh"
ENV PATH="$PNPM_HOME:$PATH"
RUN curl -fsSL https://get.pnpm.io/install.sh | ENV="$HOME/.bashrc" sh -
RUN pnpm install -g prettier eslint typescript

# Rust - rustup handles architecture automatically
RUN apt-get install -y curl gcc
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
RUN rustup component add rustfmt clippy

# Cleanup
RUN apt-get clean && rm -rf /var/lib/apt/lists/*
