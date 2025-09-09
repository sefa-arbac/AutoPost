# xposter.py
# Kullanım:
#   1) pip install requests openai feedparser
#   2) setx OPENAI_API_KEY "sk-..."   (Windows; terminali kapat/aç)
#   3) python xposter.py --news       # RSS'ten son dakika çek, ChatGPT biçimlendirsin, X'e post et
#      veya
#      python xposter.py "Metni kendim yazıyorum"

import base64, hashlib, os, random, string, threading, time, webbrowser, sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs
import requests
import datetime, time
import schedule

# ---- ChatGPT (OpenAI) ----
from openai import OpenAI
import feedparser

# === DOLDURMAN GEREKENLER (X OAuth2) ===
CLIENT_ID     = "aG54RlV6X3A5ZEFDX3pWdUdYaVo6MTpjaQ"
CLIENT_SECRET = "zyYVIO8gewOlETKl0G2v7Qred1Sf2HJ9jiOblu5FZUSsxtb4yu"
REDIRECT_URI  = "http://127.0.0.1:8000/callback"

# === X sabitleri ===
SCOPES     = "tweet.read tweet.write users.read offline.access"
AUTH_URL   = "https://x.com/i/oauth2/authorize"
TOKEN_URL  = "https://api.twitter.com/2/oauth2/token"
TWEET_URL  = "https://api.x.com/2/tweets"
TOK_FILE   = "tokens.json"

POSTED_FILE = "posted.json"

# === Haber kaynakları (RSS) — İstediğin gibi düzenle
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

# === Yardımcılar ===
def b64url(b: bytes) -> str: return base64.urlsafe_b64encode(b).decode().rstrip("=")
def gen_verifier(): return b64url(os.urandom(64))
def gen_challenge(v: str): return b64url(hashlib.sha256(v.encode()).digest())
def gen_state(n=24): return "".join(random.choice(string.ascii_letters+string.digits) for _ in range(n))

def save_tokens(tokens: dict):
    tokens["_saved_at"] = int(time.time())
    with open(TOK_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)

def load_tokens():
    if not os.path.exists(TOK_FILE): return None
    with open(TOK_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def token_expired(tokens) -> bool:
    exp = int(tokens.get("expires_in", 3600))
    saved = int(tokens.get("_saved_at", 0))
    return int(time.time()) >= saved + exp - 60

# === Callback HTTP server ===
class CaptureHandler(BaseHTTPRequestHandler):
    query = {}
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404); self.end_headers()
            self.wfile.write(b"Not found"); return
        CaptureHandler.query = parse_qs(parsed.query)
        print("[Callback] Query:", CaptureHandler.query, flush=True)
        self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h3>Authorization received. You can close this tab.</h3>")
    def log_message(self, *a): return

# === OAuth 2.0: code -> token (Basic auth ile) ===
def exchange_code_for_tokens(code: str, verifier: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    }
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {basic}",
    }
    print("Exchanging code for tokens...")
    r = requests.post(TOKEN_URL, data=data, headers=headers, timeout=30)
    print("Token response status:", r.status_code)
    print("Token response body:", r.text)
    r.raise_for_status()
    tokens = r.json()
    save_tokens(tokens)
    return tokens

def refresh_tokens(refresh_token: str) -> dict:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
    }
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {basic}",
    }
    r = requests.post(TOKEN_URL, data=data, headers=headers, timeout=30)
    print("Refresh response status:", r.status_code)
    print("Refresh response body:", r.text)
    r.raise_for_status()
    tokens = r.json()
    save_tokens(tokens)
    return tokens

