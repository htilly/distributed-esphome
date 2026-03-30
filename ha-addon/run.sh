#!/usr/bin/with-contenv bashio

bashio::log.info "Starting ESPHome Distributed Build Server"
bashio::log.info "Job timeout: $(bashio::config 'job_timeout')s"
bashio::log.info "Device poll interval: $(bashio::config 'device_poll_interval')s"

if bashio::config.has_value 'token'; then
    bashio::log.info "Server token: configured"
else
    bashio::log.warning "Server token: not set — will auto-generate"
fi

exec python3 /app/main.py
