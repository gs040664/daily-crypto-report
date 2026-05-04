import os
import requests
import pandas as pd
import datetime
import time
import re
import google.generativeai as genai
import xml.etree.ElementTree as ET
from youtube_transcript_api import YouTubeTranscriptApi

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
    
    # 新增資金費率
    funding_rate = get_binance_funding_rate(symbol)
    funding_str = f"{funding_rate * 100:.4f}%" if funding_rate is not None else "暫無資料"
    
    data_str = (
        f"[{symbol}]\n"
        f"現價: {price:.4f}\n"
        f"EMA20: {ema20:.4f}, EMA50: {ema50:.4f}\n"
        f"RSI(14): {rsi:.1f}\n"
        f"MACD柱狀圖: {macd_hist:.4f}\n"
        f"當前量能對比均量: {vol_ratio:.2f}倍\n"
        f"交易密集區間 (POC): 約 {poc_price:.4f}\n"
        f"即時資金費率 (Funding Rate): {funding_str}\n"
    )
    return data_str

def get_cointelegraph_analysis():
    """抓取 Cointelegraph 最新的市場分析文章 (全球頂級機構觀點)"""
    url = "https://cointelegraph.com/rss/tag/market-analysis"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        root = ET.fromstring(res.content)
        items = []
        for item in root.findall('./channel/item')[:3]:
            title = item.find('title').text if item.find('title') is not None else ""
            desc = item.find('description').text if item.find('description') is not None else ""
            desc = re.sub(r'<[^>]+>', '', desc) # 移除 HTML tags
            if title:
                items.append(f"標題: {title}\n摘要: {desc[:500]}...") # 截斷過長摘要
        if items:
            return "\n\n".join(items)
        return "暫無最新機構分析"
    except Exception as e:
        print(f"Cointelegraph 讀取失敗: {e}")
        return "暫無最新機構分析"

def get_tiabtc_transcripts():
    """獲取 TiaBTC 最新影片字幕與前 5 支影片整合字幕"""
    latest_transcript = ""
    top5_combined = []
    
    try:
        url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCy2h-yNK9OF1kXDtT3AlF3Q"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=10)
        root = ET.fromstring(res.content)
        
        ns = {'yt': 'http://www.youtube.com/xml/schemas/2015', 'atom': 'http://www.w3.org/2005/Atom'}
        vids = []
        for entry in root.findall('atom:entry', ns)[:5]:
            vid_elem = entry.find('yt:videoId', ns)
            title_elem = entry.find('atom:title', ns)
            title = title_elem.text if title_elem is not None else "未知影片"
            if vid_elem is not None and vid_elem.text:
                vids.append((vid_elem.text, title))
                
        for i, (vid, title) in enumerate(vids):
            try:
                transcript_list = YouTubeTranscriptApi.list_transcripts(vid)
                transcript = next(iter(transcript_list))
                if 'zh' not in transcript.language_code:
                    transcript = transcript.translate('zh-Hant')
                res_data = transcript.fetch()
                text = " ".join([t['text'] for t in res_data])
                
                if i == 0:
                    latest_transcript = text[:25000]
                    
                top5_combined.append(f"影片：{title}\n字幕摘要：{text[:4500]}")
                print(f"影片字幕抓取成功 ({vid})！語言: {transcript.language_code}")
            except Exception as e:
                print(f"影片字幕抓取失敗 ({vid}): {e}")
                continue
    except Exception as e:
        print(f"取得頻道 RSS 失敗: {e}")
        
    return latest_transcript, "\n\n".join(top5_combined)

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

