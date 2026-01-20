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

# Go - native multi-arch support
RUN apt-get install -y golang
ENV GOPATH=/usr/local/go-tools \
    PATH=/usr/local/go-tools/bin:$PATH
RUN go install golang.org/x/tools/cmd/goimports@latest
RUN go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest

# Java - OpenJDK + formatters (Trixie ships with 21/23, 17 only headless)
RUN apt-get install -y openjdk-21-jdk maven
RUN mkdir -p /usr/local/java-tools && \
    wget https://github.com/google/google-java-format/releases/download/v1.22.0/google-java-format-1.22.0-all-deps.jar \
    -O /usr/local/java-tools/google-java-format.jar && \
    wget https://github.com/checkstyle/checkstyle/releases/download/checkstyle-10.14.2/checkstyle-10.14.2-all.jar \
    -O /usr/local/java-tools/checkstyle.jar && \
    wget https://raw.githubusercontent.com/checkstyle/checkstyle/checkstyle-10.14.2/src/main/resources/google_checks.xml \
    -O /usr/local/java-tools/google_checks.xml

# C/C++ - LLVM/Clang tools
RUN apt-get install -y clang-format clang-tidy

# Shell - shfmt + shellcheck
RUN apt-get install -y shellcheck
RUN go install mvdan.cc/sh/v3/cmd/shfmt@latest

# SQL - sqlfluff (Python-based, ignore system package conflicts)
RUN pip3 install --break-system-packages --ignore-installed sqlfluff

# PHP - composer-based tools
RUN apt-get install -y php php-xml php-mbstring php-curl unzip
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer
ENV COMPOSER_HOME=/usr/local/composer
RUN composer global require friendsofphp/php-cs-fixer
RUN composer global require phpstan/phpstan
ENV PATH="${COMPOSER_HOME}/vendor/bin:$PATH"

# Kotlin - ktlint (standalone binary)
RUN wget https://github.com/pinterest/ktlint/releases/download/1.1.1/ktlint -O /usr/local/bin/ktlint && \
    chmod +x /usr/local/bin/ktlint

# ============================================================================
# EXPERIMENTAL/OPTIONAL TOOLS (failures won't break build)
# ============================================================================

# Swift - only for arm64/amd64 Linux (limited support, may fail on some platforms)
RUN if [ "$TARGETARCH" = "arm64" ] || [ "$TARGETARCH" = "amd64" ]; then \
      (apt-get install -y binutils git gnupg2 libc6-dev libcurl4-openssl-dev \
        libedit2 libgcc-s1 libpython3-dev libsqlite3-0 libstdc++6 libxml2-dev \
        libz3-dev pkg-config tzdata zlib1g-dev && \
      pip3 install --break-system-packages swiftformat && \
      echo "✓ Swift tools installed") || \
      echo "⚠ Swift tools installation failed (non-critical)"; \
    else \
      echo "⚠ Swift not supported on $TARGETARCH (non-critical)"; \
    fi

# Ruby - rbenv + rubocop
RUN apt-get install -y ruby ruby-dev
RUN gem install rubocop

# Markdown - markdownlint (npm)
RUN pnpm install -g markdownlint-cli

# YAML - yamllint (Python)
RUN pip3 install --break-system-packages yamllint

# ============================================================================
# VERIFICATION & HEALTH CHECKS
# ============================================================================

# Verify critical tools are installed
RUN echo "=== Verifying critical tools ===" && \
    python3 --version && \
    ruff --version && \
    node --version && \
    prettier --version && \
    dotnet --version && \
    rustc --version && \
    go version && \
    java -version && \
    clang-format --version && \
    shellcheck --version && \
    echo "✓ All critical tools verified"

# List optional tools status
RUN echo "=== Optional tools status ===" && \
    (swiftformat --version 2>/dev/null && echo "✓ Swift tools available") || echo "⚠ Swift tools not available" && \
    echo "=== Tool verification complete ==="

# Cleanup
RUN apt-get clean && rm -rf /var/lib/apt/lists/*
