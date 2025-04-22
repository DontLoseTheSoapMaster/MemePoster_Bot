
#!/usr/bin/env python3
"""
meme_fetcher.py  v4.0  (per‑user / per‑chat logic)

Usage examples
--------------
# random ENG meme for user 42
python meme_fetcher.py --user 42

# RU meme about cats for chat 777
python meme_fetcher.py "кот" rus --chat 777
"""

import os, sys, random, re, requests, argparse, time
from pathlib import Path
from urllib.parse import quote_plus, urlparse
import pyodbc
from sshtunnel import SSHTunnelForwarder
import giphy_client
from giphy_client.rest import ApiException

# ─── CONFIG ─────────────────────────────────────────────────────────────────
GIPHY_KEY = os.getenv("GIPHY_KEY")
SSH_HOST  = os.getenv("SSH_HOST")
SSH_PORT  = int(os.getenv("SSH_PORT","22"))
SSH_USER  = os.getenv("SSH_USER")
SSH_KEY   = os.getenv("SSH_KEY")

PG_HOST   = os.getenv("PG_HOST","127.0.0.1")
PG_PORT   = int(os.getenv("PG_PORT","my_port"))
PG_DB     = os.getenv("PG_DB","postgres")
PG_UID    = os.getenv("PG_UID","postgres")
PG_PWD    = os.getenv("PG_PWD","")

#Windows Version
#DOWNLOAD_DIR = Path("memes"); DOWNLOAD_DIR.mkdir(exist_ok=True)
#linux version
DOWNLOAD_DIR = Path(__file__).parent.resolve() / "memes"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ─── UTILS ──────────────────────────────────────────────────────────────────
CYRILLIC_RE = re.compile('[а-яА-ЯёЁ]')
HEADERS = {'User-Agent': 'MemeFetcher/4.0'}

def is_cyrillic(txt:str)->bool: return bool(CYRILLIC_RE.search(txt))

ENG_SUBS = ['memes','dankmemes','me_irl']
RUS_SUBS = ['ru_memes','RussianMemes','pikabu']

# ─── EXTERNAL SOURCES ───────────────────────────────────────────────────────
def meme_api_random(sub:str)->dict:
    data=requests.get(f'https://meme-api.com/gimme/{sub}',timeout=20).json()
    return {'url':data['url'],'title':data['title'],'source':f"meme-api/{sub}"}

def reddit_search(q:str, subs:list[str], lang:str)->list[dict]:
    url = ( "https://www.reddit.com/search.json?q="+quote_plus(q)+
            "&sort=relevance&t=year&limit=100" )
    try:
        js=requests.get(url,headers=HEADERS,timeout=20).json()
    except: return []
    out=[]
    for ch in js.get("data",{}).get("children",[]):
        d=ch["data"]
        if d.get("post_hint")!="image": continue
        title=d["title"]
        if lang=="rus" and not (is_cyrillic(title) or d["subreddit"] in RUS_SUBS): continue
        if lang=="eng" and is_cyrillic(title): continue
        out.append({'url':d['url_overridden_by_dest'],
                    'title':title,
                    'source':f"r/{d['subreddit']}"})
    return out

def giphy_ru_search(q:str)->list[dict]:
    if not GIPHY_KEY: return []
    api=giphy_client.DefaultApi()
    try:
        rsp=api.gifs_search_get(GIPHY_KEY,q,lang="ru",limit=25,rating="pg-13")
    except ApiException:
        return []
    out=[]
    for g in rsp.data:
        out.append({'url':g.images.original.url,
                    'title':getattr(g,"title","") or q,
                    'source':f"giphy/{g.id}"})
    return out

def pikabu_ru(tag:str|None=None)->list[dict]:
    url = (f"https://api.pikabu.ru/v1/story?tag={quote_plus(tag)}"
           if tag else "https://api.pikabu.ru/v1/post/random")
    try:
        js=requests.get(url,timeout=20).json()
    except: return []
    posts=js.get("stories") or js.get("posts") or []
    out=[]
    for p in posts:
        if not p.get("preview"): continue
        out.append({'url':p["preview"],
                    'title':p["title"],
                    'source':f"pikabu/{p.get('story_id',p.get('id'))}"})
    return out

def pick_english_meme(key:str|None)->dict:
    if key:
        cand=reddit_search(key,ENG_SUBS,'eng')
        random.shuffle(cand)
        return cand[0] if cand else meme_api_random(random.choice(ENG_SUBS))
    return meme_api_random(random.choice(ENG_SUBS))

def pick_russian_meme(key:str|None)->dict:
    cand=reddit_search(key or "мем",RUS_SUBS,"rus")
    if cand: random.shuffle(cand); return cand[0]
    cand=giphy_ru_search(key or "мем")
    if cand: random.shuffle(cand); return cand[0]
    cand=pikabu_ru(key)
    if cand: random.shuffle(cand); return cand[0]
    raise RuntimeError("No RU meme found")

def fetch_external_unique(key:str|None, lang:str, blacklist:set[str])->dict:
    ATT=20
    for _ in range(ATT):
        m = pick_russian_meme(key) if lang=="rus" else pick_english_meme(key)
        if m['url'] not in blacklist: return m
    raise RuntimeError("External fetch failed to find unique")

