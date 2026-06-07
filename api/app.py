import os
from flask import Flask, request, jsonify
import subprocess
import sqlite3

app = Flask(__name__)

API_KEY = os.environ.get("VPN_API_KEY", "change_me")
DB_PATH = os.environ.get("DB_PATH", "/etc/amnezia/vpn.db")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/home/vpn_clients")

def get_config(client_name):
    path = os.path.join(CONFIG_DIR, f"{client_name}.conf")
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return None

@app.route('/create', methods=['POST'])
def create():
    if request.headers.get('X-Api-Key') != API_KEY:
        return jsonify({"error": "unauthorized"}), 403

    data = request.json
    client_name = data['client_name']
    days = int(data['days'])

    result = subprocess.run(
        ['/usr/local/bin/create_client.sh', client_name, str(days)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return jsonify({"error": result.stderr.strip()}), 500

    config_text = get_config(client_name)
    if not config_text:
        return jsonify({"error": "config not found"}), 500

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT paid_until FROM clients WHERE client_name=?",
        (client_name,)
    ).fetchone()
    conn.close()

    return jsonify({
        "config": config_text,
        "paid_until": row[0] if row else "unknown"
    })

if __name__ == '__main__':
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", 5000))
    app.run(host=host, port=port)