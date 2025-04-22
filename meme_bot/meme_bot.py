#!/usr/bin/env python3
"""
meme_bot.py – Telegram bridge for meme_fetcher_4 (v1.2, April‑2025)

• /start  → buttons GET MEME | REGISTER | CANCEL | Language switch
• GET MEME → choose AnyMeme (random) or ByKeyWords
• Handles per‑user / per‑chat locks via memes_actions_tg_bot
• Registration stored in memes_user_list or memes_chat_list
• Cleans memes/ folder to 500 newest images
"""

import os, logging, shlex, subprocess, tempfile
#new line
import asyncio, functools
#from telegram import   
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup,constants
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

#from meme_fetcher_4 import Db, make_dsn   # uses your corrected file
#from meme_fetcher_4 import main as fetch_meme_sync
import meme_fetcher_4 as mf

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("bot")

# ─── constants ────────────────────────────────────────────────────────────
BTN_GET, BTN_REG, BTN_CANCEL, BTN_LANG = "GET", "REG", "CANCEL", "LANG"
BTN_ANY, BTN_KEY, BTN_LANG_MEME = "ANY", "KEY", "LANG_MEME"
LANG_EN, LANG_RU = 1, 2
LANG_EN_MEME, LANG_RU_MEME = 1,2

# --- helper so we can pass to run_in_executor -----------------------
def _fetch_sync(keywords, lang, user, chat):
    """
    Runs meme_fetcher_4.main() synchronously and
    returns the Path of the file it downloaded.
    """
    mf.main(keywords, lang, user, chat)
    # main() always downloads into mf.DOWNLOAD_DIR and names by time
    newest = max(
        (p for p in mf.DOWNLOAD_DIR.iterdir() if p.is_file()),
        key=lambda p: p.stat().st_mtime
    )
    return newest

# ─── helper text -----------------------------------------------------------
def text_start(lang):
    return (
        "Hi, I’m a meme generator bot. /get_meme or «GET MEME».\n"
        "Not registered? – «REGISTER».\n"
        "Lock lasts 10 min; cancel – «CANCEL».\n"
        "Русская версия – «Русский Язык»."
        if lang == LANG_EN else
        "Привет, я генератор мемов. /get_meme или «GET MEME».\n"
        "Не зарегистрирован? – «REGISTER».\n"
        "Команда занята 10 мин; отмена – «CANCEL».\n"
        "English version – «English Language»."
    )

def text_lock(lang):
    return ("Sorry, but someone already started the command"
            if lang == LANG_EN else
            "Извините, но кто‑то уже запустил команду")

def text_registered(lang):
    return ("Account already registered" if lang == LANG_EN
            else "Регистрация уже пройдена")

def not_yet_registered(lang):
    return ("You should register first, and only then you can GET YOUR MEMES)" if lang == LANG_EN
            else "Для начала зарегистрируйся, потом мемчики")

def text_reg_ok(lang):
    return ("Account successfully registered" if lang == LANG_EN
            else "Регистрация прошла успешно")

def text_choose_mode(lang):
    return ("Okay, ready!  «AnyMeme» for random, «ByKeyWords» to search."
            if lang == LANG_EN else
            "Ладно, поехали!  «AnyMeme» — случайный, «ByKeyWords» — по ключу.")

def text_kw_prompt(lang):
    return ("Type key words and send" if lang == LANG_EN
            else "Введите ключевые слова и отправьте сообщение")

def kb_start(lang):
    lang_btn = "English Language" if lang == LANG_RU else "Русский Язык"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("GET MEME", callback_data=BTN_GET)],
        [InlineKeyboardButton("REGISTER", callback_data=BTN_REG)],
        [InlineKeyboardButton("CANCEL", callback_data=BTN_CANCEL)],
        [InlineKeyboardButton(lang_btn,   callback_data=BTN_LANG)]
    ])

def kb_mode(lang):
    lang_btn = "English Language" if lang == LANG_RU else "Русский Язык"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("AnyMeme",     callback_data=BTN_ANY)],
        [InlineKeyboardButton("ByKeyWords",  callback_data=BTN_KEY)],
        [InlineKeyboardButton("Cancel",  callback_data=BTN_CANCEL)],
        [InlineKeyboardButton(lang_btn,   callback_data=BTN_LANG_MEME)]
         
    ])

