# ci_xposter.py
# Kullanım:
#   python ci_xposter.py --news
#
# Gereken env değişkenleri (GitHub Secrets):
#   X_CLIENT_ID
#   X_CLIENT_SECRET
#   X_REFRESH_TOKEN
#   OPENAI_API_KEY (opsiyonel)

import os, json, time, requests, feedparser, datetime
from openai import OpenAI

CLIENT_ID      = os.environ.get("X_CLIENT_ID")
CLIENT_SECRET  = os.environ.get("X_CLIENT_SECRET")
REFRESH_TOKEN  = os.environ.get("X_REFRESH_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

TOKEN_URL  = "https://api.twitter.com/2/oauth2/token"
TWEET_URL  = "https://api.x.com/2/tweets"

FEEDS = [
    "https://www.aa.com.tr/tr/rss/default?cat=guncel",  # Anadolu Ajansı (Güncel)
    "https://www.reuters.com/world/rss",                # Reuters World
    "https://www.bbc.co.uk/news/world/rss.xml",         # BBC World
    "https://www.dw.com/overlay/atom/tr",               # DW Türkçe
    "https://www.trthaber.com/xml_mobile.php"           # TRT Haber (mobil RSS)
    "https://www.fanatik.com.tr/rss",
    "https://www.ntvspor.net/rss",
    "https://www.trtspor.com.tr/rss.html"
]

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

def get_access_token() -> str:
    import base64
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

    # Önce tokens.json’dan refresh_token dene
    local_refresh = None
    if os.path.exists("tokens.json"):
        try:
            with open("tokens.json", "r", encoding="utf-8") as f:
                local_refresh = json.load(f).get("refresh_token")
        except Exception:
            pass

    refresh = local_refresh or REFRESH_TOKEN

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

    # Eğer yeni refresh_token geldiyse tokens.json’a yaz
    if "refresh_token" in resp:
        with open("tokens.json", "w", encoding="utf-8") as f:
            json.dump(resp, f, indent=2)
        print("🔄 Yeni refresh_token kaydedildi.")

    return resp["access_token"]


def fetch_breaking_from_feeds():
    """RSS akışlarından en güncel, daha önce paylaşılmamış haberi döndürür."""
    posted = set(load_posted())
    latest_item = None
    latest_time = None

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

    return latest_item  # None veya (title, link, source, datetime)


def safe_trim(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len-3] + "..."

def build_tweet(headline: str, link: str, source: str) -> str:
    base_text = f"#SonDakika {headline} ({source})"
    if not OPENAI_API_KEY:
        return safe_trim(f"{base_text} {link}", 280)

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        system = (
            "Sen deneyimli bir Türk haber editörüsün. "
            "Görev: Verilen haberi 200 karakter içinde özetle. "
            "Türkçe yaz. "
            "Kaynak adı ya da link yazma. "
            "Sadece #SonDakika hashtagini kullan."
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
    tweet = build_tweet(title, link, src)
    print(">> Draft:\n", tweet)
    post_tweet(tweet)

    posted = load_posted()
    posted.append(link)
    save_posted(posted)


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "--news":
        run_news_once()
    else:
        run_news_once()
