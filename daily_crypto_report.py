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

def get_historical_funding_rates(symbol, start_time, end_time):
    url = f"https://fapi.binance.com/fapi/v1/fundingRate"
    params = {"symbol": symbol, "startTime": start_time, "endTime": end_time, "limit": 1000}
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if isinstance(data, list):
            df_fr = pd.DataFrame(data)
            if not df_fr.empty:
                df_fr['fundingTime'] = pd.to_numeric(df_fr['fundingTime'])
                df_fr['fundingRate'] = pd.to_numeric(df_fr['fundingRate'])
                df_fr = df_fr.sort_values('fundingTime')
                return df_fr[['fundingTime', 'fundingRate']]
    except Exception as e:
        print(f"無法取得 {symbol} 歷史資金費率: {e}")
    return pd.DataFrame()

def calculate_indicators(symbol, df):
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
    
    df['vol_avg_20'] = df['volume'].rolling(window=20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_avg_20']
    
    poc_series = []
    window = 240
    for i in range(len(df)):
        if i < 10:
            poc_series.append(df['close'].iloc[i])
            continue
        start_idx = max(0, i - window + 1)
        sub_df = df.iloc[start_idx:i+1]
        sub_bins = pd.cut(sub_df['close'], bins=20)
        vp = sub_df.groupby(sub_bins, observed=False)['volume'].sum()
        if not vp.empty:
            poc_series.append(vp.idxmax().mid)
        else:
            poc_series.append(df['close'].iloc[i])
    df['POC'] = poc_series
    
    start_time = int(df['time'].iloc[0])
    end_time = int(df['time'].iloc[-1])
    df_fr = get_historical_funding_rates(symbol, start_time, end_time)
    
    if not df_fr.empty:
        df = df.sort_values('time')
        df = pd.merge_asof(df, df_fr, left_on='time', right_on='fundingTime', direction='backward')
    else:
        df['fundingRate'] = None
        
    return df

def save_data_to_csv(symbol, df):
    file_path = os.path.join(DATA_DIR, f"{symbol}_{INTERVAL}_history.csv")
    cols = ['datetime', 'time', 'open', 'high', 'low', 'close', 'volume', 'EMA20', 'EMA50', 'RSI', 'MACD', 'Hist', 'vol_ratio', 'POC', 'fundingRate']
    cols = [c for c in cols if c in df.columns]
    save_df = df[cols].copy()
    
    if os.path.exists(file_path):
        old_df = pd.read_csv(file_path)
        combined_df = pd.concat([old_df, save_df]).drop_duplicates(subset=['time'], keep='last')
        combined_df = combined_df.sort_values('time')
    else:
        combined_df = save_df
    combined_df.to_csv(file_path, index=False)

def get_binance_funding_rate(symbol):
    url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if isinstance(data, dict) and 'lastFundingRate' in data:
            return float(data['lastFundingRate'])
    except:
        pass
    return None

def fetch_and_save_ta_data(symbol):
    print(f"--- 處理 {symbol} ---")
    df = get_binance_klines(symbol, INTERVAL, limit=240)
    df = calculate_indicators(symbol, df)
    save_data_to_csv(symbol, df)
    
    current = df.iloc[-1]
    print(f"現價: {current['close']:.4f}")
    print(f"EMA20: {current['EMA20']:.4f}, EMA50: {current['EMA50']:.4f}")
    print(f"RSI(14): {current['RSI']:.1f}")
    print(f"MACD柱狀圖: {current['Hist']:.4f}")
    print(f"當前量能對比均量: {current['vol_ratio']:.2f}倍")
    print(f"交易密集區間 (POC): 約 {current['POC']:.4f}")
    
    funding_val = current.get('fundingRate')
    if pd.isna(funding_val):
        funding_val = get_binance_funding_rate(symbol)
        
    if funding_val is not None:
        print(f"最新資金費率: {funding_val * 100:.4f}%")
    else:
        print("最新資金費率: 暫無資料")
    print(f"✓ {symbol} 歷史資料已儲存更新。\n")

def upload_to_gdrive_proxy(file_path, webapp_url, token):
    """透過 Google Apps Script 網頁程式上傳檔案 (繞過 0GB 配額限制)"""
    try:
        file_name = os.path.basename(file_path)
        with open(file_path, 'rb') as f:
            content = f.read()
        
        # 將檔案內容透過 POST 傳送給 Google Apps Script
        upload_url = f"{webapp_url}?filename={file_name}&token={token}"
        response = requests.post(upload_url, data=content, timeout=30)
        
        if response.text == "Success":
            print(f"✓ 已成功透過 Proxy 上傳至 Google Drive: {file_name}")
        else:
            print(f"上傳失敗，Google 腳本回傳: {response.text}")
    except Exception as e:
        print(f"Proxy 上傳過程發生錯誤: {e}")

if __name__ == "__main__":
    for sym in SYMBOLS:
        try:
            fetch_and_save_ta_data(sym)
            time.sleep(1)
        except Exception as e:
            print(f"[{sym}] 讀取失敗: {e}\n")

    # 檢查是否有設定 GAS 中轉站網址與密鑰
    webapp_url = os.environ.get("GDRIVE_WEBAPP_URL")
    webapp_token = os.environ.get("GDRIVE_WEBAPP_TOKEN")
    
    if webapp_url and webapp_token:
        print("\n--- 開始透過 Proxy 上傳至 Google Drive ---")
        for sym in SYMBOLS:
            file_path = os.path.join(DATA_DIR, f"{sym}_{INTERVAL}_history.csv")
            if os.path.exists(file_path):
                upload_to_gdrive_proxy(file_path, webapp_url, webapp_token)