# ─── DB WRAPPER ─────────────────────────────────────────────────────────────
class Db:
    def __init__(self,dsn:str): self.cx=pyodbc.connect(dsn,autocommit=False)
    def close(self): self.cx.close()

    # helper
    def _identity_clause(self, field:str):
        return "USER_ID" if field=="USER_ID" else "CHAT_ID"

    def blacklist(self, field:str, value:int)->set[str]:
        clause=self._identity_clause(field)
        sql=f'''SELECT a."url"
                 FROM "memes_all_urls" a
                 JOIN "memes_queries_journal" q ON a."ID"=q."URL_ID"
                 WHERE q."{clause}"=?'''
        return {r.url for r in self.cx.cursor().execute(sql, value)}

    def find_cached_url(self, key:str|None, lang:str,
                        field:str, val:int):
        clause=self._identity_clause(field)
        params=[val]
        sql=(
            'SELECT a."ID",a."url",a."name",a."source" '
            'FROM "memes_all_urls" a '
            f'LEFT JOIN "memes_queries_journal" q '
            f'ON a."ID"=q."URL_ID" AND q."{clause}"=? '
            'WHERE q."ID" IS NULL '
        )
        if key or lang:
            sql+=(
                'AND EXISTS (SELECT 1 FROM "memes_key_words_using" k '
                'WHERE k."ID_URL"=a."ID" '
            )
            if key:
                sql+='AND k."Keywords_Searched_user"=? '
                params.append(key)
            if lang:
                sql+='AND k."Language"=? '
                params.append(lang)
            sql+=")"
        sql+=' ORDER BY a."date_upload" DESC LIMIT 1'
        #print(sql)
        #print(params)
        cur=self.cx.cursor().execute(sql,*params)
        row=cur.fetchone()
        if row:
            #print(dir(row))
            #print(row)
            return dict(id=row.ID,url=row.url,title=row.name,source=row.source)
        return None

    def get_url_id(self,url)->int|None:
        cur=self.cx.cursor().execute('SELECT "ID" FROM "memes_all_urls" WHERE url=?',url)
        r=cur.fetchone()
        return r.id if r else None

    def insert_url(self,url,title,source)->int:
        cur=self.cx.cursor()
        cur.execute('INSERT INTO "memes_all_urls" ("url","name","source") VALUES (?,?,?) RETURNING "ID"',
                    url,title,source)
        url_id=cur.fetchone()[0]
        self.cx.commit()
        return url_id

    def add_journal(self,url_id, field:str,val:int):
        clause=self._identity_clause(field)
        cur=self.cx.cursor()
        cur.execute(f'INSERT INTO "memes_queries_journal" ("{clause}","URL_ID") VALUES (?,?)',
                    val,url_id)
        self.cx.commit()

    def add_keyword_usage(self,url_id,key,lang,field,val):
        if key == '':
            key = None
        clause=self._identity_clause(field)
        cur=self.cx.cursor()
        cur.execute(f'''INSERT INTO "memes_key_words_using"
                     ("ID_URL","Keywords_Searched_user","Language","{clause}")
                     VALUES (?,?,?,?)''',url_id,key,lang,val)
        self.cx.commit()

# ─── CONNECT ────────────────────────────────────────────────────────────────
def make_dsn()->tuple[str,SSHTunnelForwarder|None]:
    if not SSH_HOST:
        return (f'DRIVER={{PostgreSQL Unicode}};SERVER={PG_HOST};PORT={PG_PORT};DATABASE={PG_DB};UID={PG_UID};PWD={PG_PWD}', None)
    tun=SSHTunnelForwarder((SSH_HOST,SSH_PORT),
                           ssh_username=SSH_USER,
                           ssh_private_key=SSH_KEY,
                           remote_bind_address=(PG_HOST,PG_PORT))
    tun.start()
    dsn=(f'DRIVER={{PostgreSQL Unicode}};SERVER=127.0.0.1;PORT={tun.local_bind_port};DATABASE={PG_DB};UID={PG_UID};PWD={PG_PWD}')
    return dsn,tun

# ─── FILE DL ────────────────────────────────────────────────────────────────
def download(url:str)->Path:
    name=Path(urlparse(url).path).name or f"meme_{int(time.time())}.jpg"
    dest=DOWNLOAD_DIR/name
    data=requests.get(url,timeout=30).content
    dest.write_bytes(data); return dest

# ─── MAIN ────────────────────────────────────────────────────────────────────
def parse_args():
    p=argparse.ArgumentParser()
    p.add_argument("keywords",nargs="?",help="search keywords")
    p.add_argument("lang",nargs="?",choices=["eng","rus"],default="eng")
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--user",type=int,help="user id")
    g.add_argument("--chat",type=int,help="chat id")
    return p.parse_args()

def main(keywords:str|None, lang:str|None,
                        user:str|None, chat:str|None):
    #args=parse_args()
    field='USER_ID' if user is not None else 'CHAT_ID'
    val = user if user is not None else chat
    #print(val)
    #print(keywords)
    #print(lang)
    #print(user)
    #print(chat)
    #print(field)
    dsn,tun=make_dsn()
    db=Db(dsn)
    try:
        # 1. blacklist
        bl=db.blacklist(field,val)
        # 2. try cached
        cached=db.find_cached_url(keywords,lang,field,val)
        if cached:
            url_id=cached['id']
            path=download(cached['url'])
            db.add_journal(url_id,field,val)
            if keywords or lang:
                db.add_keyword_usage(url_id,keywords,lang,field,val)
            print(f"📁 Cached meme delivered → {path}")
            return

        # 3. fetch external
        meme=fetch_external_unique(keywords,lang,bl)
        url_id=db.get_url_id(meme['url'])
        if url_id is None:
            url_id=db.insert_url(meme['url'],meme['title'],meme['source'])
        db.add_journal(url_id,field,val)
        if keywords or lang:
            db.add_keyword_usage(url_id,keywords,lang,field,val)
        path=download(meme['url'])
        print(f"✅ New meme fetched → {path}")
    finally:
        db.close()
        if tun: tun.stop()

if __name__=="__main__":
    main()
