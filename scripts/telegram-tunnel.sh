#!/usr/bin/env bash
#
# Поднимает на сервере постоянный SOCKS5-туннель до зарубежного сервера,
# чтобы уведомления доходили до api.telegram.org (из России он недоступен).
#
#   sudo ./scripts/telegram-tunnel.sh user@1.2.3.4 [ssh-порт]
#
# Нужен любой зарубежный сервер, куда есть доступ по SSH (например, ваш VPN-сервер).
# Скрипт ставит autossh, создаёт systemd-сервис и печатает строку,
# которую надо вставить в админке в поле «Прокси для Telegram».

set -euo pipefail

SSH_TARGET="${1:-}"
SSH_PORT="${2:-22}"
SOCKS_PORT=1080
SERVICE_NAME=telegram-tunnel
KEY_PATH=/root/.ssh/id_ed25519

info()  { echo -e "\033[1;34m▶\033[0m $*"; }
ok()    { echo -e "\033[1;32m✓\033[0m $*"; }
warn()  { echo -e "\033[1;33m!\033[0m $*"; }
fail()  { echo -e "\033[1;31m✗\033[0m $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Запускать под root: sudo $0 $*"
[ -n "$SSH_TARGET" ] || fail "Укажите SSH-адрес зарубежного сервера: sudo $0 user@1.2.3.4 [ssh-порт]"

# ── 1. А нужен ли туннель вообще ──────────────────────────────────────────────
info "Проверяю, доступен ли Telegram напрямую..."
if curl -s -m 12 -o /dev/null https://api.telegram.org/bot123:fake/getMe; then
    ok "api.telegram.org отвечает напрямую — туннель не нужен."
    echo "  Поле «Прокси для Telegram» в админке можно оставить пустым."
    exit 0
fi
warn "Напрямую Telegram недоступен — поднимаю туннель."

# ── 2. Зависимости ────────────────────────────────────────────────────────────
info "Ставлю autossh..."
if ! command -v autossh >/dev/null; then
    apt-get update -qq && apt-get install -y -qq autossh
fi
ok "autossh готов."

# ── 3. Ключ и доступ к зарубежному серверу ────────────────────────────────────
if [ ! -f "$KEY_PATH" ]; then
    info "Создаю SSH-ключ для root..."
    ssh-keygen -t ed25519 -N "" -f "$KEY_PATH" -q
fi

info "Проверяю вход по ключу на $SSH_TARGET..."
if ! ssh -i "$KEY_PATH" -p "$SSH_PORT" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=12 "$SSH_TARGET" true 2>/dev/null; then
    echo
    warn "Вход по ключу пока не работает. Разрешите его — одной командой:"
    echo
    echo "    ssh-copy-id -i $KEY_PATH -p $SSH_PORT $SSH_TARGET"
    echo
    echo "  (спросит пароль от зарубежного сервера). Либо добавьте вручную"
    echo "  в ~/.ssh/authorized_keys на том сервере вот этот ключ:"
    echo
    cat "${KEY_PATH}.pub"
    echo
    echo "  После этого запустите скрипт снова."
    exit 1
fi
ok "Вход по ключу работает."

# ── 4. Куда вешать SOCKS5, чтобы его видел контейнер ──────────────────────────
# Слушаем на docker-мосту: снаружи порт недоступен, из контейнера — да.
BIND_ADDR=$(ip -4 -o addr show docker0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)
if [ -z "$BIND_ADDR" ]; then
    BIND_ADDR=127.0.0.1
    warn "Мост docker0 не найден, слушаю на $BIND_ADDR."
    warn "Если парсер работает в контейнере, запустите его с network_mode: host."
fi
info "SOCKS5 будет слушать на ${BIND_ADDR}:${SOCKS_PORT}"

# ── 5. systemd-сервис ─────────────────────────────────────────────────────────
info "Создаю сервис ${SERVICE_NAME}..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<UNIT
[Unit]
Description=SOCKS5 tunnel for Telegram API
After=network-online.target docker.service
Wants=network-online.target

[Service]
Environment=AUTOSSH_GATETIME=0
ExecStart=/usr/bin/autossh -M 0 -N \\
    -D ${BIND_ADDR}:${SOCKS_PORT} \\
    -i ${KEY_PATH} -p ${SSH_PORT} \\
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \\
    -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \\
    ${SSH_TARGET}
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}" >/dev/null 2>&1 || systemctl restart "${SERVICE_NAME}"
sleep 3

systemctl is-active --quiet "${SERVICE_NAME}" \
    || fail "Сервис не поднялся. Логи: journalctl -u ${SERVICE_NAME} -n 30"
ok "Туннель запущен и будет подниматься сам после перезагрузки."

# ── 6. Проверка ───────────────────────────────────────────────────────────────
info "Проверяю Telegram через туннель..."
CODE=$(curl -s -m 25 -o /dev/null -w '%{http_code}' \
       --proxy "socks5h://${BIND_ADDR}:${SOCKS_PORT}" \
       https://api.telegram.org/bot123:fake/getMe || echo 000)

if [ "$CODE" = "401" ]; then
    ok "Telegram доступен через туннель (401 — это правильный ответ на неверный токен)."
else
    warn "Ответ через туннель: $CODE. Проверьте логи: journalctl -u ${SERVICE_NAME} -n 30"
fi

EXIT_IP=$(curl -s -m 20 --proxy "socks5h://${BIND_ADDR}:${SOCKS_PORT}" https://ipinfo.io/ip || echo "?")

echo
echo "─────────────────────────────────────────────────────────────"
echo " Готово. Выходной IP туннеля: ${EXIT_IP}"
echo
echo " В админке → Настройки → Telegram → «Прокси для Telegram»"
echo " вставьте:"
echo
echo "     socks5://${BIND_ADDR}:${SOCKS_PORT}"
echo
echo " Сохраните настройки и нажмите «Отправить тестовое сообщение»."
echo "─────────────────────────────────────────────────────────────"
echo
echo " Управление туннелем:"
echo "   systemctl status ${SERVICE_NAME}"
echo "   journalctl -u ${SERVICE_NAME} -f"
echo "   systemctl restart ${SERVICE_NAME}"
