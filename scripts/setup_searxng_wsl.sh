#!/usr/bin/env bash
set -euo pipefail

install_dir="${HOME}/.local/share/searxng"
config_dir="${HOME}/.config/searxng"
service_dir="${HOME}/.config/systemd/user"

if [[ ! -d "${install_dir}/.git" ]]; then
  git clone --depth 1 https://github.com/searxng/searxng.git "${install_dir}"
else
  git -C "${install_dir}" pull --ff-only
fi

python3 -m venv "${install_dir}/.venv"
"${install_dir}/.venv/bin/python" -m pip install --upgrade pip wheel
"${install_dir}/.venv/bin/python" -m pip install \
  --requirement "${install_dir}/requirements.txt" \
  --requirement "${install_dir}/requirements-server.txt"

mkdir -p "${config_dir}" "${service_dir}"
secret="$(${install_dir}/.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
cat > "${config_dir}/settings.yml" <<EOF
use_default_settings: true
server:
  bind_address: "127.0.0.1"
  port: 8080
  secret_key: "${secret}"
  limiter: false
search:
  formats:
    - html
    - json
EOF
chmod 600 "${config_dir}/settings.yml"

cat > "${service_dir}/searxng.service" <<EOF
[Unit]
Description=Local SearXNG for Advanced Transportation News Update
After=network-online.target

[Service]
Type=simple
Environment=SEARXNG_SETTINGS_PATH=${config_dir}/settings.yml
WorkingDirectory=${install_dir}
ExecStart=${install_dir}/.venv/bin/granian --interface wsgi --host 127.0.0.1 --port 8080 searx.webapp:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable searxng.service
systemctl --user restart searxng.service
echo "SearXNG installed at $(git -C "${install_dir}" rev-parse --short HEAD)."