# ─── DB wrapper ------------------------------------------------------------
class BotDB(mf.Db):
    def __init__(self):
        dsn, tun = mf.make_dsn()
        super().__init__(dsn)
        self.tun = tun
    def close_all(self):
        super().close()
        if self.tun: self.tun.stop()

    # lock helpers
    def lock_exists(self, uid, cid):
        return self.cx.cursor().execute(
            'SELECT 1 FROM "memes_actions_tg_bot" WHERE "User_ID"=? AND "Chat_ID"=? AND "Action_ID">0',
            uid, cid).fetchone() is not None
    def action_id(self, uid, cid):
        row = self.cx.cursor().execute(
            'SELECT "Action_ID" FROM "memes_actions_tg_bot" '
            'WHERE "User_ID"=? AND "Chat_ID"=?', uid, cid).fetchone()
        return row[0] if row else None
    def set_action(self, uid, cid, aid):
        #print(uid, cid, aid)
        self.cx.cursor().execute(
            'INSERT INTO "memes_actions_tg_bot" ("Action_ID","User_ID","Chat_ID") '
            'VALUES (?,?,?) ON CONFLICT ("User_ID","Chat_ID") DO UPDATE '
            'SET "Action_ID"=EXCLUDED."Action_ID", "date_time_action" = NOW()',
            aid, uid, cid)
        self.cx.commit()
    def clear_lock(self, uid, cid):
        self.cx.cursor().execute(
            'DELETE FROM "memes_actions_tg_bot" WHERE "User_ID"=? AND "Chat_ID"=?',
            uid, cid)
        self.cx.commit()
    # language
    def lang(self, uid, cid):
        if cid:
            row = self.cx.cursor().execute(
                'SELECT "LANG_ID" FROM "memes_chat_list" WHERE "CHAT_ID_TELEGRAM"=?', cid
            ).fetchone()
            if row: return row[0]
        row = self.cx.cursor().execute(
            'SELECT "LANG_ID" FROM "memes_user_list" WHERE "USER_ID_TELEGRAM"=?', uid
        ).fetchone()
        return row[0] if row else LANG_EN
    # registration
    def is_registered(self, uid, cid):
        if cid:
            return self.cx.cursor().execute(
                'SELECT 1 FROM "memes_chat_list" WHERE "CHAT_ID_TELEGRAM"=?', cid
            ).fetchone() is not None
        return self.cx.cursor().execute(
            'SELECT 1 FROM "memes_user_list" WHERE "USER_ID_TELEGRAM"=?', uid
        ).fetchone() is not None
    def register(self, uid, cid, lang_id):
        if cid:
            self.cx.cursor().execute(
                'INSERT INTO "memes_chat_list" ("CHAT_ID_TELEGRAM","LANG_ID") VALUES(?,?)',
                cid, lang_id)
        else:
            self.cx.cursor().execute(
                'INSERT INTO "memes_user_list" ("USER_ID_TELEGRAM","LANG_ID") VALUES(?,?)',
                uid, lang_id)
        self.cx.commit()

