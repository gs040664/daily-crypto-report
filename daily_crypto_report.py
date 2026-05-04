import os
import requests
import pandas as pd
import datetime
import time
import re
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp
import google.generativeai as genai
import xml.etree.ElementTree as ET

# ==========================================
# 💎 激進波段分析師 - 每日晨報機器人 (AI 升級版)
# ==========================================

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "YOUR_DISCORD_WEBHOOK_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

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

def get_ta_raw_data(symbol):
    """獲取純技術指標字串，供 LLM 分析用"""
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
    
    data_str = (
        f"[{symbol}]\n"
        f"現價: {price:.4f}\n"
        f"EMA20: {ema20:.4f}, EMA50: {ema50:.4f}\n"
        f"RSI(14): {rsi:.1f}\n"
        f"MACD柱狀圖: {macd_hist:.4f}\n"
        f"當前量能對比均量: {vol_ratio:.2f}倍\n"
        f"交易密集區間 (POC): 約 {poc_price:.4f}\n"
    )
    return data_str

def get_latest_tiabtc_video_info():
    """回傳最新的前 5 個影片 ID 列表"""
    try:
        url = "https://www.youtube.com/@tiabtc/videos"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        html = requests.get(url, headers=headers, timeout=10).text
        vids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        unique_vids = []
        for v in vids:
            if v not in unique_vids:
                unique_vids.append(v)
        return unique_vids[:5]
    except:
        return []

