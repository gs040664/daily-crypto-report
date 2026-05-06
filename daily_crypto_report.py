import os
import requests
import pandas as pd
import datetime
import time

# ==========================================
# 💎 Crypto TA Data Fetcher
# ==========================================

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT"]
INTERVAL = "4h"

DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def get_binance_klines(symbol, interval, limit=240):
    endpoints = [
        "https://api.binance.com/api/v3/klines",
        "https://data-api.binance.vision/api/v3/klines"
    ]
    res = None
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    for url in endpoints:
        try:
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            if isinstance(data, list):
                res = data
                break
        except:
            continue
            
    if res is None:
        raise Exception("API 無法存取")
        
    df = pd.DataFrame(res, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
    df['datetime'] = pd.to_datetime(df['time'], unit='ms')
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['volume'] = df['volume'].astype(float)
    return df

def calculate_indicators(df):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
    
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['Hist'] = df['MACD'] - df['Signal']
    return df

def save_data_to_csv(symbol, df):
    file_path = os.path.join(DATA_DIR, f"{symbol}_{INTERVAL}_history.csv")
    save_df = df[['datetime', 'time', 'open', 'high', 'low', 'close', 'volume', 'EMA20', 'EMA50', 'RSI', 'MACD', 'Hist']].copy()
    if os.path.exists(file_path):
        old_df = pd.read_csv(file_path)
        combined_df = pd.concat([old_df, save_df]).drop_duplicates(subset=['time'], keep='last')
        combined_df = combined_df.sort_values('time')
    else:
        combined_df = save_df
    combined_df.to_csv(file_path, index=False)

def get_binance_funding_rate(symbol):
    """獲取幣安合約即時資金費率 (Funding Rate)"""
    url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if isinstance(data, dict) and 'lastFundingRate' in data:
            return float(data['lastFundingRate'])
    except Exception as e:
        print(f"無法取得 {symbol} 資金費率: {e}")
    return None

def fetch_and_save_ta_data(symbol):
    """獲取純技術指標字串與 POC 等，並印出及存檔"""
    print(f"--- 處理 {symbol} ---")
    df = get_binance_klines(symbol, INTERVAL)
    df = calculate_indicators(df)
    save_data_to_csv(symbol, df)
    
    current = df.iloc[-1]
    price = current['close']
    rsi = current['RSI']
    macd_hist = current['Hist']
    ema20 = current['EMA20']
    ema50 = current['EMA50']
    vol_avg = df['volume'].rolling(20).mean().iloc[-1]
    vol_ratio = current['volume'] / vol_avg if vol_avg > 0 else 1
    
    # 計算交易密集區間 (Point of Control - 近期最大籌碼換手區)
    bins = pd.cut(df['close'], bins=20)
    vp = df.groupby(bins, observed=False)['volume'].sum()
    poc_bin = vp.idxmax()
    poc_price = poc_bin.mid
    
    # 新增資金費率
    funding_rate = get_binance_funding_rate(symbol)
    funding_str = f"{funding_rate * 100:.4f}%" if funding_rate is not None else "暫無資料"
    
    print(f"現價: {price:.4f}")
    print(f"EMA20: {ema20:.4f}, EMA50: {ema50:.4f}")
    print(f"RSI(14): {rsi:.1f}")
    print(f"MACD柱狀圖: {macd_hist:.4f}")
    print(f"當前量能對比均量: {vol_ratio:.2f}倍")
    print(f"交易密集區間 (POC): 約 {poc_price:.4f}")
    print(f"即時資金費率: {funding_str}")
    print(f"✓ {symbol} 歷史資料已儲存更新。\n")

if __name__ == "__main__":
    for sym in SYMBOLS:
        try:
            fetch_and_save_ta_data(sym)
            time.sleep(1)
        except Exception as e:
            print(f"[{sym}] 讀取失敗: {e}\n")