# ─── meme sending helper ----------------------------------------------------
async def send_meme(ctx, uid, cid, lang, keywords=None):
    """
    Fetches a meme in a background thread, prunes cache,
    and sends the image without blocking the event‑loop.
    """
    lang_arg = "rus" if lang == LANG_RU else "eng"

    loop = asyncio.get_running_loop()
    img_path: Path = await loop.run_in_executor(
        None,
        _fetch_sync,           # the blocking function
        keywords, lang_arg,
        uid if cid == 0 else None,
        cid  if cid != 0 else None
    )

    # ---- prune cache to newest 500 files ---------------------------
    files = sorted(
        (p for p in mf.DOWNLOAD_DIR.iterdir() if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    for old in files[500:]:
        try:
            old.unlink()
        except Exception as e:
            log.warning("Delete %s failed: %s", old, e)

    # ---- send ------------------------------------------------------
    await ctx.bot.send_chat_action(chat_id=cid or uid,
                                   action=constants.ChatAction.UPLOAD_PHOTO)
    with img_path.open("rb") as f:
        await ctx.bot.send_photo(chat_id=cid or uid,
                                 photo=f,
                                 write_timeout=30)

# ─── handlers ---------------------------------------------------------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cid = 0 if update.effective_chat.type == "private" else update.effective_chat.id
    db = BotDB()
    try:
        lang = db.lang(uid, cid)
        ctx.user_data["lang"] = lang
        ctx.user_data["lang_meme"] = 1 if lang == 2 else 2
        if db.lock_exists(uid, cid):
            await update.message.reply_text(text_lock(lang))
            return
        db.set_action(uid, cid, 1)
        await update.message.reply_text(text_start(lang), reply_markup=kb_start(lang))
    finally: db.close_all()

async def get_meme(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    #q = update.callback_query; await q.answer()
    uid = update.effective_user.id
    cid = 0 if update.effective_chat.type == "private" else update.effective_chat.id
    db = BotDB()
    try:
        lang = db.lang(uid, cid)
        ctx.user_data["lang"] = lang
        ctx.user_data["lang_meme"] = 1 if lang == 2 else 2
        lang_meme = ctx.user_data["lang_meme"]
        if not db.is_registered(uid, cid):
            await update.message.reply_text(not_yet_registered(lang))
            db.set_action(uid, cid, 0)
            return
        if db.lock_exists(uid, cid):
            await update.message.reply_text(text_lock(lang))
            return
        db.set_action(uid, cid, 2)
        await update.message.reply_text(text_choose_mode(lang),
                                          reply_markup=kb_mode(lang_meme))
    finally: db.close_all()

async def get_any_meme(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    #q = update.callback_query; await q.answer()
    uid = update.effective_user.id
    cid = 0 if update.effective_chat.type == "private" else update.effective_chat.id
    db = BotDB()
    try:
        lang = db.lang(uid, cid)
        ctx.user_data["lang"] = lang
        ctx.user_data["lang_meme"] = 1 if lang == 2 else 2
        lang_meme = ctx.user_data["lang_meme"]
        if not db.is_registered(uid, cid):
            await update.message.reply_text(not_yet_registered(lang))
            db.set_action(uid, cid, 0)
            return
        if db.lock_exists(uid, cid):
            await update.message.reply_text(text_lock(lang))
            return
        await send_meme(ctx, uid, cid, 1 if lang_meme == 2 else 2)
        db.set_action(uid, cid, 0)
    finally: db.close_all()

async def cb_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    cid = 0 if q.message.chat.type == "private" else q.message.chat.id
    db = BotDB()
    try:
        lang = ctx.user_data.get("lang", db.lang(uid, cid))
        try:
            lang_meme = ctx.user_data["lang_meme"]
        except:
            lang_meme = 1 if lang == 2 else 2
        if not db.lock_exists(uid, cid):
            #await q.edit_message_text(text_lock(lang)); return
            #print('TYTAAAAA')
            await q.answer(text_lock(lang), show_alert=True)
            return

        # language toggle
        if q.data == BTN_LANG:
            lang = LANG_RU if lang == LANG_EN else LANG_EN
            ctx.user_data["lang"] = lang
            await q.edit_message_text(text_start(lang), reply_markup=kb_start(lang))
            return

        # cancel
        if q.data == BTN_CANCEL:
            db.clear_lock(uid, cid)
            await q.edit_message_text("Cancelled."); return

        # register
        if q.data == BTN_REG:
            if db.is_registered(uid, cid):
                await q.edit_message_text(text_registered(lang),
                                          reply_markup=kb_start(lang))
            else:
                db.register(uid, cid, lang)
                await q.edit_message_text(text_reg_ok(lang),
                                          reply_markup=kb_start(lang))
            return

        # GET MEME
        if q.data == BTN_GET:
            if db.is_registered(uid, cid):
                db.set_action(uid, cid, 2)          # select mode
                await q.edit_message_text(text_choose_mode(lang),
                                          reply_markup=kb_mode(lang_meme))
            else:
                await q.edit_message_text(not_yet_registered(lang),
                                          reply_markup=kb_start(lang))
            return

        # AnyMeme
        if q.data == BTN_ANY:
            if db.action_id(uid, cid) != 2:
                await q.answer(text_lock(lang)); return
            await send_meme(ctx, uid, cid, 1 if lang_meme == 2 else 2)
            db.set_action(uid, cid, 0)
            return

        # ByKeyWords
        if q.data == BTN_KEY:
            if db.action_id(uid, cid) != 2:
                await q.answer(text_lock(lang)); return
            db.set_action(uid, cid, 3)
            await q.edit_message_text(text_kw_prompt(lang));
            #db.set_action(uid, cid, 0)
            return

        # language_meme toggle
        if q.data == BTN_LANG_MEME:
            ctx.user_data["lang_meme"] = 1 if lang_meme==2 else 2
            lang_meme = ctx.user_data["lang_meme"]
            await q.edit_message_reply_markup(reply_markup=kb_mode(lang_meme))
            return

    finally: db.close_all()

async def txt_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cid = 0 if update.effective_chat.type == "private" else update.effective_chat.id
    db = BotDB()
    try:
        if db.action_id(uid, cid) != 3: return   # not waiting for keywords
        lang = ctx.user_data.get("lang", db.lang(uid, cid))
        try:
            lang_meme = 1 if ctx.user_data["lang_meme"] == 2 else 2 
        except:
            lang_meme = lang 
        kw = update.message.text.strip()
        await send_meme(ctx, uid, cid, lang_meme, kw)
        db.set_action(uid, cid, 0)
    finally:
        db.close_all()
        #pass
# ─── main -------------------------------------------------------------------
def main():
    token = 'REMOVED'          # export or .env
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("get_meme", get_meme))
    app.add_handler(CommandHandler("get_any_meme", get_any_meme))
    app.add_handler(CallbackQueryHandler(cb_query))
    app.add_handler(MessageHandler(~filters.COMMAND, txt_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