def pkce_authorize() -> dict:
    verifier  = gen_verifier()
    challenge = gen_challenge(verifier)
    state     = gen_state()

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{AUTH_URL}?{urlencode(params)}"

    server = HTTPServer(("127.0.0.1", 8000), CaptureHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("👉 Tarayıcı açılıyor. Açılmazsa bu linki kopyala:\n", url, flush=True)
    webbrowser.open(url)

    while not CaptureHandler.query:
        time.sleep(0.2)
    server.shutdown()

    q = CaptureHandler.query
    if "error" in q:
        raise SystemExit(f"Auth error: {q['error'][0]}")
    if state != q.get("state", [""])[0]:
        raise SystemExit("State mismatch")

    code = q["code"][0]
    return exchange_code_for_tokens(code, verifier)

def ensure_tokens() -> dict:
    tokens = load_tokens()
    if tokens is None:
        print("🔐 İlk yetkilendirme yapılıyor (PKCE)...")
        tokens = pkce_authorize()
    elif token_expired(tokens):
        if "refresh_token" in tokens:
            print("♻️ Access token yenileniyor (refresh_token ile)...")
            tokens = refresh_tokens(tokens["refresh_token"])
        else:
            print("🔁 refresh_token yok; yeniden yetkilendirme gerekiyor.")
            tokens = pkce_authorize()
    return tokens

def post_tweet(text: str):
    tokens = ensure_tokens()
    access_token = tokens["access_token"]
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {"text": text}
    r = requests.post(TWEET_URL, headers=headers, json=payload, timeout=30)

    if r.status_code in (401, 403) and "token" in r.text.lower() and "refresh_token" in tokens:
        print("⚠️ Token hatası; otomatik yenileyip tekrar deniyorum...")
        tokens = refresh_tokens(tokens["refresh_token"])
        headers["Authorization"] = f"Bearer {tokens['access_token']}"
        r = requests.post(TWEET_URL, headers=headers, json=payload, timeout=30)

    print("Status:", r.status_code)
    print(r.text)

def safe_tweet(text: str, link: str) -> str:
    MAX_LEN = 280
    URL_LEN = 23  # X her linki 23 karakter sayar
    RESERVED = URL_LEN + 1  # boşluk + link

    if len(text) > MAX_LEN - RESERVED:
        text = text[:MAX_LEN - RESERVED - 3] + "..."
    return f"{text} {link}"

POSTED_FILE = "posted.json"

def load_posted():
    if os.path.exists(POSTED_FILE):
        try:
            with open(POSTED_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # Dosya boş ya da bozuksa temiz bir listeyle başla
            return []
    return []

def save_posted(posted):
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(posted, f, indent=2)


# ====== HABER + ChatGPT ======
def fetch_breaking_from_feeds(feeds=FEEDS):
    """RSS akışlarından en güncel, daha önce paylaşılmamış haberi döndürür."""
    posted = load_posted()
    latest = None
    latest_time = None

    for url in feeds:
        try:
            d = feedparser.parse(url)
            if not d.entries:
                continue

            for e in d.entries:
                title = e.get("title") or ""
                link  = e.get("link") or ""
                src   = d.feed.get("title") or url
                published = e.get("published_parsed")
                if not (title and link and published):
                    continue

                # Eğer bu haber daha önce paylaşıldıysa geç
                if link in posted:
                    continue

                pub_dt = datetime.datetime.fromtimestamp(time.mktime(published))
                if (latest_time is None) or (pub_dt > latest_time):
                    latest_time = pub_dt
                    latest = (title.strip(), link.strip(), src.strip(), published)

        except Exception as ex:
            print(f"Feed okunamadı: {url} ({ex})")
            continue

    return latest  # None veya (title, link, source, published)


def safe_trim(text: str, max_len: int) -> str:
    """Kelime bazlı güvenli kısaltma (sonuna ... ekler)."""
    if len(text) <= max_len:
        return text
    words = text.split()
    out = ""
    for w in words:
        if len(out) + len(w) + 1 > max_len - 3:  # 3 = "..."
            break
        out += (" " if out else "") + w
    return out + "..."

def build_tweet_with_chatgpt(headline: str, link: str, source: str, published=None) -> str:
    """
    ChatGPT kısa bir haber özeti üretir.
    - Haber yaşı ≤ 2 saat → #SonDakika
    - Haber yaşı > 2 saat → #Gündem
    - Linki biz ekleriz (asla kesilmez).
    """
    # Haber yaşı kontrolü
    force_hashtag = "#SonDakika"
    if published:
        dt = datetime.datetime.fromtimestamp(time.mktime(published))
        age_hours = (datetime.datetime.utcnow() - dt).total_seconds() / 3600
        if age_hours > 2:
            force_hashtag = "#Gündem"

    client = OpenAI()
    system = (
        "Sen deneyimli bir Türk haber editörüsün. "
        "Görev: Verilen haberi 200 karakter içinde özetle. "
        "Türkçe yaz. "
        "Kaynak adı ya da parantez içi bilgi verme. "
        "Link yazma, linki biz ekleyeceğiz. "
        f"Sadece {force_hashtag} hashtagini kullan."
    )
    user = f"Başlık: {headline}"

    resp = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    text = resp.output_text.strip()

    # Tweet boyutu kontrolü (280 - 23 link rezerve)
    MAX_LEN = 280
    URL_LEN = 23
    RESERVED = URL_LEN + 1  # link + boşluk
    text = safe_trim(text, MAX_LEN - RESERVED)

    tweet = f"{text} {link}"
    return tweet

def post_latest_news():
    item = fetch_breaking_from_feeds()
    if not item:
        print("Haber bulunamadı (RSS boş veya hepsi paylaşıldı).")
        return
    title, link, src, published = item
    tweet = build_tweet_with_chatgpt(title, link, src, published)
    print(">> Tweet draft:\n", tweet, "\n")
    post_tweet(tweet)

    # Cache'e ekle
    posted = load_posted()
    posted.append(link)
    save_posted(posted)

# ====== main ======
if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--news":
        post_latest_news()
    elif len(sys.argv) >= 2 and sys.argv[1] == "--auto":
        print("⏳ Otomatik mod başlatıldı: Her 10 dakikada bir haber paylaşılacak.")

        # Başlangıçta hemen bir tweet at
        post_latest_news()

        # Sonra her 10 dakikada bir devam et
        schedule.every(10).minutes.do(post_latest_news)

        while True:
            schedule.run_pending()
            time.sleep(1)
    elif len(sys.argv) >= 2:
        text = " ".join(sys.argv[1:])
        post_tweet(text)
    else:
        print('Kullanım:\n'
              '  python xposter.py "Metin"\n'
              '  python xposter.py --news   (RSS + ChatGPT ile tek seferlik haber)\n'
              '  python xposter.py --auto   (Başlangıçta bir defa + her 10 dakikada bir haber paylaş)')