def download_and_upload_video(vid_list):
    if not vid_list:
        return None, "沒有找到影片 ID"
        
    for vid in vid_list:
        try:
            url = f"https://www.youtube.com/watch?v={vid}"
            filename = f"video_{vid}.mp4"
            
            # 使用 yt-dlp 下載最低畫質 (360p)，足以辨識 K 線與畫線，並能大幅加快下載與上傳速度
            ydl_opts = {
                'format': 'worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst[ext=mp4]/worst',
                'outtmpl': filename,
                'quiet': True,
                'no_warnings': True,
            }
            print(f"正在下載影片: {url} ...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                
            print(f"影片下載完成，準備上傳至 Gemini (這可能需要幾分鐘)...")
            video_file = genai.upload_file(path=filename)
            
            # 輪詢等待 Gemini 處理影片完成
            print("等待 Gemini 視覺與聽覺模型處理影片...")
            while video_file.state.name == "PROCESSING":
                time.sleep(5)
                video_file = genai.get_file(video_file.name)
                
            if video_file.state.name == "FAILED":
                raise Exception("Gemini 影片處理失敗")
                
            print("Gemini 影片處理完成！可以開始分析。")
            return video_file, ""
            
        except Exception as e:
            print(f"處理影片 {vid} 時發生錯誤: {e}")
            continue
            
    return None, "所有近期影片皆無法下載或上傳處理。"

def get_macro_news():
    """抓取最新的總體經濟與國際局勢新聞標題 (CNBC RSS)"""
    urls = [
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?id=10000664", # Economy
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?id=100727362" # World
    ]
    news_items = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    for url in urls:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            root = ET.fromstring(res.content)
            for item in root.findall('./channel/item')[:3]:
                title = item.find('title').text if item.find('title') is not None else ""
                desc = item.find('description').text if item.find('description') is not None else ""
                desc = re.sub(r'<[^>]+>', '', desc) # 移除 HTML tags
                if title:
                    news_items.append(f"標題: {title}\n摘要: {desc}")
        except:
            continue
            
    if not news_items:
        return "暫無重大國際總經新聞"
    return "\n\n".join(news_items)

def generate_ai_report(ta_string, video_file, macro_news_str):
    if not GEMINI_API_KEY:
        return "⚠️ 未設定 GEMINI_API_KEY，請至 GitHub Secrets 設定。\n\n" + ta_string
        
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        # 動態尋找可用的模型 (解決 404 找不到模型或版本更迭的問題)
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
                
        if not available_models:
            raise Exception("你的 API Key 沒有權限使用任何支援生成的模型。")
            
        # 優先尋找 flash 或 pro
        target_model = None
        for m in available_models:
            if 'flash' in m.lower():
                target_model = m
                break
        if not target_model:
            for m in available_models:
                if 'pro' in m.lower():
                    target_model = m
                    break
        if not target_model:
            target_model = available_models[0]
            
        # GenerativeModel 參數通常不需要 "models/" 前綴
        model_name = target_model.replace("models/", "")
        prompt = f"""
請扮演一位專業技術分析師。每天固定從trading view (或幣安) 上撈取BTC ETH SOL BNB ADA k線數據，並從以下角度進行嚴格判斷：
1. 趨勢方向與型態結構
2. 支撐與壓力 (特別留意交易密集區間 POC)
3. 成交量變化與主力動向
4. 現在偏向突破、假突破、反轉還是整理

我的交易市場：［加密貨幣市場］
我的交易風格：［波段 / 趨勢］
我重視的東西：［技術面 / 籌碼 ］
我的風險偏好：［激進］
加密貨幣市場也跟全球經濟有關係，美國的經濟消息預測還有國際局勢也要加進分析要素裡面。

【資料一：幣安 4H 級別即時技術數據 (過去40天)】
{ta_string}

【資料二：最新美國經濟與國際局勢新聞 (CNBC)】
{macro_news_str}

【資料三：YouTuber (TiaBTC) 最新盤勢分析影片】
{'這份指令已經附帶了 TiaBTC 最新的影片檔案！請直接觀看影片，觀察他在圖表上畫的線、指出的型態，並結合他口述的觀點。' if video_file else '今日因技術問題無影片可供參考。'}

請融合以上資料，嚴格按照上述的分析角度與我的交易風格，撰寫一份「極度精簡且具備高實戰價值」的 Markdown 格式 Discord 晨報：
1. 【宏觀定調】：一句話結合「國際經濟局勢」與「技術面」，定調今天的市場總結。
2. 5 個幣種的分析，每個幣種【只保留兩個欄位】：
   - **現價**: 直接標註於幣種名稱旁。
   - **趨勢與型態**: 用一句話總結目前的盤面強弱與型態結構。絕對不要羅列生硬的技術數據！除非某個數據是支撐你策略的「前三大核心依據」(例如： POC 剛好形成強支撐、或 RSI 出現嚴重背離)，否則一律省略不講。
   - **行動指南**: 直接給出『建議入場價位與必須確認的價格行為』、『預期止盈與止損價位』、『高機率劇本』。
3. 【TiaBTC 觀點速遞】：請用語音與視覺分析，用 1-3 點條列式，極度精簡地總結 TiaBTC 影片中的核心盤勢重點 (例如他在哪裡畫了壓力線)。
4. 語氣果斷專業、像個身經百戰的操盤手。直接輸出純文本，不要加上 ``` 區塊包裝，總字數控制在 1500 字以內。
"""
        model = genai.GenerativeModel(model_name)
        content_payload = [prompt]
        if video_file:
            content_payload.append(video_file)
            
        response = model.generate_content(content_payload)
        
        return response.text
    except Exception as e:
        return f"⚠️ AI 生成失敗 ({e})\n\n純技術數據：\n{ta_string}"

def send_to_discord(content):
    if not WEBHOOK_URL or WEBHOOK_URL == "YOUR_DISCORD_WEBHOOK_URL":
        print(content)
        return
        
    tw_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    full_msg = f"🔔 **早安！AI 激進波段分析師晨報** | {tw_time.strftime('%Y-%m-%d %H:%M')}\n\n" + content
    
    # Discord 單則訊息字數上限為 2000 字元，需分段發送
    chunks = [full_msg[i:i+1900] for i in range(0, len(full_msg), 1900)]
    
    for i, chunk in enumerate(chunks):
        payload = {"content": chunk}
        try:
            res = requests.post(WEBHOOK_URL, json=payload, timeout=10)
            if res.status_code >= 400:
                print(f"Webhook 發送失敗 (HTTP {res.status_code}): {res.text}")
            else:
                print(f"第 {i+1}/{len(chunks)} 段訊息發送成功！")
            time.sleep(1) # 避免觸發 Discord 速率限制
        except Exception as e:
            print(f"網路連線發送失敗: {e}")

if __name__ == "__main__":
    ta_full_string = ""
    for sym in SYMBOLS:
        try:
            time.sleep(0.5)
            ta_full_string += get_ta_raw_data(sym) + "\n"
        except Exception as e:
            ta_full_string += f"[{sym}] 讀取失敗: {e}\n\n"
            
    vids = get_latest_tiabtc_video_info()
    video_file, err = download_and_upload_video(vids)
    macro_news = get_macro_news()
    
    final_report = generate_ai_report(ta_full_string, video_file, macro_news)
    send_to_discord(final_report)
