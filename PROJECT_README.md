# AI 驅動加密貨幣自動化晨報系統（2026 升級版）

這份文件記錄了本專案的核心架構、開發歷程中所遇到的關鍵錯誤（YouTube 防爬蟲、速率限制等），以及對應的解決策略，供未來快速了解專案現狀並進行開發。

---

## 📌 專案核心目標
建立一套透過 **GitHub Actions** 排程每天自動執行的「激進波段風格」加密貨幣晨報系統。
系統會自動從 **Binance** 抓取技術指標數據、從 **CNBC** 抓取總經數據、從 **Cointelegraph** 抓取機構頂級觀點，並透過 **Google Gemini API** 的聯網搜尋能力獲取 **TiaBTC** 最新觀點，最終自動發送 Markdown 格式的綜合晨報至指定 **Discord Webhook**。

---

## 🛠️ 系統架構與特色

### 1. 數據源與抓取機制
*   **幣安 K 線技術指標**：抓取 BTC、ETH、SOL、BNB、ADA 的 4 小時線數據（EMA20, EMA50, RSI, MACD 等），並計算近期最大籌碼密集區（POC）。
*   **宏觀總經新聞**：從 CNBC 的 RSS Feed 抓取當天最重要的國際政經新聞。
*   **機構市場分析**：抓取全球頂級加密權威媒體 Cointelegraph 的市場分析 RSS，提供專業機構觀點。
*   **網紅專家觀點**：使用 Gemini 1.5 內建的 `tools='google_search_retrieval'` 聯網搜尋功能，動態爬取 TiaBTC 過去 24 小時的最新盤勢。

### 2. Discord 自動推送與分段
*   訊息長度若超過 Discord 的 2000 字元限制，腳本會自動將內容進行精準分段，並依照時間戳記有序推送，保證晨報排版精美且完整。

---

## ⚠️ 開發過程中遇到的重大錯誤與解決方案

### 🔴 1. YouTube 實體影片下載與字幕爬蟲完全失效（YouTube 的雲端 IP 封鎖）
*   **問題**：最初設計是透過 `yt-dlp` 下載 TiaBTC 最新影片並上傳至 Gemini 進行視覺聽覺分析，或者用 `youtube-transcript-api` 爬取字幕。然而，由於 GitHub Actions 屬於資料中心（Data Center IP），YouTube 採取了最高級別防禦，回傳 **HTTP 403 / 429** 惡意爬蟲錯誤，導致完全抓不到影片。
*   **解決方案**：
    1. 捨棄不可控的第三方 YouTube 爬蟲套件。
    2. 改用 **Cointelegraph RSS** 直接合法獲取最新的頂級盤勢。
    3. 開啟 **Gemini API 的 Google Search Grounding** 聯網搜尋 TiaBTC 的分析觀點，避開了對 YouTube 的直接爬蟲封鎖。

### 🔴 2. Google AI Studio 免費版速率限制（429 Too Many Requests）
*   **問題**：在密集測試時，短時間內連續觸發 GitHub Actions，導致超出 Gemini API 的免費額度（每分鐘能呼叫的次數 RPM 超限），導致 AI 回報 429 錯誤。
*   **解決方案**：
    1. 在 `model.generate_content` 外層包裝了專門的重試機制迴圈。
    2. 當偵測到 `429` 或 `quota exceeded` 關鍵字時，腳本會自動暫停 **42 秒**（避開速率限制時段）後再行重試，最高重試 3 次，極大提升了系統在雲端 Actions 執行的穩定度。

### 🔴 3. Python `UnboundLocalError: response referenced before assignment`
*   **問題**：在引進重試機制後，由於 `response` 變數在發生異常並重試 3 次失敗後沒有預先初始化，導致了變數未宣告的程式報錯。
*   **解決方案**：在每次重試迴圈前，先明確將 `response = None` 初始化。如果重試 3 次後依然沒有產生合法的回應，則明確拋出異常，讓錯誤資訊清晰可辨。

---

## 🚀 未來維護與執行建議
1. **GitHub Secrets**：請確保在 GitHub 倉庫設定了 `DISCORD_WEBHOOK_URL` 與 `GEMINI_API_KEY` 這兩個秘密變數。
2. **手動測試**：隨時可以到 GitHub 點擊 `Actions` -> `Run workflow` 手動產生一份最新的晨報進行測試。
3. **模型與功能升級**：若未來 Google 釋出更強大的模型或聯網搜尋語法調整，可至 `generate_ai_report` 函數中更新 `model_name` 或搜尋提示詞（Prompt）以提升準確度。
