import os
import requests
import pandas as pd
import datetime
import time
import re

# ==========================================
# 💎 激進波段分析師 - 每日晨報機器人 (GitHub Actions 版)
# ==========================================

# 優先從環境變數讀取 Webhook (給 GitHub Secrets 使用)
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "YOUR_DISCORD_WEBHOOK_URL")

# 監控的 5 大核心幣種
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT"]
INTERVAL = "4h"  # 使用 4 小時線作為波段趨勢判斷最為精準

# 確保數據儲存資料夾存在
DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def get_binance_klines(symbol, interval, limit=240):
    """從幣安 API 獲取真實 K 線數據，並自動處理 GitHub Actions 的 IP 阻擋問題"""
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
            # 幣安錯誤訊息會是 dict 格式，正常的 K 線是 list
            if isinstance(data, list):
                res = data
                break
        except Exception as e:
            continue
            
    if res is None:
        raise Exception("所有幣安 API 端點均無法存取，可能是 IP 完全被封鎖或幣種不存在。")
        
    df = pd.DataFrame(res, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
    df['datetime'] = pd.to_datetime(df['time'], unit='ms')
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['volume'] = df['volume'].astype(float)
    return df

def calculate_indicators(df):
    """計算 RSI, MACD, EMA"""
    # RSI (14)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # EMA 20, 50
    df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
    
    # MACD
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['Hist'] = df['MACD'] - df['Signal']
    
    return df

def save_data_to_csv(symbol, df):
    """將最新的 K 線與指標數據持續儲存並更新到本地 CSV"""
    file_path = os.path.join(DATA_DIR, f"{symbol}_{INTERVAL}_history.csv")
    
    # 只保留需要的欄位以節省空間
    save_df = df[['datetime', 'time', 'open', 'high', 'low', 'close', 'volume', 'EMA20', 'EMA50', 'RSI', 'MACD', 'Hist']].copy()
    
    if os.path.exists(file_path):
        # 讀取舊資料
        old_df = pd.read_csv(file_path)
        # 合併新舊資料，以 time 為主鍵去重，保留最新
        combined_df = pd.concat([old_df, save_df]).drop_duplicates(subset=['time'], keep='last')
        combined_df = combined_df.sort_values('time')
    else:
        combined_df = save_df
        
    # 儲存回 CSV
    combined_df.to_csv(file_path, index=False)
    print(f"✅ {symbol} 歷史數據已更新儲存至 {file_path}，總筆數: {len(combined_df)}")

def generate_analysis(symbol):
    df = get_binance_klines(symbol, INTERVAL)
    df = calculate_indicators(df)
    
    # 儲存持續更新的數據
    save_data_to_csv(symbol, df)
    
    current = df.iloc[-1]
    prev = df.iloc[-2]
    
    price = current['close']
    rsi = current['RSI']
    macd_hist = current['Hist']
    ema20 = current['EMA20']
    ema50 = current['EMA50']
    vol_avg = df['volume'].rolling(20).mean().iloc[-1]
    vol_ratio = current['volume'] / vol_avg if vol_avg > 0 else 1
    
    # --- 盤勢定調邏輯 ---
    trend = "📈 強勢多頭" if price > ema20 and ema20 > ema50 else "📉 弱勢空頭" if price < ema20 and ema20 < ema50 else "⚖️ 震盪整理"
    rsi_status = "🔥 嚴重超買 (留意回調)" if rsi >= 70 else "❄️ 嚴重超賣 (留意反彈)" if rsi <= 30 else "⚡ 動能中性"
    macd_status = "🟢 零軸上金叉 (動能強)" if macd_hist > 0 and current['MACD'] > 0 else "🔴 零軸下死叉 (動能弱)" if macd_hist < 0 else "🟡 動能轉換中"
    
    # --- 動態生成文案 ---
    report = f"### 【{symbol}】 ({INTERVAL} 級別)\n"
    report += f"**現價**: `{price:.4f}`\n"
    report += f"1. **趨勢與型態**: 目前為 {trend}。EMA20({ema20:.4f}) 與 EMA50({ema50:.4f}) {'呈多頭發散' if ema20>ema50 else '呈空頭壓制'}。\n"
    report += f"2. **量價與指標**: RSI: `{rsi:.1f}` ({rsi_status}) | MACD: {macd_status} | 成交量比: `{vol_ratio:.1f}x`。\n"
    
    # 判斷突破與高機率劇本
    if "多頭" in trend:
        if rsi >= 70:
            scenario = f"目前極度強勢但追高風險大。**高機率劇本**：等待回踩 EMA20 ({ema20:.4f}) 支撐不破時，右側進場做多。"
            strength = "極強 (需防出貨)"
        else:
            scenario = f"多頭結構健康。**高機率劇本**：順勢看漲，若帶量突破前高可加倉，防守線設於 EMA50 ({ema50:.4f})。"
            strength = "強勢"
    elif "空頭" in trend:
        if rsi <= 30:
            scenario = f"已經超賣，主力隨時可能獵殺空頭流動性。**高機率劇本**：不宜追空，等待向下插針收長下影線的 2B 假跌破反轉進場。"
            strength = "極弱 (醞釀反轉)"
        else:
            scenario = f"空方控盤。**高機率劇本**：反彈至 EMA20 ({ema20:.4f}) 附近若遇阻，可尋找右側做空機會。"
            strength = "弱勢"
    else:
        scenario = f"籌碼正在博弈換手。**高機率劇本**：在 {ema50:.4f} 與 {ema20:.4f} 區間高拋低吸，等待放量突破表態。"
        strength = "盤整中"

    report += f"3. **籌碼與盤勢定調**: 目前盤面強弱為 **【{strength}】**。\n"
    report += f"4. **【行動指南】**: \n> {scenario}\n\n"
    
    return report

def get_latest_tiabtc_video():
    """從 YouTube 抓取 @tiabtc 最新影片連結"""
    try:
        url = "https://www.youtube.com/@tiabtc/videos"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        html = requests.get(url, headers=headers, timeout=10).text
        vid_match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        if vid_match:
            return f"https://www.youtube.com/watch?v={vid_match.group(1)}"
        return "https://www.youtube.com/@tiabtc"
    except:
        return "https://www.youtube.com/@tiabtc"

def send_to_discord(content):
    if not WEBHOOK_URL or WEBHOOK_URL == "YOUR_DISCORD_WEBHOOK_URL":
        print(content)
        print("\n[提示] 尚未設定 Webhook URL，結果僅印出在終端機。")
        return
        
    # 將 GitHub Actions 的 UTC 時間轉換為 UTC+8 (台灣時間)
    tw_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
        
    payload = {
        "content": f"🔔 **早安！激進波段分析師晨報** | {tw_time.strftime('%Y-%m-%d %H:%M')}\n\n" + content
    }
    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print(f"[{tw_time.strftime('%Y-%m-%d %H:%M:%S')}] Webhook 發送成功！")
    except Exception as e:
        print(f"Webhook 發送失敗: {e}")

if __name__ == "__main__":
    final_msg = ""
    print("正在從幣安撈取真實 K 線並進行運算...")
    for sym in SYMBOLS:
        try:
            time.sleep(0.5)
            final_msg += generate_analysis(sym)
        except Exception as e:
            final_msg += f"### 【{sym}】\n讀取數據失敗: {e}\n\n"
            
    # 加入 tiabtc 最新影片參考
    yt_link = get_latest_tiabtc_video()
    final_msg += f"---\n📺 **【提阿非羅大人 TiaBTC】最新盤勢觀點**\n> 每次分析別忘了參考頻道的最新看法：\n{yt_link}\n"
            
    send_to_discord(final_msg)
