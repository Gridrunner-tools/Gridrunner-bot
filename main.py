#!/usr/bin/env python3
# My Trading Bot | Ethereum | Standard
# Customer: Customer
import time
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from config_loader import load_config
from modules.stop_loss import StopLossModule
from modules.take_profit import TakeProfitModule
from modules.limit_orders import LimitOrdersModule
from modules.risk_manager import RiskManagerModule
from modules.telegram_alerts import TelegramAlertsModule
from modules.wallet_tracker import WalletTrackerModule
from modules.copy_trading import CopyTradingModule
from modules.scanner import ScannerModule

# ── Keep-alive server (required for Render free tier) ──────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is running')
    def log_message(self, format, *args): pass  # silence request logs

def start_health_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    print('Health server listening on port ' + str(port))
    server.serve_forever()

# ── Bot logic ──────────────────────────────────────────────────────────────
def main():
    cfg = load_config('config.json')
    print('My Trading Bot starting...')
    stop_loss = StopLossModule(cfg)
    take_profit = TakeProfitModule(cfg)
    limit_orders = LimitOrdersModule(cfg)
    risk_manager = RiskManagerModule(cfg)
    telegram_alerts = TelegramAlertsModule(cfg)
    wallet_tracker = WalletTrackerModule(cfg)
    copy_trading = CopyTradingModule(cfg)
    scanner = ScannerModule(cfg)
    wallet_tracker.on('activity',copy_trading.on_activity)
    copy_trading.set_risk(risk_manager)
    stop_loss.start()
    take_profit.start()
    limit_orders.start()
    risk_manager.start()
    telegram_alerts.start()
    wallet_tracker.start()
    copy_trading.start()
    scanner.start()
    print('Running - press Ctrl+C to stop')
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: print('Stopped.')

if __name__ == '__main__':
    # Start health server in background thread
    t = threading.Thread(target=start_health_server, daemon=True)
    t.start()
    # Start bot
    main()