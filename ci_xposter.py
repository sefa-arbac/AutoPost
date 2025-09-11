# ci_xposter.py
# Kullanım:
#   python ci_xposter.py --news   → Tek seferlik haber tweetle
#   python ci_xposter.py --auto   → Başlangıçta + her 10 dakikada bir tweetle
#
# Gerekli Secrets (GitHub Actions için):
#   X_CLIENT_ID
#   X_CLIENT_SECRET
#   X_REFRESH_TOKEN
#   OPENAI_API_KEY (opsiyonel)

import os, json, time, requests, feedparser, datetime, schedule, sys
from openai import OpenAI

CLIENT_ID      = os.environ.get("X_CLIENT_ID")
CLIENT_SECRET  = os.environ.get("X_CLIENT_SECRET")
REFRESH_TOKEN  = os.environ.get("X_REFRESH_TOKEN")   # fallback olarak
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

TOKEN_URL  = "https://api.twitter.com/2/oauth2/token"
TWEET_URL  = "https://api.twitter.com/2/tweets"

FEEDS = [
    "https://www.aa.com.tr/tr/rss/default?cat=guncel",
    "https://www.reuters.com/world/rss",
    "https://www.bbc.co.uk/news/world/rss.xml",
    "https://www.dw.com/overlay/atom/tr",
    "https://www.trthaber.com/xml_mobile.php",
    "https://www.fanatik.com.tr/rss",
    "https://www.ntvspor.net/rss",
    "https://www.trtspor.com.tr/rss.html"
]

TOK_FILE    = "tokens.json"
POSTED_FILE = "posted.json"

# --- Yardımcılar ---
def load_posted():
    if os.path.exists(POSTED_FILE):
        try:
            with open(POSTED_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_posted(posted):
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(posted, f, indent=2)

def get_refresh_token():
    """tokens.json içindeki refresh_token varsa onu kullanır, yoksa Secrets’taki"""
    if os.path.exists(TOK_FILE):
        try:
            with open(TOK_FILE, "r", encoding="utf-8") as f:
                local = json.load(f).get("refresh_token")
                if local:
                    return local
        except Exception:
            pass
    return REFRESH_TOKEN

def get_access_token() -> str:
    """refresh_token ile yeni access_token alır, yeni refresh_token geldiyse kaydeder"""
    import base64
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

    refresh = get_refresh_token()
    if not refresh:
        raise SystemExit("❌ Refresh token bulunamadı (ne tokens.json’da ne de Secrets’ta).")

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": CLIENT_ID,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {basic}",
    }
    r = requests.post(TOKEN_URL, data=data, headers=headers, timeout=30)
    print("Refresh status:", r.status_code, r.text)
    r.raise_for_status()
    resp = r.json()

    # Yeni refresh_token geldiyse kaydet
    if "refresh_token" in resp:
        with open(TOK_FILE, "w", encoding="utf-8") as f:
            json.dump(resp, f, indent=2)
        print("🔄 Yeni refresh_token kaydedildi.")

    return resp["access_token"]

def fetch_breaking_from_feeds():
    posted = set(load_posted())
    latest_item, latest_time = None, None

    for url in FEEDS:
        try:
            d = feedparser.parse(url)
            if not d.entries:
                continue
            for e in d.entries:
                title = (e.get("title") or "").strip()
                link  = (e.get("link") or "").strip()
                src   = (d.feed.get("title") or url).strip()
                published = e.get("published_parsed") or e.get("updated_parsed")
                if not (title and link and published):
                    continue
                if link in posted:
                    continue

                pub_dt = datetime.datetime.fromtimestamp(time.mktime(published))
                if (latest_time is None) or (pub_dt > latest_time):
                    latest_time = pub_dt
                    latest_item = (title, link, src, pub_dt)
        except Exception as ex:
            print(f"Feed okunamadı: {url} ({ex})")
            continue

    return latest_item

def safe_trim(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[:max_len-3] + "..."

def build_tweet(headline: str, link: str, source: str, published=None) -> str:
    hashtag = "#SonDakika"
    if published:
        age_hours = (datetime.datetime.now(datetime.UTC) - published.replace(tzinfo=datetime.UTC)).total_seconds()/3600
        if age_hours > 2:
            hashtag = "#Gündem"

    base_text = f"{hashtag} {headline}"
    if not OPENAI_API_KEY:
        return safe_trim(f"{base_text} {link}", 280)

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        system = (
            "Sen deneyimli bir Türk haber editörüsün. "
            "Görev: Verilen haberi 200 karakter içinde özetle. "
            "Türkçe yaz. Kaynak adı ya da link yazma. "
            f"Sadece {hashtag} hashtagini kullan."
        )
        resp = client.responses.create(
            model="gpt-4o-mini",
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": headline},
            ],
        )
        text = resp.output_text.strip() or base_text
    except Exception:
        text = base_text

    return safe_trim(f"{text} {link}", 280)

def post_tweet(text: str):
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"text": text}
    r = requests.post(TWEET_URL, headers=headers, json=payload, timeout=30)
    print("POST /2/tweets:", r.status_code, r.text)
    r.raise_for_status()

def run_news_once():
    item = fetch_breaking_from_feeds()
    if not item:
        print("Haber bulunamadı veya hepsi paylaşıldı.")
        return
    title, link, src, pub_dt = item
    tweet = build_tweet(title, link, src, pub_dt)
    print(">> Draft:\n", tweet)
    post_tweet(tweet)

    posted = load_posted()
    posted.append(link)
    save_posted(posted)

# ====== main ======
if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--news":
        run_news_once()
    elif len(sys.argv) >= 2 and sys.argv[1] == "--auto":
        print("⏳ Otomatik mod başlatıldı: Her 10 dakikada bir haber paylaşılacak.")
        run_news_once()
        schedule.every(100).minutes.do(run_news_once)
        while True:
            schedule.run_pending()
            time.sleep(1)
    else:
        run_news_once()
