#!/bin/bash
# Деактивация клиентов с истекшей подпиской
DB="${DB_PATH:-/etc/amnezia/vpn.db}"
AWG_CONF="${AWG_CONF_FILE:-/etc/amneziawg/awg0.conf}"

EXPIRED=$(sqlite3 "$DB" "SELECT client_name FROM clients WHERE active=1 AND paid_until < datetime('now')")
for NAME in $EXPIRED; do
    sed -i "/# Client: $NAME/,/^$/d" "$AWG_CONF"
    awg syncconf awg0 <(awg-quick strip awg0)
    sqlite3 "$DB" "UPDATE clients SET active=0 WHERE client_name='$NAME'"
    echo "Деактивирован $NAME"
done