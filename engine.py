import os
import sys
import time
import json
import random
from datetime import datetime, timedelta, time as datetime_time
from kiteconnect import KiteConnect
import redis

# Connect to the Redis instance using your environment variable
redis_client = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))

# ----------------- CONFIGURATION PATHS -----------------
CONFIG_PATH = "/trading_data/config.json"
TOKEN_PATH = "/trading_data/token.txt"
STATE_PATH = "/trading_data/state.json"
LEDGER_PATH = "/trading_data/mock_ledger.json"

# Default configuration to fall back on or initialize
DEFAULT_CONFIG = {
    "LIVE_TRADING": False,
    "EOD_CUTOFF_TIME": "15:15",
    "WATCHED_SCRIPS": ["RELIANCE", "INFY", "SBIN", "TCS", "TATAMOTORS", "HDFCBANK", "ICICIBANK", "BHARTIARTL", "LT", "ITC"],
    "QUANTITY": 1
}

# ----------------- UTILITIES -----------------

def round_to_tick(price, tick_size=0.05):
    """
    Rounds a given price to the nearest tick size (default 0.05).
    """
    return round(round(price / tick_size) * tick_size, 2)

def load_json_file(path, default_val):
    """
    Loads a JSON file with exception handling, returning a default value if missing or invalid.
    """
    if not os.path.exists(path):
        return default_val
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Error reading JSON from {path}: {str(e)}. Using fallback value.")
        return default_val

