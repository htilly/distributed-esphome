ARG BUILD_FROM
ARG BUILD_ARCH
ARG BUILD_VERSION

FROM $BUILD_FROM

LABEL \
    io.hass.name="ESPHome Distributed Build Server" \
    io.hass.description="Distributed ESPHome compilation coordinator" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}"

# Install build deps for local worker — matches ESPHome's docker-base + build-base
ENV UV_EXTRA_INDEX_URL=https://wheels.home-assistant.io/musllinux-index/
# gcompat provides glibc compatibility for PlatformIO's pre-built cross-compiler toolchains
RUN apk add --no-cache bash git curl openssh-client patch build-base libffi-dev openssl-dev gcompat

WORKDIR /app
COPY server/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY server/ .
COPY VERSION /app/VERSION

# Bundle client code and install its dependencies for the local worker
COPY client/ /app/client/
RUN pip3 install --no-cache-dir -r /app/client/requirements.txt

ENTRYPOINT []
CMD ["python3", "/app/main.py"]