def generate_ai_report(ta_string, ct_analysis_str, latest_transcript, top5_transcripts, macro_news_str):
    if not GEMINI_API_KEY:
        return "⚠️ 未設定 GEMINI_API_KEY，請至 GitHub Secrets 設定。\n\n" + ta_string
        
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        # 動態尋找可用的模型
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
                
        if not available_models:
            raise Exception("你的 API Key 沒有權限使用任何支援生成的模型。")
            
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
            
        model_name = target_model.replace("models/", "")
        
        # 依照優先順位組織 TiaBTC 資訊
        tiabtc_sources = []
        if latest_transcript:
            tiabtc_sources.append(f"【優先順位 1：TiaBTC 最新影片字幕】\n{latest_transcript}")
            task_prompt = "請優先根據【優先順位 1：TiaBTC 最新影片字幕】提取重點。若有需要，再結合【優先順位 3：前 5 支影片整合字幕】進行精準推斷。"
            use_search = False
        else:
            tiabtc_sources.append("【優先順位 1：無】無最新影片字幕，請改用優先順位 2。")
            task_prompt = "今日無法擷取最新影片字幕，請根據【優先順位 2】在網路上聯網搜尋『TiaBTC 最新影片分析 提阿非羅大人』。另外若有【優先順位 3：前 5 支影片整合字幕】，請一併綜合提取。"
            use_search = True
            
        if top5_transcripts:
            tiabtc_sources.append(f"【優先順位 3：TiaBTC 前 5 支影片整合字幕】\n{top5_transcripts}")
        else:
            tiabtc_sources.append("【優先順位 3：無】無前 5 支影片整合字幕。")
            
        tiabtc_source_str = "\n\n".join(tiabtc_sources)
        
        prompt = f"""
請扮演一位專業技術分析師。每天固定從trading view (或幣安) 上撈取BTC ETH SOL BNB ADA k線與合約資金費率數據，並從以下角度進行嚴格判斷：
1. 趨勢方向與型態結構
2. 支撐與壓力 (特別留意交易密集區間 POC)
3. 成交量變化與主力動向
4. 資金費率 (Funding Rate) 的變化：資金費率代表多空雙方的擁擠程度與情緒！當資金費率異常偏高或偏低時，代表出現了擁擠交易。請提醒我不做擁擠的交易，因為市場大部分人是虧錢的，可以適時思考反向或觀望策略！
5. 現在偏向突破、假突破、反轉還是整理

我的交易市場：［加密貨幣市場］
我的交易風格：［波段 / 趨勢］
我重視的東西：［技術面 / 籌碼 ］
我的風險偏好：［激進］
加密貨幣市場也跟全球經濟有關係，美國的經濟消息預測還有國際局勢也要加進分析要素裡面。

【資料一：幣安 4H 級別即時技術數據 (過去40天)】
{ta_string}

【資料二：最新美國經濟與國際局勢新聞 (CNBC)】
{macro_news_str}

【資料三：全球頂級機構與分析師觀點 (Cointelegraph)】
{ct_analysis_str}

{tiabtc_source_str}

【特別任務：整合 TiaBTC 觀點】
{task_prompt}

⚠️ **核心提醒**：即使 TiaBTC 觀點抓取失敗、甚至沒有 Cointelegraph 觀點，你也必須堅持頂級技術分析師的專業態度，根據【資料一：幣安 4H 技術數據】提供完整的 5 個幣種技術面、資金費率分析與行情策略，絕對不能省略或不寫。

請融合以上所有資料，嚴格按照我的交易風格，撰寫一份「極度精簡、排版精美且具備高實戰價值」的 Discord 晨報。請善用 Markdown 格式（如粗體、區塊引用、列表）來增加易讀性，並在適當的地方加入各種相關 Emoji（如 📈、📉、💡、🎯、⚠️、🔥、💎、📊）來視覺引導：
1. 【宏觀定調】：一句話結合「國際經濟局勢」、「技術面」與「多空擁擠度（資金費率）」，定調今天的市場總結。
2. 5 個幣種的分析，每個幣種【只保留兩個欄位】：
   - **現價**: 直接標註於幣種名稱旁。
   - **趨勢與型態**: 用一句話總結目前的盤面強弱、型態結構，並特別指出資金費率是否呈現擁擠交易（若是過高過低則警示反轉或調整）。
   - **行動指南**: 直接給出『建議入場價位與必須確認的價格行為』、『預期止盈與止損價位』、『高機率劇本』。
3. 【TiaBTC 觀點速遞】：請優先以 TiaBTC 的觀點（無論是字幕提供、或是你搜尋到的分析）進行 1-3 點條列式精簡總結。若有必要，再將 Cointelegraph 的頂級機構觀點作為背景與補充。
4. 語氣果斷專業、像個身經百戰的操盤手。直接輸出純文本，不要加上 ``` 區塊包裝，總字數控制在 1500 字以內。
"""
        try:
            if use_search:
                print("嘗試啟用 Gemini Google 搜尋引擎...")
                try:
                    model = genai.GenerativeModel(model_name, tools='google_search_retrieval')
                    response = model.generate_content(prompt)
                except Exception as search_err:
                    print(f"無法啟用 Google 搜尋工具 ({search_err})，退回無搜尋模式...")
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content(prompt)
            else:
                print("使用已擷取的字幕數據，停用搜尋引擎以節省 Quota...")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                
            return response.text
        except Exception as e:
            raise e
    except Exception as e:
        return (
            f"⚠️ **AI 核心服務暫時受限**\n"
            f"原因：您的 Gemini API Key 超出官方今日或每分鐘的請求額度（429 Quota Exceeded），或者 API 金鑰暫時遭到拒絕。此時 Google 的 AI 服務完全拒絕處理任何對話或分析請求，因此暫時無法為您生成專家解析。\n\n"
            f"待額度重置後，系統將自動恢復專家策略晨報。以下為當前最新的盤勢技術數據供您參考：\n"
            f"```text\n{ta_string}\n```"
        )

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
            
    ct_analysis = get_cointelegraph_analysis()
    latest_transcript, top5_transcripts = get_tiabtc_transcripts()
    macro_news = get_macro_news()
    
    final_report = generate_ai_report(ta_full_string, ct_analysis, latest_transcript, top5_transcripts, macro_news)
    send_to_discord(final_report)