def save_json_file(path, data):
    """
    Saves data to a JSON file, ensuring the parent directories exist.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"❌ Error writing JSON to {path}: {str(e)}")

def append_ledger_transaction(scrip, action, price, quantity, order_id="N/A", gtt_id="N/A", message=""):
    """
    Logs transaction events to mock_ledger.json when operating in simulation mode.
    """
    ledger = load_json_file(LEDGER_PATH, [])
    transaction = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scrip": scrip,
        "action": action,
        "price": round(price, 2),
        "quantity": quantity,
        "order_id": order_id,
        "gtt_id": gtt_id,
        "message": message
    }
    ledger.append(transaction)
    save_json_file(LEDGER_PATH, ledger)
    print(f"📝 Mock Ledger entry added: {transaction}")

# ----------------- STRATEGY PLACEHOLDER -----------------

def calculate_lorentzian_signal(scrip, ltp, config):
    """
    Placeholder function for the Lorentzian ML Strategy.
    In production, this would retrieve historical candle data and run the KNN model.
    
    For live development, manual test signals can be forced by placing them inside 
    config.json under the key 'TEST_SIGNALS' (e.g. "TEST_SIGNALS": {"INFY": "BUY"}).
    Otherwise, default to 'HOLD'.
    """
    test_signals = config.get("TEST_SIGNALS", {})
    
    # Check if signal is defined for the short symbol (e.g., "INFY") or full symbol (e.g., "NSE:INFY")
    scrip_upper = scrip.upper()
    short_symbol = scrip_upper.split(":")[-1] if ":" in scrip_upper else scrip_upper
    
    if scrip_upper in test_signals:
        signal = test_signals[scrip_upper].upper()
        print(f"⚡ [FORCED SIGNAL] Detected test signal {signal} for {scrip_upper}")
        return signal
    elif short_symbol in test_signals:
        signal = test_signals[short_symbol].upper()
        print(f"⚡ [FORCED SIGNAL] Detected test signal {signal} for {short_symbol}")
        return signal
        
    return "HOLD"

# ----------------- CORE TRADING ENGINE class -----------------

class TradingEngine:
    def __init__(self):
        self.kite = None
        self.api_key = os.environ.get("KITE_API_KEY")
        self.access_token = ""
        self.state = {}
        self.config = {}

    def initialize_session(self):
        """
        Initializes the KiteConnect session using credentials from the environment 
        and syncs tokens dynamically via the Redis shared memory layer.
        """
        import redis
        print("🔧 Initializing trading session via Redis wrapper...")
        
        if not self.api_key:
            print("❌ Aborted: Environment variable KITE_API_KEY is not defined.")
            sys.exit(1)

        # 1. Connect to your secure, shared Redis container instance
        try:
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
            redis_client = redis.Redis.from_url(redis_url)
        except Exception as e:
            print(f"❌ Aborted: Failed to connect to Redis instance: {str(e)}")
            sys.exit(1)

        # 2. Check Redis for a valid access token first
        token_bytes = redis_client.get("zerodha_access_token")
        self.access_token = token_bytes.decode("utf-8") if token_bytes else ""

        # 3. Fallback: If Redis is completely blank, fire the inline browser login automation
        if not self.access_token:
            print("🎫 Redis cache is empty. Triggering inline login handshake container...")
            try:
                from playwright.sync_api import sync_playwright
                import pyotp
                from urllib.parse import urlparse, parse_qs

                user_id = os.environ.get("ZERODHA_USER_ID")
                password = os.environ.get("ZERODHA_PASSWORD")
                totp_secret = os.environ.get("TOTP_SECRET")

                self.kite = KiteConnect(api_key=self.api_key)
                login_url = self.kite.login_url()

                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    context = browser.new_context()
                    page = context.new_page()
                    page.goto(login_url)

                    page.wait_for_selector('input[id="userid"]', timeout=15000)
                    page.fill('input[id="userid"]', user_id)
                    page.fill('input[id="password"]', password)
                    page.click('button[type="submit"]')

                    page.wait_for_selector('input[type="number"]', timeout=15000)
                    totp = pyotp.TOTP(totp_secret)
                    token = totp.now()

                    totp_input = page.locator('input[type="number"]')
                    totp_input.fill(token)
                    totp_input.press("Enter")

                    page.wait_for_url(lambda url: "request_token=" in url, timeout=30000)
                    query_params = parse_qs(urlparse(page.url).query)
                    request_token = query_params.get("request_token", [None])[0]
                    browser.close()

                if request_token:
                    print("🔑 Generating fresh access token session...")
                    session = self.kite.generate_session(request_token, api_secret=os.environ.get("KITE_API_SECRET"))
                    self.access_token = session.get("access_token")
                    
                    # Store it inside Redis so the web app container can instantly read it too!
                    redis_client.set("zerodha_access_token", self.access_token)
                    print("✅ Fresh access token written securely into Redis database.")
            except Exception as login_err:
                print(f"❌ Inline login automation failed: {str(login_err)}")
                sys.exit(1)

        # 4. Bind the active token to your functional trading client
        try:
            self.kite = KiteConnect(api_key=self.api_key)
            self.kite.set_access_token(self.access_token)
            print("🔑 KiteConnect connection fully bound to active token session.")
        except Exception as e:
            print(f"❌ Failed to bind authenticated connection client: {str(e)}")
            sys.exit(1)

        # Initialize config backup parameters
        if not os.path.exists(CONFIG_PATH):
            save_json_file(CONFIG_PATH, DEFAULT_CONFIG)
            self.config = DEFAULT_CONFIG.copy()
        else:
            self.config = load_json_file(CONFIG_PATH, DEFAULT_CONFIG)

        self.state = load_json_file(STATE_PATH, {})
        print(f"💾 Active state tracker loaded with {len(self.state)} entries.")

    def get_scrip_keys(self, scrip):
        """
        Returns the parsed exchange and tradingsymbol for order execution, and
        the base tracking key for state lookup (removes exchange prefix).
        """
        scrip_upper = scrip.upper()
        if ":" in scrip_upper:
            exchange, symbol = scrip_upper.split(":", 1)
        else:
            exchange = "NSE"
            symbol = scrip_upper
        return exchange, symbol, symbol

    def check_and_initialize_scrip_state(self, scrip_key):
        """
        Ensures a scrip key is present in the tracking state dictionary.
        """
        if scrip_key not in self.state:
            self.state[scrip_key] = {
                "status": "IDLE",
                "order_id": "N/A",
                "gtt_id": "N/A",
                "entry_price": 0.0,
                "stop_loss": 0.0,
                "highest_step": 0,
                "history_open": [],
                "history_high": [],
                "history_low": [],
                "history_close": [],
                "history_timestamps": [],
                "LTP": 0.0,
                "State": "IDLE"
            }

    def set_scrip_state(self, scrip_key, new_values):
        """
        Updates the state for a scrip while preserving history fields.
        """
        if scrip_key not in self.state:
            self.check_and_initialize_scrip_state(scrip_key)
        
        # Keep history and LTP keys
        preserved_keys = ["history_open", "history_high", "history_low", "history_close", "history_timestamps", "LTP", "State"]
        preserved = {k: self.state[scrip_key][k] for k in preserved_keys if k in self.state[scrip_key]}
        
        # Set new values
        self.state[scrip_key] = new_values
        
        # Restore preserved keys
        self.state[scrip_key].update(preserved)
        # Ensure State is kept synced with status
        if "status" in new_values:
            self.state[scrip_key]["State"] = new_values["status"]

    def place_order_wrapper(self, exchange, symbol, tx_type, quantity, limit_price, live_trading):
        """
        Helper function to execute a live limit order or simulate it.
        """
        if live_trading:
            # Place live order on Zerodha
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=symbol,
                transaction_type=tx_type,
                quantity=quantity,
                product=self.kite.PRODUCT_MIS,
                order_type=self.kite.ORDER_TYPE_LIMIT,
                price=limit_price,
                validity=self.kite.VALIDITY_DAY
            )
            return str(order_id), limit_price
        else:
            # Simulate order
            order_id = f"MOCK-ORD-{random.randint(100000, 999999)}"
            return order_id, limit_price

    def place_gtt_wrapper(self, exchange, symbol, trigger_price, limit_price, tx_type, quantity, ltp, live_trading):
        """
        Helper function to deploy a live single-leg GTT order or simulate it.
        """
        if live_trading:
            orders_payload = [
                {
                    "exchange": exchange,
                    "tradingsymbol": symbol,
                    "transaction_type": tx_type,
                    "quantity": quantity,
                    "order_type": self.kite.ORDER_TYPE_LIMIT,
                    "product": self.kite.PRODUCT_MIS,
                    "price": limit_price
                }
            ]
            gtt_response = self.kite.place_gtt(
                trigger_type=self.kite.GTT_TYPE_SINGLE,
                tradingsymbol=symbol,
                exchange=exchange,
                trigger_values=[trigger_price],
                last_price=round_to_tick(ltp),
                orders=orders_payload
            )
            gtt_id = gtt_response.get("trigger_id") if isinstance(gtt_response, dict) else gtt_response
            return str(gtt_id)
        else:
            # Simulate GTT
            return f"MOCK-GTT-{random.randint(100000, 999999)}"

    def delete_gtt_wrapper(self, gtt_id, live_trading):
        """
        Helper function to delete an active GTT order or simulate the deletion.
        """
        if live_trading:
            try:
                self.kite.delete_gtt(int(gtt_id))
                print(f"🗑️ Live GTT {gtt_id} deleted successfully.")
            except Exception as e:
                print(f"⚠️ Failed to delete live GTT {gtt_id}: {str(e)}")
        else:
            print(f"🗑️ [SIMULATED] GTT {gtt_id} deleted.")

    def run_eod_square_off(self, live_trading):
        """
        Mass market square-off routine executing immediately upon EOD cutoff.
        """
        print("⏰ [EOD CUTOFF] Cutoff time reached. Starting mass square-off routine...")
        
        # Check active positions
        active_found = False
        watched_scrips = self.config.get("WATCHED_SCRIPS", [])
        
        for scrip_key, pos in list(self.state.items()):
            status = pos.get("status", "IDLE")
            if status in ("LONG_TRIGGERED", "SHORT_TRIGGERED"):
                active_found = True
                print(f"⚠️ Open position found for {scrip_key} (State: {status}). Executing exit...")
                
                # Dynamically retrieve correct exchange prefix from watched_scrips
                exchange = "NSE"
                for ws in watched_scrips:
                    ws_upper = ws.upper()
                    if ws_upper.endswith(scrip_key) or scrip_key in ws_upper:
                        if ":" in ws_upper:
                            exchange = ws_upper.split(":")[0]
                        break
                
                # Fetch latest price
                ltp = 0.0
                inst_str = f"{exchange}:{scrip_key}"
                try:
                    ltp_res = self.kite.ltp([inst_str])
                    if inst_str in ltp_res:
                        ltp = ltp_res[inst_str]["last_price"]
                except Exception as e:
                    print(f"⚠️ Could not fetch LTP for {inst_str} during EOD square-off: {str(e)}")
                    # Use last known entry price as fallback
                    ltp = pos.get("entry_price", 0.0)

                # Step 1: Exit order placement
                try:
                    symbol = scrip_key
                    quantity = self.config.get("QUANTITY", 1)
                    
                    if status == "LONG_TRIGGERED":
                        exit_tx = self.kite.TRANSACTION_TYPE_SELL if live_trading else "SELL"
                        limit_price = round_to_tick(ltp * 0.995)  # 0.5% lower to ensure fill
                        action_label = "EOD_EXIT_LONG"
                    else:
                        exit_tx = self.kite.TRANSACTION_TYPE_BUY if live_trading else "BUY"
                        limit_price = round_to_tick(ltp * 1.005)  # 0.5% higher to ensure fill
                        action_label = "EOD_EXIT_SHORT"

                    order_id, exec_price = self.place_order_wrapper(
                        exchange=exchange,
                        symbol=symbol,
                        tx_type=exit_tx,
                        quantity=quantity,
                        limit_price=limit_price,
                        live_trading=live_trading
                    )
                    print(f"⚡ EOD Square-Off order {order_id} placed at ₹{exec_price:.2f} for {inst_str}")
                    
                    # Delete GTT Stop Loss
                    gtt_id = pos.get("gtt_id", "N/A")
                    if gtt_id and gtt_id != "N/A":
                        self.delete_gtt_wrapper(gtt_id, live_trading)
                    
                    if not live_trading:
                        append_ledger_transaction(
                            scrip=scrip_key,
                            action=action_label,
                            price=exec_price,
                            quantity=quantity,
                            order_id=order_id,
                            gtt_id=gtt_id,
                            message=f"Simulated EOD auto-exit completed."
                        )
                except Exception as e:
                    print(f"🚨 Fail-safe: EOD exit order failed for {inst_str}: {str(e)}")

                # Reset scrip state tracker to IDLE
                self.set_scrip_state(scrip_key, {
                    "status": "IDLE",
                    "order_id": "N/A",
                    "gtt_id": "N/A",
                    "entry_price": 0.0,
                    "stop_loss": 0.0,
                    "highest_step": 0
                })
                
        if active_found:
            print("🧹 EOD mass square-off routine completed.")
        else:
            print("✓ No active positions to square off.")

    def execute_forced_trade(self, scrip, signal):
        """
        Executes a forced trade (BUY/SELL) for a specific scrip from the UI.
        """
        exchange, symbol, scrip_key = self.get_scrip_keys(scrip)
        inst_str = f"{exchange}:{symbol}"
        
        # 1. Fetch current price
        ltp = 0.0
        live_trading = self.config.get("LIVE_TRADING", False)
        try:
            ltp_res = self.kite.ltp([inst_str])
            if inst_str in ltp_res:
                ltp = ltp_res[inst_str]["last_price"]
        except Exception as e:
            print(f"⚠️ Could not fetch LTP for {inst_str} during forced trade: {str(e)}")
            
        if ltp <= 0.0:
            # Revert to a fallback or mock price if live fetch failed and simulation mode
            if not live_trading:
                prev_state = self.state.get(scrip_key, {})
                ltp = prev_state.get("entry_price", 1500.0)
                if ltp <= 0.0:
                    ltp = 1500.0
            else:
                print(f"🚨 Aborting forced trade for {inst_str}: price fetch failed in Live mode.")
                return

        # 2. Get current state of the scrip
        self.check_and_initialize_scrip_state(scrip_key)
        pos = self.state[scrip_key]
        current_status = pos.get("status", "IDLE")
        gtt_id = pos.get("gtt_id", "N/A")
        quantity = self.config.get("QUANTITY", 1)

        # 3. Process BUY/SELL transition logic
        if signal == "BUY":
            if current_status == "IDLE":
                try:
                    print(f"🚀 Firing Forced Long Entry for {scrip_key} at ₹{ltp:.2f}...")
                    limit_price = round_to_tick(ltp * 1.005)
                    tx_type = self.kite.TRANSACTION_TYPE_BUY if live_trading else "BUY"
                    
                    order_id, exec_price = self.place_order_wrapper(
                        exchange=exchange, symbol=symbol, tx_type=tx_type,
                        quantity=quantity, limit_price=limit_price, live_trading=live_trading
                    )
                    
                    stop_loss_trigger = round_to_tick(exec_price * 0.985)
                    stop_loss_limit = round_to_tick(stop_loss_trigger * 0.995)
                    gtt_tx_type = self.kite.TRANSACTION_TYPE_SELL if live_trading else "SELL"
                    
                    new_gtt_id = self.place_gtt_wrapper(
                        exchange=exchange, symbol=symbol, trigger_price=stop_loss_trigger,
                        limit_price=stop_loss_limit, tx_type=gtt_tx_type, quantity=quantity,
                        ltp=ltp, live_trading=live_trading
                    )
                    
                    self.set_scrip_state(scrip_key, {
                        "status": "LONG_TRIGGERED",
                        "order_id": order_id,
                        "gtt_id": new_gtt_id,
                        "entry_price": exec_price,
                        "stop_loss": stop_loss_trigger,
                        "highest_step": 0
                    })
                    
                    if not live_trading:
                        append_ledger_transaction(
                            scrip=scrip_key, action="BUY_ENTRY", price=exec_price,
                            quantity=quantity, order_id=order_id, gtt_id=new_gtt_id,
                            message=f"Forced Long Entry simulated at ₹{exec_price:.2f}. Stop-Loss set at ₹{stop_loss_trigger:.2f}."
                        )
                except Exception as e:
                    print(f"🚨 Order failure (FORCED LONG ENTRY) for {scrip_key}: {str(e)}")

            elif current_status == "SHORT_TRIGGERED":
                # Reversal: exit SHORT and enter LONG
                exit_success = False
                try:
                    print(f"🔄 [FORCED SAR REVERSAL] Exiting Short position for {scrip_key} at ₹{ltp:.2f}...")
                    limit_price_exit = round_to_tick(ltp * 1.005)
                    exit_tx = self.kite.TRANSACTION_TYPE_BUY if live_trading else "BUY"
                    
                    order_id, exec_price = self.place_order_wrapper(
                        exchange=exchange, symbol=symbol, tx_type=exit_tx,
                        quantity=quantity, limit_price=limit_price_exit, live_trading=live_trading
                    )
                    exit_success = True
                    
                    if gtt_id and gtt_id != "N/A":
                        self.delete_gtt_wrapper(gtt_id, live_trading)
                        
                    if not live_trading:
                        append_ledger_transaction(
                            scrip=scrip_key, action="EXIT_SHORT_SAR", price=exec_price,
                            quantity=quantity, order_id=order_id, gtt_id=gtt_id,
                            message="Short position closed in forced SAR reversal."
                        )
                except Exception as exit_err:
                    print(f"🚨 Fail-safe: Forced Exit Short failed for {scrip_key}: {str(exit_err)}")
                
                if exit_success:
                    time.sleep(1.5)
                    try:
                        print(f"🚀 [FORCED SAR REVERSAL] Firing Long Entry for {scrip_key} at ₹{ltp:.2f}...")
                        limit_price_entry = round_to_tick(ltp * 1.005)
                        entry_tx = self.kite.TRANSACTION_TYPE_BUY if live_trading else "BUY"
                        
                        new_order_id, new_exec_price = self.place_order_wrapper(
                            exchange=exchange, symbol=symbol, tx_type=entry_tx,
                            quantity=quantity, limit_price=limit_price_entry, live_trading=live_trading
                        )
                        
                        stop_loss_trigger = round_to_tick(new_exec_price * 0.985)
                        stop_loss_limit = round_to_tick(stop_loss_trigger * 0.995)
                        gtt_tx_type = self.kite.TRANSACTION_TYPE_SELL if live_trading else "SELL"
                        
                        new_gtt_id = self.place_gtt_wrapper(
                            exchange=exchange, symbol=symbol, trigger_price=stop_loss_trigger,
                            limit_price=stop_loss_limit, tx_type=gtt_tx_type, quantity=quantity,
                            ltp=ltp, live_trading=live_trading
                        )
                        
                        self.set_scrip_state(scrip_key, {
                            "status": "LONG_TRIGGERED",
                            "order_id": new_order_id,
                            "gtt_id": new_gtt_id,
                            "entry_price": new_exec_price,
                            "stop_loss": stop_loss_trigger,
                            "highest_step": 0
                        })
                        
                        if not live_trading:
                            append_ledger_transaction(
                                scrip=scrip_key, action="BUY_ENTRY_SAR", price=new_exec_price,
                                quantity=quantity, order_id=new_order_id, gtt_id=new_gtt_id,
                                message=f"Forced Long reversal entry simulated. Stop-Loss set at ₹{stop_loss_trigger:.2f}."
                            )
                    except Exception as entry_err:
                        print(f"🚨 Fail-safe: Forced Reverse Entry (LONG) failed for {scrip_key}: {str(entry_err)}")

        elif signal == "SELL":
            if current_status == "IDLE":
                try:
                    print(f"🚀 Firing Forced Short Entry for {scrip_key} at ₹{ltp:.2f}...")
                    limit_price = round_to_tick(ltp * 0.995)
                    tx_type = self.kite.TRANSACTION_TYPE_SELL if live_trading else "SELL"
                    
                    order_id, exec_price = self.place_order_wrapper(
                        exchange=exchange, symbol=symbol, tx_type=tx_type,
                        quantity=quantity, limit_price=limit_price, live_trading=live_trading
                    )
                    
                    stop_loss_trigger = round_to_tick(exec_price * 1.015)
                    stop_loss_limit = round_to_tick(stop_loss_trigger * 1.005)
                    gtt_tx_type = self.kite.TRANSACTION_TYPE_BUY if live_trading else "BUY"
                    
                    new_gtt_id = self.place_gtt_wrapper(
                        exchange=exchange, symbol=symbol, trigger_price=stop_loss_trigger,
                        limit_price=stop_loss_limit, tx_type=gtt_tx_type, quantity=quantity,
                        ltp=ltp, live_trading=live_trading
                    )
                    
                    self.set_scrip_state(scrip_key, {
                        "status": "SHORT_TRIGGERED",
                        "order_id": order_id,
                        "gtt_id": new_gtt_id,
                        "entry_price": exec_price,
                        "stop_loss": stop_loss_trigger,
                        "highest_step": 0
                    })
                    
                    if not live_trading:
                        append_ledger_transaction(
                            scrip=scrip_key, action="SELL_ENTRY", price=exec_price,
                            quantity=quantity, order_id=order_id, gtt_id=new_gtt_id,
                            message=f"Forced Short Entry simulated at ₹{exec_price:.2f}. Stop-Loss set at ₹{stop_loss_trigger:.2f}."
                        )
                except Exception as e:
                    print(f"🚨 Order failure (FORCED SHORT ENTRY) for {scrip_key}: {str(e)}")

            elif current_status == "LONG_TRIGGERED":
                # Reversal: exit LONG and enter SHORT
                exit_success = False
                try:
                    print(f"🔄 [FORCED SAR REVERSAL] Exiting Long position for {scrip_key} at ₹{ltp:.2f}...")
                    limit_price_exit = round_to_tick(ltp * 0.995)
                    exit_tx = self.kite.TRANSACTION_TYPE_SELL if live_trading else "SELL"
                    
                    order_id, exec_price = self.place_order_wrapper(
                        exchange=exchange, symbol=symbol, tx_type=exit_tx,
                        quantity=quantity, limit_price=limit_price_exit, live_trading=live_trading
                    )
                    exit_success = True
                    
                    if gtt_id and gtt_id != "N/A":
                        self.delete_gtt_wrapper(gtt_id, live_trading)
                        
                    if not live_trading:
                        append_ledger_transaction(
                            scrip=scrip_key, action="EXIT_LONG_SAR", price=exec_price,
                            quantity=quantity, order_id=order_id, gtt_id=gtt_id,
                            message="Long position closed in forced SAR reversal."
                        )
                except Exception as exit_err:
                    print(f"🚨 Fail-safe: Forced Exit Long failed for {scrip_key}: {str(exit_err)}")
                
                if exit_success:
                    time.sleep(1.5)
                    try:
                        print(f"🚀 [FORCED SAR REVERSAL] Firing Short Entry for {scrip_key} at ₹{ltp:.2f}...")
                        limit_price_entry = round_to_tick(ltp * 0.995)
                        entry_tx = self.kite.TRANSACTION_TYPE_SELL if live_trading else "SELL"
                        
                        new_order_id, new_exec_price = self.place_order_wrapper(
                            exchange=exchange, symbol=symbol, tx_type=entry_tx,
                            quantity=quantity, limit_price=limit_price_entry, live_trading=live_trading
                        )
                        
                        stop_loss_trigger = round_to_tick(new_exec_price * 1.015)
                        stop_loss_limit = round_to_tick(stop_loss_trigger * 1.005)
                        gtt_tx_type = self.kite.TRANSACTION_TYPE_BUY if live_trading else "BUY"
                        
                        new_gtt_id = self.place_gtt_wrapper(
                            exchange=exchange, symbol=symbol, trigger_price=stop_loss_trigger,
                            limit_price=stop_loss_limit, tx_type=gtt_tx_type, quantity=quantity,
                            ltp=ltp, live_trading=live_trading
                        )
                        
                        self.set_scrip_state(scrip_key, {
                            "status": "SHORT_TRIGGERED",
                            "order_id": new_order_id,
                            "gtt_id": new_gtt_id,
                            "entry_price": new_exec_price,
                            "stop_loss": stop_loss_trigger,
                            "highest_step": 0
                        })
                        
                        if not live_trading:
                            append_ledger_transaction(
                                scrip=scrip_key, action="SELL_ENTRY_SAR", price=new_exec_price,
                                quantity=quantity, order_id=new_order_id, gtt_id=new_gtt_id,
                                message=f"Forced Short reversal entry simulated. Stop-Loss set at ₹{stop_loss_trigger:.2f}."
                            )
                    except Exception as entry_err:
                        print(f"🚨 Fail-safe: Forced Reverse Entry (SHORT) failed for {scrip_key}: {str(entry_err)}")

    def check_and_execute_ui_test_orders(self):
        """
        Checks the shared Redis queue for manual test orders pushed from the Streamlit UI,
        dynamically re-verifies runtime configuration parameters, and routes orders live or mock.
        """
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        redis_client = redis.Redis.from_url(redis_url)

        # 1. Fetch active live trading flag state directly from Redis storage mapping
        config_bytes = redis_client.get("zerodha_trading_config") # Ensure this matches your UI config storage key
        is_live = False
        qty = 1

        if config_bytes:
            try:
                live_config = json.loads(config_bytes.decode("utf-8"))
                is_live = live_config.get("LIVE_TRADING", False)
                qty = live_config.get("QUANTITY", 1)
                # Sync local object attributes
                self.config = live_config
            except Exception:
                is_live = self.config.get("LIVE_TRADING", False) if hasattr(self, 'config') else False
                qty = self.config.get("QUANTITY", 1) if hasattr(self, 'config') else 1
        else:
            is_live = self.config.get("LIVE_TRADING", False) if hasattr(self, 'config') else False
            qty = self.config.get("QUANTITY", 1) if hasattr(self, 'config') else 1

        # 2. Check for pending test transaction signals
        test_signal_bytes = redis_client.get("zerodha_test_signals")
        
        if test_signal_bytes:
            try:
                test_signals = json.loads(test_signal_bytes.decode("utf-8"))
                
                for scrip, action in test_signals.items():
                    print(f"\n⚡ [FORCED UI SIGNAL DETECTED] Processing manual test {action} order for {scrip}...")
                    
                    if not is_live:
                        print(f"🎲 Simulation Mode Active: Writing mock entry packet to local ledger mapping...")
                        self.execute_forced_trade(scrip, action)
                        print(f"📝 Mock Ledger entry appended successfully for {qty} shares of {scrip}.")
                    else:
                        print(f"🚨 CRITICAL WARNING: Live Trading Active! Dispatching transaction straight to Zerodha API...")
                        self.execute_forced_trade(scrip, action)
                        print(f"✅ Real order request dispatched successfully to Zerodha book for {qty} share(s) of {scrip}!")
                
                # Sync updated state to Redis
                save_json_file(STATE_PATH, self.state)
                try:
                    redis_client.set("zerodha_trading_state", json.dumps(self.state))
                    print("💾 Syncing active state machine variables to Redis memory tier (Forced Trade).")
                except Exception as e:
                    print(f"⚠️ Failed to sync state to Redis (Forced Trade): {str(e)}")
                        
            except Exception as e:
                print(f"❌ Failed to execute forced UI test order routine: {str(e)}")
            finally:
                # Clear the gate key cleanly from Redis
                redis_client.delete("zerodha_test_signals")
                print("🗑️ Test signal queue cleared cleanly from Redis storage tier.\n")

    def process_market_iteration(self):
        """
        Executes a single workflow iteration: loads settings, checks cutoff gates,
        processes signals, manages positions, and outputs state.
        """
        # Load configuration dynamically from Redis, fallback to local config.json
        try:
            config_bytes = redis_client.get("zerodha_trading_config")
            if config_bytes:
                self.config = json.loads(config_bytes.decode("utf-8"))
            else:
                self.config = load_json_file(CONFIG_PATH, DEFAULT_CONFIG)
        except Exception:
            self.config = load_json_file(CONFIG_PATH, DEFAULT_CONFIG)
        
        live_trading = self.config.get("LIVE_TRADING", False)
        eod_cutoff_str = self.config.get("EOD_CUTOFF_TIME", "15:15")
        watched_scrips = self.config.get("WATCHED_SCRIPS", [])
        quantity = self.config.get("QUANTITY", 1)

        print(f"\n⏰ [{datetime.now().strftime('%H:%M:%S')}] Iteration Tick: Live={live_trading}, EOD={eod_cutoff_str}, Scrips={watched_scrips}")

        # Check for forced test signals from Redis UI
        self.check_and_execute_ui_test_orders()

        # Check Timing Gate
        try:
            eod_hour, eod_minute = map(int, eod_cutoff_str.split(":"))
            cutoff_time = datetime_time(eod_hour, eod_minute)
        except Exception as te:
            print(f"⚠️ Invalid EOD_CUTOFF_TIME format '{eod_cutoff_str}'. Defaulting to 15:15.")
            cutoff_time = datetime_time(15, 15)

        current_time = datetime.now().time()
        is_eod = current_time >= cutoff_time

        # Ensure all watched scrips exist in state tracking dict
        for scrip in watched_scrips:
            _, _, scrip_key = self.get_scrip_keys(scrip)
            self.check_and_initialize_scrip_state(scrip_key)

        # 1. Handle EOD Square-Off Routine
        if is_eod:
            self.run_eod_square_off(live_trading)
            print("🔏 [EOD GATE] Execution gate closed. Blocking new entry signals.")
            save_json_file(STATE_PATH, self.state)
            try:
                redis_client.set("zerodha_trading_state", json.dumps(self.state))
                print("💾 Syncing active state machine variables to Redis memory tier (EOD).")
            except Exception as e:
                print(f"⚠️ Failed to sync state to Redis (EOD): {str(e)}")
            return

        # 2. Get Live Price Data via batch ltp()
        if not watched_scrips:
            print("✓ Watchlist is empty. No symbols to process.")
            save_json_file(STATE_PATH, self.state)
            try:
                redis_client.set("zerodha_trading_state", json.dumps(self.state))
                print("💾 Syncing active state machine variables to Redis memory tier (Empty watchlist).")
            except Exception as e:
                print(f"⚠️ Failed to sync state to Redis (Empty watchlist): {str(e)}")
            return

        query_list = []
        for scrip in watched_scrips:
            exchange, symbol, _ = self.get_scrip_keys(scrip)
            query_list.append(f"{exchange}:{symbol}")

        ltp_data = {}
        try:
            ltp_data = self.kite.ltp(query_list)
        except Exception as e:
            print(f"❌ Failed to fetch LTP from Zerodha API: {str(e)}")
            # If in mock mode, generate random walk prices so simulation can continue offline
            if not live_trading:
                print("🎲 Simulation Mode: Synthesizing mock market feed...")
                for full_symbol in query_list:
                    symbol = full_symbol.split(":")[-1]
                    prev_state = self.state.get(symbol, {})
                    prev_price = prev_state.get("entry_price", 1500.0)
                    if prev_price <= 0.0:
                        prev_price = 1500.0
                    mock_ltp = prev_price * (1.0 + random.uniform(-0.005, 0.005))
                    ltp_data[full_symbol] = {"last_price": round_to_tick(mock_ltp)}
            else:
                print("⚠️ Live mode: skipping price feed updates this iteration.")
                return

        # 3. Process Signals and State Machine transitions
        for scrip in watched_scrips:
            exchange, symbol, scrip_key = self.get_scrip_keys(scrip)
            query_symbol = f"{exchange}:{symbol}"
            
            if query_symbol not in ltp_data:
                print(f"⚠️ Scrip {query_symbol} price feed missing from response.")
                continue

            ltp = float(ltp_data[query_symbol]["last_price"])
            pos = self.state[scrip_key]
            
            # --- REAL-TIME CANDLESTICK HISTORY ACCUMULATION ---
            for key in ["history_open", "history_high", "history_low", "history_close", "history_timestamps"]:
                if key not in pos:
                    pos[key] = []

            instrument_token = ltp_data[query_symbol].get("instrument_token")
            fetched_history = False

            if live_trading and instrument_token:
                try:
                    to_date = datetime.now()
                    from_date = to_date - timedelta(days=5)
                    # Fetch true market candles from Zerodha Kite API
                    records = self.kite.historical_data(
                        instrument_token=instrument_token,
                        from_date=from_date.strftime("%Y-%m-%d %H:%M:%S"),
                        to_date=to_date.strftime("%Y-%m-%d %H:%M:%S"),
                        interval="15minute"
                    )
                    if records:
                        pos["history_open"] = [c["open"] for c in records]
                        pos["history_high"] = [c["high"] for c in records]
                        pos["history_low"] = [c["low"] for c in records]
                        pos["history_close"] = [c["close"] for c in records]
                        pos["history_timestamps"] = [
                            c["date"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(c["date"], datetime) else str(c["date"])
                            for c in records
                        ]
                        fetched_history = True
                        print(f"📈 [HISTORICAL DATA] Fetched {len(records)} true 15-minute candles for {scrip_key} (Token {instrument_token}).")
                except Exception as ex:
                    print(f"⚠️ [HISTORICAL DATA] Failed to fetch historical data for {scrip_key}: {str(ex)}")

            if not fetched_history:
                # Fallback: Accumulate raw price snapshot tick-by-tick
                open_val = pos["history_close"][-1] if pos["history_close"] else ltp
                close_val = ltp
                high_val = max(open_val, close_val)
                low_val = min(open_val, close_val)
                timestamp_val = datetime.now().strftime("%H:%M:%S")

                pos["history_open"].append(open_val)
                pos["history_high"].append(high_val)
                pos["history_low"].append(low_val)
                pos["history_close"].append(close_val)
                pos["history_timestamps"].append(timestamp_val)

                max_candles = 30
                if len(pos["history_open"]) > max_candles:
                    pos["history_open"] = pos["history_open"][-max_candles:]
                    pos["history_high"] = pos["history_high"][-max_candles:]
                    pos["history_low"] = pos["history_low"][-max_candles:]
                    pos["history_close"] = pos["history_close"][-max_candles:]
                    pos["history_timestamps"] = pos["history_timestamps"][-max_candles:]

            pos["LTP"] = ltp
            pos["State"] = pos.get("status", "IDLE")
            # --------------------------------------------------
            current_status = pos.get("status", "IDLE")
            entry_price = pos.get("entry_price", 0.0)
            gtt_id = pos.get("gtt_id", "N/A")
            stop_loss = pos.get("stop_loss", 0.0)
            highest_step = pos.get("highest_step", 0)

            # Generate indicator signal
            signal = calculate_lorentzian_signal(scrip_key, ltp, self.config)
            print(f"📊 {scrip_key}: LTP=₹{ltp:.2f}, State={current_status}, Signal={signal}")

            # Local Stop-Loss Breach Check
            local_sl_breach = False
            if current_status == "LONG_TRIGGERED" and stop_loss > 0.0 and ltp <= stop_loss:
                local_sl_breach = True
                print(f"🚨 LOCAL SL BREACH: Long SL at ₹{stop_loss:.2f} triggered for {scrip_key} at ₹{ltp:.2f}")
            elif current_status == "SHORT_TRIGGERED" and stop_loss > 0.0 and ltp >= stop_loss:
                local_sl_breach = True
                print(f"🚨 LOCAL SL BREACH: Short SL at ₹{stop_loss:.2f} triggered for {scrip_key} at ₹{ltp:.2f}")

            # Check GTT Status via API if Live
            live_gtt_breached = False
            if not local_sl_breach and live_trading and gtt_id and gtt_id != "N/A":
                try:
                    gtt_detail = self.kite.get_gtt(int(gtt_id))
                    if gtt_detail and gtt_detail.get("status") not in ("active", "allotted"):
                        live_gtt_breached = True
                        print(f"🚨 LIVE GTT TRIGGERED: GTT ID {gtt_id} state is '{gtt_detail.get('status')}'")
                except Exception as ex:
                    # Ignore API lookup failures to avoid flakiness
                    pass

            if local_sl_breach or live_gtt_breached:
                # Reset to IDLE
                print(f"💥 Resetting position for {scrip_key} to IDLE due to Stop-Loss trigger.")
                if not live_trading:
                    append_ledger_transaction(
                        scrip=scrip_key,
                        action="SL_BREACH",
                        price=ltp,
                        quantity=quantity,
                        order_id="N/A",
                        gtt_id=gtt_id,
                        message=f"Simulated Stop-Loss breached at ₹{ltp:.2f} (SL: ₹{stop_loss:.2f})"
                    )
                self.set_scrip_state(scrip_key, {
                    "status": "IDLE",
                    "order_id": "N/A",
                    "gtt_id": "N/A",
                    "entry_price": 0.0,
                    "stop_loss": 0.0,
                    "highest_step": 0
                })
                # Sync loop
                current_status = "IDLE"
                entry_price = 0.0
                gtt_id = "N/A"
                stop_loss = 0.0
                highest_step = 0

            # 4. Trailing stop-loss modification logic (Trailing GTT)
            if current_status in ("LONG_TRIGGERED", "SHORT_TRIGGERED") and gtt_id != "N/A":
                price_step_pct = 0.0025 # 0.25%
                trailing_dist_pct = 0.015 # 1.5%
                has_trailed = False
                new_trigger_price = stop_loss

                if current_status == "LONG_TRIGGERED" and ltp > entry_price:
                    steps = int((ltp - entry_price) / (entry_price * price_step_pct))
                    if steps > highest_step:
                        ref_price = entry_price * (1.0 + steps * price_step_pct)
                        new_trigger_price = round_to_tick(ref_price * (1.0 - trailing_dist_pct))
                        highest_step = steps
                        has_trailed = True

                elif current_status == "SHORT_TRIGGERED" and ltp < entry_price:
                    steps = int((entry_price - ltp) / (entry_price * price_step_pct))
                    if steps > highest_step:
                        ref_price = entry_price * (1.0 - steps * price_step_pct)
                        new_trigger_price = round_to_tick(ref_price * (1.0 + trailing_dist_pct))
                        highest_step = steps
                        has_trailed = True

                if has_trailed:
                    print(f"📈 Trailing SL for {scrip_key}: trigger moving from ₹{stop_loss:.2f} to ₹{new_trigger_price:.2f} (Step {highest_step})")
                    try:
                        # Determine modify params
                        if current_status == "LONG_TRIGGERED":
                            limit_price = round_to_tick(new_trigger_price * 0.995)
                            gtt_tx_type = self.kite.TRANSACTION_TYPE_SELL if live_trading else "SELL"
                        else:
                            limit_price = round_to_tick(new_trigger_price * 1.005)
                            gtt_tx_type = self.kite.TRANSACTION_TYPE_BUY if live_trading else "BUY"

                        if live_trading:
                            orders_payload = [{
                                "exchange": exchange,
                                "tradingsymbol": symbol,
                                "transaction_type": gtt_tx_type,
                                "quantity": quantity,
                                "order_type": self.kite.ORDER_TYPE_LIMIT,
                                "product": self.kite.PRODUCT_MIS,
                                "price": limit_price
                            }]
                            self.kite.modify_gtt(
                                trigger_id=int(gtt_id),
                                trigger_type=self.kite.GTT_TYPE_SINGLE,
                                tradingsymbol=symbol,
                                exchange=exchange,
                                trigger_values=[new_trigger_price],
                                last_price=round_to_tick(ltp),
                                orders=orders_payload
                            )
                        
                        self.state[scrip_key].update({
                            "stop_loss": new_trigger_price,
                            "highest_step": highest_step
                        })
                        stop_loss = new_trigger_price

                        if not live_trading:
                            append_ledger_transaction(
                                scrip=scrip_key,
                                action="TRAIL_SL_MODIFY",
                                price=new_trigger_price,
                                quantity=quantity,
                                order_id="N/A",
                                gtt_id=gtt_id,
                                message=f"Simulated trailing SL modified to ₹{new_trigger_price:.2f}"
                            )

                    except Exception as gtt_mod_err:
                        print(f"🚨 Fail-safe: GTT modification failed for {scrip_key}: {str(gtt_mod_err)}. Resetting to IDLE.")
                        self.set_scrip_state(scrip_key, {
                            "status": "IDLE",
                            "order_id": "N/A",
                            "gtt_id": "N/A",
                            "entry_price": 0.0,
                            "stop_loss": 0.0,
                            "highest_step": 0
                        })
                        continue

            # 5. Signal-Based Entry and Reversal State Machine
            if signal == "BUY":
                if current_status == "IDLE":
                    # Fire long entry
                    try:
                        print(f"🚀 Firing Long Entry for {scrip_key} at ₹{ltp:.2f}...")
                        limit_price = round_to_tick(ltp * 1.005) # Buy higher to guarantee immediate execution
                        tx_type = self.kite.TRANSACTION_TYPE_BUY if live_trading else "BUY"
                        
                        order_id, exec_price = self.place_order_wrapper(
                            exchange=exchange, symbol=symbol, tx_type=tx_type,
                            quantity=quantity, limit_price=limit_price, live_trading=live_trading
                        )
                        
                        # Place GTT Stop loss at 1.5% away
                        stop_loss_trigger = round_to_tick(exec_price * 0.985)
                        stop_loss_limit = round_to_tick(stop_loss_trigger * 0.995)
                        gtt_tx_type = self.kite.TRANSACTION_TYPE_SELL if live_trading else "SELL"
                        
                        new_gtt_id = self.place_gtt_wrapper(
                            exchange=exchange, symbol=symbol, trigger_price=stop_loss_trigger,
                            limit_price=stop_loss_limit, tx_type=gtt_tx_type, quantity=quantity,
                            ltp=ltp, live_trading=live_trading
                        )
                        
                        self.set_scrip_state(scrip_key, {
                            "status": "LONG_TRIGGERED",
                            "order_id": order_id,
                            "gtt_id": new_gtt_id,
                            "entry_price": exec_price,
                            "stop_loss": stop_loss_trigger,
                            "highest_step": 0
                        })
                        
                        if not live_trading:
                            append_ledger_transaction(
                                scrip=scrip_key, action="BUY_ENTRY", price=exec_price,
                                quantity=quantity, order_id=order_id, gtt_id=new_gtt_id,
                                message=f"Long Entry simulated at ₹{exec_price:.2f}. Stop-Loss set at ₹{stop_loss_trigger:.2f}."
                            )
                    except Exception as e:
                        print(f"🚨 Order failure (LONG ENTRY) for {scrip_key}: {str(e)}. Force resetting to IDLE.")
                        self.set_scrip_state(scrip_key, {
                            "status": "IDLE", "order_id": "N/A", "gtt_id": "N/A",
                            "entry_price": 0.0, "stop_loss": 0.0, "highest_step": 0
                        })

                elif current_status == "SHORT_TRIGGERED":
                    # Reversal: holding SHORT and BUY signal prints.
                    # STEP 1 (EXIT SHORT)
                    exit_success = False
                    try:
                        print(f"🔄 [SAR REVERSAL] Exiting Short position for {scrip_key} at ₹{ltp:.2f}...")
                        limit_price_exit = round_to_tick(ltp * 1.005) # Buy higher to guarantee immediate fill
                        exit_tx = self.kite.TRANSACTION_TYPE_BUY if live_trading else "BUY"
                        
                        order_id, exec_price = self.place_order_wrapper(
                            exchange=exchange, symbol=symbol, tx_type=exit_tx,
                            quantity=quantity, limit_price=limit_price_exit, live_trading=live_trading
                        )
                        exit_success = True
                        
                        # Delete corresponding GTT
                        if gtt_id and gtt_id != "N/A":
                            self.delete_gtt_wrapper(gtt_id, live_trading)
                            
                        if not live_trading:
                            append_ledger_transaction(
                                scrip=scrip_key, action="EXIT_SHORT_SAR", price=exec_price,
                                quantity=quantity, order_id=order_id, gtt_id=gtt_id,
                                message="Short position closed in SAR reversal."
                            )
                    except Exception as exit_err:
                        print(f"🚨 Fail-safe: Exit Short failed for {scrip_key}: {str(exit_err)}. Force resetting to IDLE.")
                        self.set_scrip_state(scrip_key, {
                            "status": "IDLE", "order_id": "N/A", "gtt_id": "N/A",
                            "entry_price": 0.0, "stop_loss": 0.0, "highest_step": 0
                        })
                    
                    # STEP 2 (MARGIN BUFFER)
                    if exit_success:
                        print("⏳ Pausing 1.5s for margin/ledger settlement...")
                        time.sleep(1.5)
                        
                        # STEP 3 (REVERSE ENTRY TO LONG)
                        try:
                            print(f"🚀 [SAR REVERSAL] Firing Long Entry for {scrip_key} at ₹{ltp:.2f}...")
                            limit_price_entry = round_to_tick(ltp * 1.005)
                            entry_tx = self.kite.TRANSACTION_TYPE_BUY if live_trading else "BUY"
                            
                            new_order_id, new_exec_price = self.place_order_wrapper(
                                exchange=exchange, symbol=symbol, tx_type=entry_tx,
                                quantity=quantity, limit_price=limit_price_entry, live_trading=live_trading
                            )
                            
                            # Place new protective GTT at 1.5% away
                            stop_loss_trigger = round_to_tick(new_exec_price * 0.985)
                            stop_loss_limit = round_to_tick(stop_loss_trigger * 0.995)
                            gtt_tx_type = self.kite.TRANSACTION_TYPE_SELL if live_trading else "SELL"
                            
                            new_gtt_id = self.place_gtt_wrapper(
                                exchange=exchange, symbol=symbol, trigger_price=stop_loss_trigger,
                                limit_price=stop_loss_limit, tx_type=gtt_tx_type, quantity=quantity,
                                ltp=ltp, live_trading=live_trading
                            )
                            
                            # Update dictionary states ('current_position_state' -> LONG_TRIGGERED, entry price, highest_step reset)
                            self.set_scrip_state(scrip_key, {
                                "status": "LONG_TRIGGERED",
                                "order_id": new_order_id,
                                "gtt_id": new_gtt_id,
                                "entry_price": new_exec_price,
                                "stop_loss": stop_loss_trigger,
                                "highest_step": 0
                            })
                            
                            if not live_trading:
                                append_ledger_transaction(
                                    scrip=scrip_key, action="BUY_ENTRY_SAR", price=new_exec_price,
                                    quantity=quantity, order_id=new_order_id, gtt_id=new_gtt_id,
                                    message=f"Long reversal entry simulated. Stop-Loss set at ₹{stop_loss_trigger:.2f}."
                                )
                        except Exception as entry_err:
                            print(f"🚨 Fail-safe: Reverse Entry (LONG) failed for {scrip_key}: {str(entry_err)}. Force resetting to IDLE.")
                            self.set_scrip_state(scrip_key, {
                                "status": "IDLE", "order_id": "N/A", "gtt_id": "N/A",
                                "entry_price": 0.0, "stop_loss": 0.0, "highest_step": 0
                            })

            elif signal == "SELL":
                if current_status == "IDLE":
                    # Fire short entry
                    try:
                        print(f"🚀 Firing Short Entry for {scrip_key} at ₹{ltp:.2f}...")
                        limit_price = round_to_tick(ltp * 0.995) # Sell lower to guarantee immediate fill
                        tx_type = self.kite.TRANSACTION_TYPE_SELL if live_trading else "SELL"
                        
                        order_id, exec_price = self.place_order_wrapper(
                            exchange=exchange, symbol=symbol, tx_type=tx_type,
                            quantity=quantity, limit_price=limit_price, live_trading=live_trading
                        )
                        
                        # Place GTT Stop loss at 1.5% away
                        stop_loss_trigger = round_to_tick(exec_price * 1.015)
                        stop_loss_limit = round_to_tick(stop_loss_trigger * 1.005)
                        gtt_tx_type = self.kite.TRANSACTION_TYPE_BUY if live_trading else "BUY"
                        
                        new_gtt_id = self.place_gtt_wrapper(
                            exchange=exchange, symbol=symbol, trigger_price=stop_loss_trigger,
                            limit_price=stop_loss_limit, tx_type=gtt_tx_type, quantity=quantity,
                            ltp=ltp, live_trading=live_trading
                        )
                        
                        self.set_scrip_state(scrip_key, {
                            "status": "SHORT_TRIGGERED",
                            "order_id": order_id,
                            "gtt_id": new_gtt_id,
                            "entry_price": exec_price,
                            "stop_loss": stop_loss_trigger,
                            "highest_step": 0
                        })
                        
                        if not live_trading:
                            append_ledger_transaction(
                                scrip=scrip_key, action="SELL_ENTRY", price=exec_price,
                                quantity=quantity, order_id=order_id, gtt_id=new_gtt_id,
                                message=f"Short Entry simulated at ₹{exec_price:.2f}. Stop-Loss set at ₹{stop_loss_trigger:.2f}."
                            )
                    except Exception as e:
                        print(f"🚨 Order failure (SHORT ENTRY) for {scrip_key}: {str(e)}. Force resetting to IDLE.")
                        self.set_scrip_state(scrip_key, {
                            "status": "IDLE", "order_id": "N/A", "gtt_id": "N/A",
                            "entry_price": 0.0, "stop_loss": 0.0, "highest_step": 0
                        })

                elif current_status == "LONG_TRIGGERED":
                    # Reversal: holding LONG and SELL signal prints.
                    # STEP 1 (EXIT LONG)
                    exit_success = False
                    try:
                        print(f"🔄 [SAR REVERSAL] Exiting Long position for {scrip_key} at ₹{ltp:.2f}...")
                        limit_price_exit = round_to_tick(ltp * 0.995) # Sell lower to guarantee immediate fill
                        exit_tx = self.kite.TRANSACTION_TYPE_SELL if live_trading else "SELL"
                        
                        order_id, exec_price = self.place_order_wrapper(
                            exchange=exchange, symbol=symbol, tx_type=exit_tx,
                            quantity=quantity, limit_price=limit_price_exit, live_trading=live_trading
                        )
                        exit_success = True
                        
                        # Delete corresponding GTT
                        if gtt_id and gtt_id != "N/A":
                            self.delete_gtt_wrapper(gtt_id, live_trading)
                            
                        if not live_trading:
                            append_ledger_transaction(
                                scrip=scrip_key, action="EXIT_LONG_SAR", price=exec_price,
                                quantity=quantity, order_id=order_id, gtt_id=gtt_id,
                                message="Long position closed in SAR reversal."
                            )
                    except Exception as exit_err:
                        print(f"🚨 Fail-safe: Exit Long failed for {scrip_key}: {str(exit_err)}. Force resetting to IDLE.")
                        self.set_scrip_state(scrip_key, {
                            "status": "IDLE", "order_id": "N/A", "gtt_id": "N/A",
                            "entry_price": 0.0, "stop_loss": 0.0, "highest_step": 0
                        })
                    
                    # STEP 2 (MARGIN BUFFER)
                    if exit_success:
                        print("⏳ Pausing 1.5s for margin/ledger settlement...")
                        time.sleep(1.5)
                        
                        # STEP 3 (REVERSE ENTRY TO SHORT)
                        try:
                            print(f"🚀 [SAR REVERSAL] Firing Short Entry for {scrip_key} at ₹{ltp:.2f}...")
                            limit_price_entry = round_to_tick(ltp * 0.995)
                            entry_tx = self.kite.TRANSACTION_TYPE_SELL if live_trading else "SELL"
                            
                            new_order_id, new_exec_price = self.place_order_wrapper(
                                exchange=exchange, symbol=symbol, tx_type=entry_tx,
                                quantity=quantity, limit_price=limit_price_entry, live_trading=live_trading
                            )
                            
                            # Place new protective GTT at 1.5% away
                            stop_loss_trigger = round_to_tick(new_exec_price * 1.015)
                            stop_loss_limit = round_to_tick(stop_loss_trigger * 1.005)
                            gtt_tx_type = self.kite.TRANSACTION_TYPE_BUY if live_trading else "BUY"
                            
                            new_gtt_id = self.place_gtt_wrapper(
                                exchange=exchange, symbol=symbol, trigger_price=stop_loss_trigger,
                                limit_price=stop_loss_limit, tx_type=gtt_tx_type, quantity=quantity,
                                ltp=ltp, live_trading=live_trading
                            )
                            
                            # Update dictionary states ('current_position_state' -> SHORT_TRIGGERED, entry price, highest_step reset)
                            self.set_scrip_state(scrip_key, {
                                "status": "SHORT_TRIGGERED",
                                "order_id": new_order_id,
                                "gtt_id": new_gtt_id,
                                "entry_price": new_exec_price,
                                "stop_loss": stop_loss_trigger,
                                "highest_step": 0
                            })
                            
                            if not live_trading:
                                append_ledger_transaction(
                                    scrip=scrip_key, action="SELL_ENTRY_SAR", price=new_exec_price,
                                    quantity=quantity, order_id=new_order_id, gtt_id=new_gtt_id,
                                    message=f"Short reversal entry simulated. Stop-Loss set at ₹{stop_loss_trigger:.2f}."
                                )
                        except Exception as entry_err:
                            print(f"🚨 Fail-safe: Reverse Entry (SHORT) failed for {scrip_key}: {str(entry_err)}. Force resetting to IDLE.")
                            self.set_scrip_state(scrip_key, {
                                "status": "IDLE", "order_id": "N/A", "gtt_id": "N/A",
                                "entry_price": 0.0, "stop_loss": 0.0, "highest_step": 0
                            })

        # 6. Save tracking persistence
        save_json_file(STATE_PATH, self.state)
        # Save to Redis as a text string so the web app can see it instantly
        import json
        try:
            redis_client.set("zerodha_trading_state", json.dumps(self.state))
            print("💾 Syncing active state machine variables to Redis memory tier.")
        except Exception as redis_err:
            print(f"❌ Failed to sync state to Redis: {str(redis_err)}")

    def start_loop(self):
        """
        Infinite worker loop sleeping for 30 seconds between iterations.
        """
        self.initialize_session()
        print("🟢 Continuous Trading Engine is running. Polling interval: 30 seconds.")
        
        while True:
            try:
                self.process_market_iteration()
            except Exception as e:
                print(f"🚨 Global Loop Exception: {str(e)}")
            
            # Wait for 30 seconds before next iteration
            time.sleep(30)

if __name__ == "__main__":
    engine = TradingEngine()
    engine.start_loop()
