import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "usdt_bot.db")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "15"))
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY", "").strip()
USDT_TRC20_CONTRACT = os.getenv("USDT_TRC20_CONTRACT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t").strip()

TRONGRID_URL = "https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("usdt-bot")

if not BOT_TOKEN:
    raise RuntimeError("Thiếu BOT_TOKEN. Hãy thêm BOT_TOKEN trong Railway Variables hoặc file .env")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def short_addr(addr: str) -> str:
    if not addr:
        return "N/A"
    return addr[:6] + "..." + addr[-6:] if len(addr) > 14 else addr


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        energy_total INTEGER DEFAULT 0,
        energy_used INTEGER DEFAULT 0,
        balance_usdt TEXT DEFAULT '0',
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS watched_wallets (
        address TEXT PRIMARY KEY,
        label TEXT,
        chat_id INTEGER NOT NULL,
        added_by INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS seen_transactions (
        txid TEXT PRIMARY KEY,
        wallet_address TEXT NOT NULL,
        direction TEXT NOT NULL,
        amount_usdt TEXT NOT NULL,
        from_address TEXT,
        to_address TEXT,
        block_time TEXT,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS energy_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        amount INTEGER NOT NULL,
        note TEXT,
        created_at TEXT NOT NULL
    )
    """)
    conn.commit()
    conn.close()


def ensure_user(update: Update) -> None:
    user = update.effective_user
    if not user:
        return
    conn = get_db()
    conn.execute(
        """
        INSERT INTO users (telegram_id, username, first_name, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name
        """,
        (user.id, user.username or "", user.first_name or "", now_text()),
    )
    conn.commit()
    conn.close()


def get_user_energy(telegram_id: int) -> Tuple[int, int, int]:
    conn = get_db()
    row = conn.execute("SELECT energy_total, energy_used FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    conn.close()
    if not row:
        return 0, 0, 0
    total = int(row["energy_total"] or 0)
    used = int(row["energy_used"] or 0)
    return total, used, max(total - used, 0)


def add_energy_log(telegram_id: int, action: str, amount: int, note: str = "") -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO energy_logs (telegram_id, action, amount, note, created_at) VALUES (?, ?, ?, ?, ?)",
        (telegram_id, action, amount, note, now_text()),
    )
    conn.commit()
    conn.close()


def parse_amount(value: str) -> Optional[Decimal]:
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    await update.message.reply_text(
        "🤖 USDT Transfer Manager đã hoạt động!\n\n"
        "Gõ /help để xem lệnh."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    await update.message.reply_text(
        "📌 DANH SÁCH LỆNH\n\n"
        "/balance - Xem số dư\n"
        "/energy - Xem lượt chuyển còn lại\n"
        "/buy - Xem gói mua lượt chuyển\n"
        "/useenergy - Mô phỏng dùng 1 lượt chuyển\n"
        "/history - Lịch sử gần nhất\n"
        "/tx <hash> - Tra cứu giao dịch đã lưu\n\n"
        "👑 Admin:\n"
        "/watch_add <địa_chỉ_ví> - Thêm ví theo dõi\n"
        "/watch_remove <địa_chỉ_ví> - Xóa ví theo dõi\n"
        "/watch_list - Xem ví đang theo dõi\n"
        "/addtimes <telegram_id> <số_lần> - Cộng lượt\n"
        "/minustimes <telegram_id> <số_lần> - Trừ lượt\n"
        "/setbalance <telegram_id> <số_usdt> - Đặt số dư giả lập\n"
        "/restart - Khởi động lại bot"
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    user_id = update.effective_user.id
    conn = get_db()
    row = conn.execute("SELECT balance_usdt FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    conn.close()
    bal = row["balance_usdt"] if row else "0"
    await update.message.reply_text(f"💵 Số dư hiện tại:\n{bal} USDT\n\n🌐 Mạng: TRC20")


async def energy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    user_id = update.effective_user.id
    total, used, remain = get_user_energy(user_id)
    status = "Đang hoạt động" if remain > 0 else "Hết lượt"
    await update.message.reply_text(
        "⚡ THÔNG TIN NĂNG LƯỢNG CHUYỂN\n\n"
        f"👤 ID: {user_id}\n"
        f"✅ Trạng thái: {status}\n"
        f"🔄 Tổng số lần: {total} lần\n"
        f"📤 Đã sử dụng: {used} lần\n"
        f"🟢 Số lần còn lại: {remain} lần\n\n"
        "💡 Còn lượt thì mỗi lần chuyển sẽ trừ 1 lượt."
    )


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    await update.message.reply_text(
        "🛒 GÓI MUA LƯỢT CHUYỂN\n\n"
        "1️⃣ 30 lần chuyển\n"
        "2️⃣ 50 lần chuyển\n"
        "3️⃣ 100 lần chuyển\n"
        "4️⃣ VIP Unlimited\n\n"
        "Hiện bản này admin sẽ cộng lượt bằng:\n"
        "/addtimes <telegram_id> <số_lần>"
    )


async def use_energy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    user_id = update.effective_user.id
    total, used, remain = get_user_energy(user_id)
    if remain <= 0:
        await update.message.reply_text("❌ 转账失败\n剩余次数不足\n请购买套餐")
        return
    conn = get_db()
    conn.execute("UPDATE users SET energy_used = energy_used + 1 WHERE telegram_id=?", (user_id,))
    conn.commit()
    conn.close()
    add_energy_log(user_id, "use", 1, "Dùng 1 lượt chuyển")
    _, _, new_remain = get_user_energy(user_id)
    await update.message.reply_text(f"✅ 能量发送成功\n剩余转账次数:{new_remain}")


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    conn = get_db()
    txs = conn.execute(
        "SELECT * FROM seen_transactions ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    logs = conn.execute(
        "SELECT * FROM energy_logs WHERE telegram_id=? ORDER BY created_at DESC LIMIT 10",
        (update.effective_user.id,),
    ).fetchall()
    conn.close()
    lines = ["📜 LỊCH SỬ GẦN NHẤT"]
    if txs:
        lines.append("\n💰 Giao dịch USDT:")
        for r in txs[:5]:
            sign = "+" if r["direction"] == "in" else "-"
            lines.append(f"{r['created_at']} | {sign}{r['amount_usdt']} USDT | {r['txid'][:12]}...")
    if logs:
        lines.append("\n⚡ Lượt chuyển:")
        for r in logs[:5]:
            lines.append(f"{r['created_at']} | {r['action']} | {r['amount']} | {r['note'] or ''}")
    if len(lines) == 1:
        lines.append("Chưa có dữ liệu.")
    await update.message.reply_text("\n".join(lines))


async def tx_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if not context.args:
        await update.message.reply_text("Cách dùng: /tx <hash>")
        return
    txid = context.args[0].strip()
    conn = get_db()
    row = conn.execute("SELECT * FROM seen_transactions WHERE txid=?", (txid,)).fetchone()
    conn.close()
    if not row:
        await update.message.reply_text("Không tìm thấy giao dịch này trong database bot.")
        return
    sign = "+" if row["direction"] == "in" else "-"
    await update.message.reply_text(
        f"🔍 THÔNG TIN GIAO DỊCH\n\n"
        f"Hash: {row['txid']}\n"
        f"Loại: {'Tiền vào' if row['direction']=='in' else 'Tiền ra'}\n"
        f"Số tiền: {sign}{row['amount_usdt']} USDT\n"
        f"From: {row['from_address']}\n"
        f"To: {row['to_address']}\n"
        f"Thời gian block: {row['block_time']}"
    )


async def addtimes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Chỉ admin được dùng lệnh này.")
        return
    if len(context.args) != 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text("Cách dùng: /addtimes <telegram_id> <số_lần>")
        return
    target = int(context.args[0])
    amount = int(context.args[1])
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO users (telegram_id, created_at) VALUES (?, ?)",
        (target, now_text()),
    )
    conn.execute("UPDATE users SET energy_total = energy_total + ? WHERE telegram_id=?", (amount, target))
    conn.commit()
    conn.close()
    add_energy_log(target, "add", amount, f"Admin {update.effective_user.id} cộng lượt")
    await update.message.reply_text(f"✅ Đã cộng {amount} lượt cho user {target}.")


async def minustimes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Chỉ admin được dùng lệnh này.")
        return
    if len(context.args) != 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text("Cách dùng: /minustimes <telegram_id> <số_lần>")
        return
    target = int(context.args[0])
    amount = int(context.args[1])
    conn = get_db()
    row = conn.execute("SELECT energy_total FROM users WHERE telegram_id=?", (target,)).fetchone()
    if not row:
        await update.message.reply_text("Không tìm thấy user này.")
        conn.close()
        return
    new_total = max(int(row["energy_total"] or 0) - amount, 0)
    conn.execute("UPDATE users SET energy_total=? WHERE telegram_id=?", (new_total, target))
    conn.commit()
    conn.close()
    add_energy_log(target, "minus", amount, f"Admin {update.effective_user.id} trừ lượt")
    await update.message.reply_text(f"✅ Đã trừ {amount} lượt của user {target}.")


async def setbalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Chỉ admin được dùng lệnh này.")
        return
    if len(context.args) != 2 or not context.args[0].isdigit() or parse_amount(context.args[1]) is None:
        await update.message.reply_text("Cách dùng: /setbalance <telegram_id> <số_usdt>")
        return
    target = int(context.args[0])
    amount = str(parse_amount(context.args[1]))
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (telegram_id, created_at) VALUES (?, ?)", (target, now_text()))
    conn.execute("UPDATE users SET balance_usdt=? WHERE telegram_id=?", (amount, target))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Đã đặt số dư user {target}: {amount} USDT")


async def watch_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Chỉ admin được dùng lệnh này.")
        return
    if not context.args:
        await update.message.reply_text("Cách dùng: /watch_add <địa_chỉ_ví_TRC20> [nhãn]")
        return
    address = context.args[0].strip()
    label = " ".join(context.args[1:]) if len(context.args) > 1 else "Ví USDT"
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO watched_wallets (address, label, chat_id, added_by, created_at) VALUES (?, ?, ?, ?, ?)",
        (address, label, update.effective_chat.id, update.effective_user.id, now_text()),
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Đã thêm ví theo dõi:\n{address}\nNhãn: {label}")


async def watch_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Chỉ admin được dùng lệnh này.")
        return
    if not context.args:
        await update.message.reply_text("Cách dùng: /watch_remove <địa_chỉ_ví_TRC20>")
        return
    address = context.args[0].strip()
    conn = get_db()
    conn.execute("DELETE FROM watched_wallets WHERE address=?", (address,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Đã xóa ví theo dõi:\n{address}")


async def watch_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Chỉ admin được dùng lệnh này.")
        return
    conn = get_db()
    rows = conn.execute("SELECT * FROM watched_wallets ORDER BY created_at DESC").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Chưa có ví nào đang theo dõi.")
        return
    lines = ["👛 VÍ ĐANG THEO DÕI"]
    for r in rows:
        lines.append(f"- {r['label']}: {r['address']}")
    await update.message.reply_text("\n".join(lines))


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Chỉ admin được restart bot.")
        return
    await update.message.reply_text("🔄 Bot đang restart...")
    os._exit(0)


def normalize_trc20_tx(item: dict, watched_address: str) -> Optional[dict]:
    txid = item.get("transaction_id") or item.get("txID") or item.get("hash")
    from_addr = item.get("from") or item.get("from_address") or ""
    to_addr = item.get("to") or item.get("to_address") or ""
    raw_value = item.get("value") or "0"
    decimals = int(item.get("token_info", {}).get("decimals", 6) or 6)
    try:
        amount = Decimal(str(raw_value)) / (Decimal(10) ** decimals)
    except Exception:
        amount = Decimal("0")
    ts = item.get("block_timestamp")
    block_time = ""
    if ts:
        try:
            block_time = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            block_time = str(ts)
    direction = "in" if to_addr.lower() == watched_address.lower() else "out" if from_addr.lower() == watched_address.lower() else "other"
    if not txid or direction == "other":
        return None
    return {
        "txid": txid,
        "direction": direction,
        "amount": str(amount.normalize()),
        "from": from_addr,
        "to": to_addr,
        "block_time": block_time,
    }


async def fetch_trc20_transactions(address: str) -> List[dict]:
    params = {
        "only_confirmed": "true",
        "limit": "20",
        "order_by": "block_timestamp,desc",
        "contract_address": USDT_TRC20_CONTRACT,
    }
    headers = {}
    if TRONGRID_API_KEY:
        headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY
    url = TRONGRID_URL.format(address=address)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data.get("data", [])


async def monitor_wallets(app: Application) -> None:
    await asyncio.sleep(5)
    logger.info("Wallet monitor started. Poll every %s seconds", POLL_SECONDS)
    while True:
        try:
            conn = get_db()
            wallets = conn.execute("SELECT * FROM watched_wallets").fetchall()
            conn.close()
            for wallet in wallets:
                address = wallet["address"]
                try:
                    items = await fetch_trc20_transactions(address)
                except Exception as e:
                    logger.warning("Fetch error %s: %s", address, e)
                    continue
                for item in reversed(items):
                    tx = normalize_trc20_tx(item, address)
                    if not tx:
                        continue
                    conn = get_db()
                    exists = conn.execute("SELECT 1 FROM seen_transactions WHERE txid=?", (tx["txid"],)).fetchone()
                    if exists:
                        conn.close()
                        continue
                    conn.execute(
                        """
                        INSERT INTO seen_transactions
                        (txid, wallet_address, direction, amount_usdt, from_address, to_address, block_time, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (tx["txid"], address, tx["direction"], tx["amount"], tx["from"], tx["to"], tx["block_time"], now_text()),
                    )
                    conn.commit()
                    conn.close()
                    if tx["direction"] == "in":
                        text = (
                            "🔔 THÔNG BÁO TIỀN VÀO\n\n"
                            f"💰 +{tx['amount']} USDT\n"
                            f"👛 Ví: {wallet['label']}\n"
                            f"👤 Người gửi: {short_addr(tx['from'])}\n"
                            f"🕒 {tx['block_time']}\n"
                            f"📦 Hash:\n{tx['txid']}"
                        )
                    else:
                        text = (
                            "📤 THÔNG BÁO TIỀN RA\n\n"
                            f"💰 -{tx['amount']} USDT\n"
                            f"👛 Ví: {wallet['label']}\n"
                            f"👤 Người nhận: {short_addr(tx['to'])}\n"
                            f"🕒 {tx['block_time']}\n"
                            f"📦 Hash:\n{tx['txid']}"
                        )
                    await app.bot.send_message(chat_id=wallet["chat_id"], text=text)
        except Exception as e:
            logger.exception("Monitor loop error: %s", e)
        await asyncio.sleep(POLL_SECONDS)


async def post_init(app: Application) -> None:
    init_db()
    app.create_task(monitor_wallets(app))


def main() -> None:
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("energy", energy))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("useenergy", use_energy))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("tx", tx_lookup))
    app.add_handler(CommandHandler("addtimes", addtimes))
    app.add_handler(CommandHandler("minustimes", minustimes))
    app.add_handler(CommandHandler("setbalance", setbalance))
    app.add_handler(CommandHandler("watch_add", watch_add))
    app.add_handler(CommandHandler("watch_remove", watch_remove))
    app.add_handler(CommandHandler("watch_list", watch_list))
    app.add_handler(CommandHandler("restart", restart))
    logger.info("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
