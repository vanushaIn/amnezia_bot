#!/bin/bash
# Создание / восстановление клиента AmneziaWG
set -e

CLIENT_NAME="$1"
DAYS="$2"

# Загрузка переменных окружения с значениями по умолчанию
DB="${DB_PATH:-/etc/amnezia/vpn.db}"
SERVER_PUBLIC_IP="${SERVER_PUBLIC_IP:-94.103.1.198}"
SERVER_PORT="${SERVER_PORT:-51820}"
CONFIG_DIR="${CONFIG_DIR:-/home/vpn_clients}"
SERVER_PUBKEY_FILE="${SERVER_PUBKEY_FILE:-/etc/amnezia/server_public.key}"
AWG_CONF="${AWG_CONF_FILE:-/etc/amneziawg/awg0.conf}"

SERVER_PUBKEY=$(cat "$SERVER_PUBKEY_FILE")

# Проверка существования клиента
EXISTS=$(sqlite3 "$DB" "SELECT active FROM clients WHERE client_name='$CLIENT_NAME'")

if [ "$EXISTS" = "1" ]; then
    echo "Клиент $CLIENT_NAME уже активен"
    exit 1
fi

if [ "$EXISTS" = "0" ]; then
    # Восстановление старого клиента
    OLD_IP=$(sqlite3 "$DB" "SELECT ip_address FROM clients WHERE client_name='$CLIENT_NAME'")
    OLD_PUB=$(sqlite3 "$DB" "SELECT public_key FROM clients WHERE client_name='$CLIENT_NAME'")
    OLD_PRIV=$(sqlite3 "$DB" "SELECT private_key FROM clients WHERE client_name='$CLIENT_NAME'")
    IP_USED=$(sqlite3 "$DB" "SELECT used FROM ip_pool WHERE ip='$OLD_IP'")
    if [ "$IP_USED" = "1" ]; then
        NEW_IP=$(sqlite3 "$DB" "SELECT ip FROM ip_pool WHERE used=0 LIMIT 1")
        if [ -z "$NEW_IP" ]; then
            echo "Нет свободных IP-адресов"
            exit 2
        fi
        CLIENT_IP=$NEW_IP
        sqlite3 "$DB" "UPDATE clients SET ip_address='$CLIENT_IP' WHERE client_name='$CLIENT_NAME'"
    else
        CLIENT_IP=$OLD_IP
    fi
    CLIENT_PRIV=$OLD_PRIV
    CLIENT_PUB=$OLD_PUB
    echo "Восстановление клиента $CLIENT_NAME с IP $CLIENT_IP"
else
    # Новый клиент
    CLIENT_PRIV=$(awg genkey)
    CLIENT_PUB=$(echo "$CLIENT_PRIV" | awg pubkey)
    CLIENT_IP=$(sqlite3 "$DB" "SELECT ip FROM ip_pool WHERE used=0 LIMIT 1")
    if [ -z "$CLIENT_IP" ]; then
        echo "Нет свободных IP-адресов"
        exit 2
    fi
    sqlite3 "$DB" "INSERT INTO clients (client_name, private_key, public_key, ip_address, config_file) VALUES ('$CLIENT_NAME', '$CLIENT_PRIV', '$CLIENT_PUB', '$CLIENT_IP', '$CONFIG_DIR/${CLIENT_NAME}.conf')"
fi

# Активируем IP
sqlite3 "$DB" "UPDATE ip_pool SET used=1 WHERE ip='$CLIENT_IP'"
PAID_UNTIL=$(date -d "+$DAYS days" +"%Y-%m-%d %H:%M:%S")
sqlite3 "$DB" "UPDATE clients SET active=1, paid_until='$PAID_UNTIL' WHERE client_name='$CLIENT_NAME'"

# Клиентский конфиг
cat > "$CONFIG_DIR/${CLIENT_NAME}.conf" << EOF
[Interface]
PrivateKey = $CLIENT_PRIV
Address = $CLIENT_IP/24
DNS = 1.1.1.1, 8.8.8.8
Jc = 5
Jmin = 40
Jmax = 70
S1 = 70
S2 = 70
H1 = 1
H2 = 2
H3 = 3
H4 = 4
MTU = 1420

[Peer]
PublicKey = $SERVER_PUBKEY
Endpoint = $SERVER_PUBLIC_IP:$SERVER_PORT
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
EOF

# Добавление пира в серверный конфиг
cat >> "$AWG_CONF" << EOF

[Peer]
# Client: $CLIENT_NAME
PublicKey = $CLIENT_PUB
AllowedIPs = $CLIENT_IP/32
EOF

awg syncconf awg0 <(awg-quick strip awg0)
echo "Клиент $CLIENT_NAME активирован до $PAID_UNTIL"
echo "Конфиг: $CONFIG_DIR/${CLIENT_NAME}.conf"