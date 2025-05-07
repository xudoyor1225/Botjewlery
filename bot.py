import logging
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, \
    ReplyKeyboardRemove, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
)
import telegram.error
from datetime import datetime
from dotenv import load_dotenv
import os
load_dotenv()


# Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Global
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DB_NAME = "jewelry_bot.db"

# Conversation States (Admin)
(ASK_CATEGORY_NAME,
 ASK_CATEGORY_EDIT_NAME,
 SELECT_PRODUCT_CATEGORY,
 ASK_PRODUCT_NAME,
 ASK_PRODUCT_DESCRIPTION,
 ASK_PRODUCT_PRICE,
 ASK_PRODUCT_IMAGE,
 ASK_EDIT_PRODUCT_NEW_NAME,
 ASK_EDIT_PRODUCT_NEW_DESC,
 ASK_EDIT_PRODUCT_NEW_PRICE,
 ASK_EDIT_PRODUCT_NEW_IMAGE,
 ASK_EDIT_PRODUCT_NEW_CATEGORY,
 ) = range(12)


EDIT_PRICE_ENTRY_PRODUCT_ID, EDIT_PRICE_ASK_NEW_PRICE = range(12, 14)


# --- Database ---
def db_query(query, params=()):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute(query, params)
    conn.commit()
    last_row_id = cursor.lastrowid
    conn.close()
    return last_row_id


def db_fetch_one(query, params=()):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute(query, params)
    result = cursor.fetchone()
    conn.close()
    return result


def db_fetch_all(query, params=()):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute(query, params)
    result = cursor.fetchall()
    conn.close()
    return result


def alter_table_add_column_if_not_exists(table_name, column_name, column_type):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [info[1] for info in cursor.fetchall()]
        if column_name not in columns:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
            conn.commit()
            logger.info(f"'{table_name}' jadvaliga '{column_name}' ustuni qo'shildi.")
        else:
            logger.debug(f"'{column_name}' ustuni '{table_name}' jadvalida allaqachon mavjud.")
    except sqlite3.Error as e:
        if "duplicate column name" in str(e).lower():
            logger.debug(f"'{column_name}' ustuni '{table_name}' jadvalida allaqachon mavjud (ALTER TABLE xatoligi).")
        else:
            logger.error(f"'{table_name}' jadvalini o'zgartirishda xatolik ({column_name}): {e}")
    finally:
        conn.close()


def setup_database():
    db_query("""
             CREATE TABLE IF NOT EXISTS categories
             (
                 id
                 INTEGER
                 PRIMARY
                 KEY
                 AUTOINCREMENT,
                 name
                 TEXT
                 UNIQUE
                 NOT
                 NULL
             )""")
    db_query("""
             CREATE TABLE IF NOT EXISTS products
             (
                 id
                 INTEGER
                 PRIMARY
                 KEY
                 AUTOINCREMENT,
                 category_id
                 INTEGER,
                 name
                 TEXT
                 NOT
                 NULL,
                 description
                 TEXT,
                 price
                 REAL
                 NOT
                 NULL,
                 image_file_id
                 TEXT,
                 FOREIGN
                 KEY
             (
                 category_id
             ) REFERENCES categories
             (
                 id
             ) ON DELETE SET NULL
                 )""")
    db_query("""
             CREATE TABLE IF NOT EXISTS orders
             (
                 id
                 INTEGER
                 PRIMARY
                 KEY
                 AUTOINCREMENT,
                 user_id
                 INTEGER
                 NOT
                 NULL,
                 user_username
                 TEXT,
                 product_id
                 INTEGER,
                 phone_number
                 TEXT
                 NOT
                 NULL,
                 timestamp
                 DATETIME
                 DEFAULT
                 CURRENT_TIMESTAMP,
                 FOREIGN
                 KEY
             (
                 product_id
             ) REFERENCES products
             (
                 id
             ) ON DELETE SET NULL
                 )""")
    alter_table_add_column_if_not_exists("orders", "product_name_at_order", "TEXT")
    alter_table_add_column_if_not_exists("orders", "product_price_at_order", "REAL")
    db_query("""
             CREATE TABLE IF NOT EXISTS users
             (
                 id
                 INTEGER
                 PRIMARY
                 KEY,
                 first_name
                 TEXT,
                 last_name
                 TEXT,
                 username
                 TEXT
                 UNIQUE
             )""")
    logger.info("Ma'lumotlar bazasi sozlandi (kerak bo'lsa, 'orders' jadvali yangilandi).")


# --- Helpers ---
def is_admin(update: Update) -> bool:
    if not update.effective_user:
        return False
    return update.effective_user.id == ADMIN_ID


async def save_user_info(user_obj):
    if not user_obj: return
    try:
        db_query(
            "INSERT OR IGNORE INTO users (id, first_name, last_name, username) VALUES (?, ?, ?, ?)",
            (user_obj.id, user_obj.first_name, user_obj.last_name, user_obj.username)
        )
        current_db_user = db_fetch_one("SELECT first_name, last_name, username FROM users WHERE id = ?", (user_obj.id,))
        if current_db_user and \
                (current_db_user[0] != user_obj.first_name or \
                 current_db_user[1] != user_obj.last_name or \
                 (current_db_user[2] != user_obj.username and user_obj.username is not None)):
            db_query(
                "UPDATE users SET first_name = ?, last_name = ?, username = ? WHERE id = ?",
                (user_obj.first_name, user_obj.last_name, user_obj.username, user_obj.id)
            )
    except Exception as e:
        logger.error(f"Foydalanuvchi ma'lumotlarini saqlashda xatolik ({user_obj.id if user_obj else 'N/A'}): {e}")


async def send_or_edit_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str,
                               reply_markup=None, message_id_to_edit: int = None,
                               parse_mode='HTML', photo_file_id=None, delete_previous=False):
    if delete_previous and message_id_to_edit:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id_to_edit)
            logger.debug(f"Oldindan mavjud xabar (ID: {message_id_to_edit}) o'chirildi.")
            message_id_to_edit = None
        except telegram.error.BadRequest:
            logger.debug(f"Oldindan mavjud xabar (ID: {message_id_to_edit}) o'chirilmadi (ehtimol allaqachon yo'q).")
            message_id_to_edit = None
        except Exception as e_del:
            logger.warning(f"Oldindan mavjud xabarni o'chirishda xatolik: {e_del}")
            pass

    try:
        if message_id_to_edit:
            if photo_file_id:
                logger.debug(
                    f"Xabarni (ID: {message_id_to_edit}) rasm bilan tahrirlash: photo={photo_file_id}, caption={text[:30]}...")
                await context.bot.edit_message_media(
                    chat_id=chat_id, message_id=message_id_to_edit,
                    media=InputMediaPhoto(media=photo_file_id, caption=text, parse_mode=parse_mode),
                    reply_markup=reply_markup
                )
            else:
                logger.debug(f"Xabarni (ID: {message_id_to_edit}) matn bilan tahrirlash: text={text[:30]}...")
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id_to_edit, text=text,
                    reply_markup=reply_markup, parse_mode=parse_mode
                )
        else:
            if photo_file_id:
                logger.debug(f"Yangi xabarni rasm bilan yuborish: photo={photo_file_id}, caption={text[:30]}...")
                await context.bot.send_photo(
                    chat_id=chat_id, photo=photo_file_id, caption=text,
                    reply_markup=reply_markup, parse_mode=parse_mode
                )
            else:
                logger.debug(f"Yangi xabarni matn bilan yuborish: text={text[:30]}...")
                await context.bot.send_message(
                    chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode
                )
    except telegram.error.BadRequest as e:
        if "message to edit not found" in str(e).lower() or \
                "message can't be edited" in str(e).lower() or \
                "there is no text in the message to edit" in str(e).lower():
            logger.warning(f"Xabarni tahrirlab bo'lmadi ({e}), yangisi yuboriladi.")
            if photo_file_id:
                await context.bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=text,
                                             reply_markup=reply_markup, parse_mode=parse_mode)
            else:
                await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup,
                                               parse_mode=parse_mode)
        elif "message is not modified" in str(e).lower():
            logger.debug(f"Xabar o'zgartirilmadi (message is not modified): {text[:30]}")
            pass
        else:
            logger.error(f"send_or_edit_message da (BadRequest): {e} - Text: {text[:100]}")
            final_text = text + ("\n(Xabarni yangilashda muammo yuz berdi)" if message_id_to_edit else "")
            if photo_file_id:
                await context.bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=final_text,
                                             reply_markup=reply_markup, parse_mode=parse_mode)
            else:
                await context.bot.send_message(chat_id=chat_id, text=final_text, reply_markup=reply_markup,
                                               parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"send_or_edit_message da kutilmagan xatolik: {e} (Text: {text[:50]})")
        final_text = text + ("\n(Xabarni yangilashda jiddiy muammo yuz berdi)" if message_id_to_edit else "")
        try:
            if photo_file_id:
                await context.bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=final_text,
                                             reply_markup=reply_markup, parse_mode=parse_mode)
            else:
                await context.bot.send_message(chat_id=chat_id, text=final_text, reply_markup=reply_markup,
                                               parse_mode=parse_mode)
        except Exception as e_fallback:
            logger.critical(f"send_or_edit_message da YAKUNIY fallback xatoligi: {e_fallback}")


# --- User handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await save_user_info(user)
    welcome_text = (f"Assalomu alaykum, {user.mention_html()}!\n"
                    f"Zargarlik buyumlari do'konimizga xush kelibsiz!")
    keyboard = [[InlineKeyboardButton("🛍️ Mahsulotlarni ko'rish", callback_data="view_categories")]]
    if is_admin(update):
        keyboard.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_html(welcome_text, reply_markup=reply_markup)
    elif update.callback_query:
        await send_or_edit_message(context, update.callback_query.message.chat_id, welcome_text, reply_markup,
                                   update.callback_query.message.message_id, delete_previous=True)


async def view_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await save_user_info(query.from_user)
    categories = db_fetch_all("SELECT id, name FROM categories ORDER BY name")
    text_to_send = "Quyidagi kategoriyalardan birini tanlang:"
    if not categories:
        text_to_send = "Hozircha kategoriyalar mavjud emas."
        keyboard_buttons = [[InlineKeyboardButton("⬅️ Orqaga (Bosh menyu)", callback_data="main_menu")]]
    else:
        keyboard_buttons = [[InlineKeyboardButton(cat_name, callback_data=f"category_{cat_id}")] for cat_id, cat_name in
                            categories]
        keyboard_buttons.append([InlineKeyboardButton("⬅️ Orqaga (Bosh menyu)", callback_data="main_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard_buttons)
    await send_or_edit_message(context, query.message.chat_id, text_to_send, reply_markup, query.message.message_id,
                               delete_previous=True)


async def show_products_in_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await save_user_info(query.from_user)
    category_id = int(query.data.split("_")[1])
    context.user_data['current_category_id'] = category_id
    context.user_data['current_product_index'] = 0
    products = db_fetch_all(
        "SELECT id, name, price, image_file_id, description FROM products WHERE category_id = ? ORDER BY name",
        (category_id,))
    if not products:
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Kategoriyalarga qaytish", callback_data="view_categories")]])
        await send_or_edit_message(context, query.message.chat_id, "Bu kategoriyada hozircha mahsulotlar mavjud emas.",
                                   reply_markup, query.message.message_id, delete_previous=True)
        return
    context.user_data['products_in_category'] = products
    await display_product(update, context, query.message.chat_id, edit_message=False,
                          delete_previous_message_id=query.message.message_id)


async def display_product(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                          message_id_to_edit: int = None, edit_message: bool = False,
                          delete_previous_message_id: int = None):
    products = context.user_data.get('products_in_category', [])
    current_index = context.user_data.get('current_product_index', 0)
    if not products or current_index >= len(products):
        logger.warning("display_product: Mahsulotlar ro'yxati bo'sh yoki indeks chegaradan tashqarida.")
        if update.callback_query:
            await send_or_edit_message(context, chat_id,
                                       "Mahsulot topilmadi yoki ro'yxatda xatolik. Kategoriyalarga qayting.",
                                       reply_markup=InlineKeyboardMarkup(
                                           [[InlineKeyboardButton("📜 Kategoriyalarga qaytish",
                                                                  callback_data="view_categories")]]),
                                       message_id_to_edit=update.callback_query.message.message_id,
                                       delete_previous=True)
        else:
            await context.bot.send_message(chat_id, "Mahsulot topilmadi. /start")
        return

    product = products[current_index]
    product_id, name, price, image_file_id, description = product
    caption = f"<b>{name}</b>\n"
    if description: caption += f"<i>{description}</i>\n"
    caption += f"\nNarxi: <b>{price:,.0f} so'm</b>"

    keyboard_nav = []
    row = []
    if current_index > 0: row.append(InlineKeyboardButton("⬅️ Oldingisi", callback_data="prev_product"))
    if current_index < len(products) - 1: row.append(InlineKeyboardButton("Keyingisi ➡️", callback_data="next_product"))
    if row: keyboard_nav.append(row)
    keyboard_nav.append([InlineKeyboardButton(f"🛍️ Sotib olish", callback_data=f"buy_{product_id}")])
    keyboard_nav.append([InlineKeyboardButton("📜 Kategoriyalarga qaytish", callback_data="view_categories")])
    reply_markup = InlineKeyboardMarkup(keyboard_nav)

    effective_message_id_for_editing = None
    delete_flag = False
    message_id_for_action = None

    if edit_message:
        if update.callback_query:
            effective_message_id_for_editing = update.callback_query.message.message_id
        elif message_id_to_edit:
            effective_message_id_for_editing = message_id_to_edit
        message_id_for_action = effective_message_id_for_editing
    elif delete_previous_message_id:
        delete_flag = True
        message_id_for_action = delete_previous_message_id

    await send_or_edit_message(context, chat_id, caption, reply_markup,
                               message_id_for_action if edit_message else None,
                               photo_file_id=image_file_id,
                               delete_previous=delete_flag and not edit_message)


async def next_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query;
    await query.answer();
    await save_user_info(query.from_user)
    current_index = context.user_data.get('current_product_index', 0)
    products_len = len(context.user_data.get('products_in_category', []))
    if current_index < products_len - 1:
        context.user_data['current_product_index'] += 1
        await display_product(update, context, query.message.chat_id, edit_message=True)
    else:
        await query.answer("Bu oxirgi mahsulot.")


async def prev_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query;
    await query.answer();
    await save_user_info(query.from_user)
    current_index = context.user_data.get('current_product_index', 0)
    if current_index > 0:
        context.user_data['current_product_index'] -= 1
        await display_product(update, context, query.message.chat_id, edit_message=True)
    else:
        await query.answer("Bu birinchi mahsulot.")


async def buy_product_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query;
    await query.answer();
    await save_user_info(query.from_user)
    product_id = int(query.data.split("_")[1])
    context.user_data['product_to_buy_id'] = product_id
    product = db_fetch_one("SELECT name FROM products WHERE id = ?", (product_id,))
    if not product:
        await send_or_edit_message(context, query.message.chat_id, "Mahsulot topilmadi.",
                                   message_id_to_edit=query.message.message_id, delete_previous=True)
        return

    await send_or_edit_message(context, query.message.chat_id,
                               f"<b>{product[0]}</b> uchun buyurtma berish uchun telefon raqamingizni yuboring...",
                               reply_markup=ReplyKeyboardMarkup.from_button(
                                   KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True),
                                   resize_keyboard=True, one_time_keyboard=True),
                               message_id_to_edit=query.message.message_id,
                               delete_previous=True, photo_file_id=None
                               )


async def process_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await save_user_info(user)
    product_id = context.user_data.get('product_to_buy_id')
    logger.info(f"process_contact: User {user.id} telefon raqamini yubordi. Product ID: {product_id}")

    if not product_id:
        logger.warning(f"process_contact: User {user.id} uchun product_id topilmadi.")
        await update.message.reply_text(
            "Xatolik: Qaysi mahsulotni sotib olmoqchi ekanligingiz aniqlanmadi. Iltimos, qaytadan boshlang.",
            reply_markup=ReplyKeyboardRemove()
        )
        await start_after_action(update, context)
        return

    phone_number = ""
    if update.message.contact:
        phone_number = update.message.contact.phone_number
        if not phone_number.startswith('+'):
            phone_number = '+' + phone_number
    elif update.message.text:
        cleaned_text = "".join(filter(str.isdigit, update.message.text))
        if update.message.text.startswith("+998") and len(cleaned_text) == 12:
            phone_number = "+" + cleaned_text
        elif len(cleaned_text) == 9 and cleaned_text.startswith(('9', '8', '7', '6', '5', '3')):
            phone_number = "+998" + cleaned_text
        elif len(cleaned_text) == 12 and cleaned_text.startswith('998'):
            phone_number = "+" + cleaned_text
        else:
            logger.info(
                f"process_contact: User {user.id} noto'g'ri formatda telefon raqam kiritdi: {update.message.text}")
            await update.message.reply_text(
                "Telefon raqam noto'g'ri formatda. Iltimos, (+998xxxxxxxxx) formatida kiriting yoki tugmani bosing.",
                reply_markup=ReplyKeyboardMarkup.from_button(
                    KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True),
                    resize_keyboard=True, one_time_keyboard=True
                )
            )
            return

    if not phone_number:
        logger.warning(f"process_contact: User {user.id} uchun telefon raqam olinmadi.")
        await update.message.reply_text("Telefon raqam olinmadi. Iltimos, qaytadan urinib ko'ring.",
                                        reply_markup=ReplyKeyboardRemove())
        return

    logger.info(f"process_contact: User {user.id} telefon raqami: {phone_number}")

    product = db_fetch_one("SELECT name, price FROM products WHERE id = ?", (product_id,))
    if not product:
        logger.warning(f"process_contact: User {user.id} uchun mahsulot (ID: {product_id}) bazadan topilmadi.")
        await update.message.reply_text("Mahsulot topilmadi.", reply_markup=ReplyKeyboardRemove())
        await start_after_action(update, context)
        return

    product_name, product_price = product
    logger.info(
        f"process_contact: User {user.id} sotib olmoqchi bo'lgan mahsulot: {product_name}, Narxi: {product_price}")

    try:
        order_params = (user.id, user.username, product_id, product_name, product_price, phone_number)
        logger.info(f"process_contact: Buyurtmani bazaga yozish uchun parametrlar: {order_params}")
        db_query(
            "INSERT INTO orders (user_id, user_username, product_id, product_name_at_order, product_price_at_order, phone_number) VALUES (?, ?, ?, ?, ?, ?)",
            order_params
        )
        logger.info(
            f"process_contact: User {user.id} uchun buyurtma (Mahsulot ID: {product_id}) bazaga muvaffaqiyatli yozildi.")
        await update.message.reply_text(
            "✅ Rahmat! Buyurtmangiz qabul qilindi. Tez orada siz bilan bog'lanamiz.",
            reply_markup=ReplyKeyboardRemove()
        )

        admin_message = (
            f"📢 <b>Yangi buyurtma!</b>\n\n"
            f"👤 Mijoz: {user.mention_html()} (ID: <code>{user.id}</code>)\n"
            f"📞 Telefon: <code>{phone_number}</code>\n"
            f"🛍️ Mahsulot: {product_name}\n"
            f"💰 Narxi: {product_price:,.0f} so'm"
        )
        logger.info(f"process_contact: Adminga yuboriladigan xabar tayyorlandi: {admin_message[:100]}...")
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_message, parse_mode='HTML')
            logger.info(f"process_contact: Adminga ({ADMIN_ID}) yangi buyurtma haqida xabar muvaffaqiyatli yuborildi.")
        except telegram.error.BadRequest as e:
            logger.error(
                f"process_contact: Adminga xabar yuborishda BadRequest xatoligi: {e}. ADMIN_ID: {ADMIN_ID}. Bot adminga yozish huquqiga egami? Admin botni bloklamaganmi?")
        except Exception as e:
            logger.error(f"process_contact: Adminga xabar yuborishda kutilmagan xatolik: {e}")

    except Exception as e_db:
        logger.error(
            f"process_contact: Buyurtmani bazaga saqlashda yoki adminga xabar yuborishda umumiy xatolik: {e_db}")
        await update.message.reply_text(
            "❌ Buyurtmani qayta ishlashda xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring.",
            reply_markup=ReplyKeyboardRemove()
        )

    if 'product_to_buy_id' in context.user_data:
        del context.user_data['product_to_buy_id']
    await start_after_action(update, context)


async def start_after_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = "Bosh menyu:"
    keyboard = [[InlineKeyboardButton("🛍️ Mahsulotlarni ko'rish", callback_data="view_categories")]]
    current_user_is_admin = False
    if update.effective_user and update.effective_user.id == ADMIN_ID:
        current_user_is_admin = True

    if current_user_is_admin:
        keyboard.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=welcome_text, reply_markup=reply_markup,
                                   parse_mode='HTML')


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await save_user_info(query.from_user)
    welcome_text = (f"Assalomu alaykum, {query.from_user.mention_html()}!\n"
                    f"Zargarlik buyumlari do'konimizga xush kelibsiz!")
    keyboard = [[InlineKeyboardButton("🛍️ Mahsulotlarni ko'rish", callback_data="view_categories")]]
    if query.from_user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_or_edit_message(context, query.message.chat_id, welcome_text, reply_markup, query.message.message_id,
                               delete_previous=True)


# --- Admin Panel ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_to_save = update.callback_query.from_user if update.callback_query else update.effective_user
    await save_user_info(user_to_save)
    if not is_admin(update):
        sender = update.callback_query.message if update.callback_query else update.message
        await sender.reply_text("Sizda bu buyruq uchun ruxsat yo'q.");
        return
    if update.callback_query: await update.callback_query.answer()
    text = "Admin Paneliga xush kelibsiz! Quyidagi amallardan birini tanlang:"
    keyboard = [
        [InlineKeyboardButton("🗂️ Kategoriyalarni boshqarish", callback_data="admin_manage_categories")],
        [InlineKeyboardButton("➕ Kategoriya qo'shish", callback_data="admin_add_category_prompt")],
        [InlineKeyboardButton("📦 Mahsulot qo'shish", callback_data="admin_add_product_start")],
        [InlineKeyboardButton("📝 Mahsulotlarni boshqarish", callback_data="admin_manage_products_list")],
        [InlineKeyboardButton("📈 Buyurtmalarni ko'rish", callback_data="admin_view_orders")],
        [InlineKeyboardButton("🏠 Bosh menyuga qaytish", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg_to_handle = update.callback_query.message if update.callback_query else update.message
    await send_or_edit_message(context, msg_to_handle.chat_id, text, reply_markup,
                               msg_to_handle.message_id if update.callback_query else None,
                               delete_previous=bool(update.callback_query))


async def admin_panel_after_conv_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "Admin Panel:"
    keyboard = [
        [InlineKeyboardButton("🗂️ Kategoriyalarni boshqarish", callback_data="admin_manage_categories")],
        [InlineKeyboardButton("➕ Kategoriya qo'shish", callback_data="admin_add_category_prompt")],
        [InlineKeyboardButton("📦 Mahsulot qo'shish", callback_data="admin_add_product_start")],
        [InlineKeyboardButton("📝 Mahsulotlarni boshqarish", callback_data="admin_manage_products_list")],
        [InlineKeyboardButton("📈 Buyurtmalarni ko'rish", callback_data="admin_view_orders")],
        [InlineKeyboardButton("🏠 Bosh menyuga qaytish", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)


async def admin_panel_after_callback_action(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE,
                                            message_text_prefix=""):
    text = message_text_prefix + "\nAdmin Panel:" if message_text_prefix and message_text_prefix.strip() else "Admin Panel:"
    keyboard = [
        [InlineKeyboardButton("🗂️ Kategoriyalarni boshqarish", callback_data="admin_manage_categories")],
        [InlineKeyboardButton("➕ Kategoriya qo'shish", callback_data="admin_add_category_prompt")],
        [InlineKeyboardButton("📦 Mahsulot qo'shish", callback_data="admin_add_product_start")],
        [InlineKeyboardButton("📝 Mahsulotlarni boshqarish", callback_data="admin_manage_products_list")],
        [InlineKeyboardButton("📈 Buyurtmalarni ko'rish", callback_data="admin_view_orders")],
        [InlineKeyboardButton("🏠 Bosh menyuga qaytish", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_or_edit_message(context, query.message.chat_id, text, reply_markup, query.message.message_id,
                               delete_previous=True)


# --- Category Management (Admin) ---
async def admin_manage_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    categories = db_fetch_all("SELECT id, name FROM categories ORDER BY name")
    text = "Kategoriyalarni boshqarish:\n"
    keyboard = []
    if categories:
        for cat_id, cat_name in categories:
            keyboard.append([
                InlineKeyboardButton(f"{cat_name[:25]}", callback_data=f"admin_noop"),
                InlineKeyboardButton("✏️", callback_data=f"admin_edit_cat_prompt_{cat_id}"),
                InlineKeyboardButton("🗑️", callback_data=f"admin_delete_cat_confirm_{cat_id}")
            ])
    else:
        text += "\nHozircha kategoriyalar mavjud emas."
    keyboard.append([InlineKeyboardButton("➕ Yangi Kategoriya Qo'shish", callback_data="admin_add_category_prompt")])
    keyboard.append([InlineKeyboardButton("⬅️ Admin Panelga", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_or_edit_message(context, query.message.chat_id, text, reply_markup, query.message.message_id,
                               delete_previous=True)


async def admin_add_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query;
    await query.answer()
    await send_or_edit_message(context, query.message.chat_id, "Yangi kategoriya nomini kiriting (/cancel):",
                               message_id_to_edit=query.message.message_id, delete_previous=True)
    return ASK_CATEGORY_NAME


async def admin_save_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    category_name = update.message.text.strip()
    if not category_name:
        await update.message.reply_text("Kategoriya nomi bo'sh bo'lishi mumkin emas.");
        return ASK_CATEGORY_NAME
    try:
        db_query("INSERT INTO categories (name) VALUES (?)", (category_name,))
        await update.message.reply_text(f"✅ '{category_name}' kategoriyasi qo'shildi.")
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"❗️ '{category_name}' allaqachon mavjud.")
    except Exception as e:
        await update.message.reply_text(f"Xatolik: {e}")
    await admin_panel_after_conv_end(update, context);
    return ConversationHandler.END


async def admin_edit_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[-1])
    category = db_fetch_one("SELECT name FROM categories WHERE id = ?", (cat_id,))
    if not category:
        await send_or_edit_message(context, query.message.chat_id, "Kategoriya topilmadi.",
                                   message_id_to_edit=query.message.message_id, delete_previous=True)
        return ConversationHandler.END
    context.user_data['edit_category_id'] = cat_id
    await send_or_edit_message(context, query.message.chat_id,
                               f"'{category[0]}' uchun yangi nom kiriting (/cancel):",
                               message_id_to_edit=query.message.message_id, delete_previous=True)
    return ASK_CATEGORY_EDIT_NAME


async def admin_save_edited_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_name = update.message.text.strip()
    cat_id = context.user_data.get('edit_category_id')
    if not new_name or not cat_id:
        await update.message.reply_text("Ma'lumotlar to'liq emas.")
        await admin_panel_after_conv_end(update, context);
        return ConversationHandler.END
    try:
        db_query("UPDATE categories SET name = ? WHERE id = ?", (new_name, cat_id))
        await update.message.reply_text(f"✅ Kategoriya nomi '{new_name}' ga o'zgartirildi.")
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"❗️ '{new_name}' nomli kategoriya allaqachon mavjud.")
    except Exception as e:
        await update.message.reply_text(f"Xatolik: {e}")
    if 'edit_category_id' in context.user_data: del context.user_data['edit_category_id']
    await admin_panel_after_conv_end(update, context);
    return ConversationHandler.END


async def admin_delete_category_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[-1])
    category = db_fetch_one("SELECT name FROM categories WHERE id = ?", (cat_id,))
    products_in_category = db_fetch_all("SELECT id FROM products WHERE category_id = ?", (cat_id,))
    if not category:
        await send_or_edit_message(context, query.message.chat_id, "Kategoriya topilmadi.",
                                   message_id_to_edit=query.message.message_id, delete_previous=True)
        return
    warning_text = ""
    if products_in_category:
        warning_text = f"\n\n⚠️ Diqqat! Bu kategoriyada {len(products_in_category)} ta mahsulot bor. Ular kategoriyasiz qoladi."
    keyboard = [
        [InlineKeyboardButton("✅ Ha, o'chirish", callback_data=f"admin_delete_cat_execute_{cat_id}")],
        [InlineKeyboardButton("❌ Yo'q, bekor qilish", callback_data="admin_manage_categories")]
    ]
    await send_or_edit_message(context, query.message.chat_id,
                               f"Haqiqatan ham '{category[0]}' kategoriyasini o'chirmoqchimisiz?{warning_text}",
                               InlineKeyboardMarkup(keyboard), query.message.message_id, delete_previous=True)


async def admin_delete_category_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[-1])
    cat_name_tuple = db_fetch_one("SELECT name FROM categories WHERE id = ?", (cat_id,))
    cat_name = cat_name_tuple[0] if cat_name_tuple else "Noma'lum"
    text_to_show = ""
    try:
        db_query("DELETE FROM categories WHERE id = ?", (cat_id,))
        text_to_show = f"🗑️ '{cat_name}' kategoriyasi o'chirildi."
    except Exception as e:
        logger.error(f"Kategoriyani o'chirishda xatolik: {e}")
        text_to_show = "Kategoriyani o'chirishda xatolik yuz berdi."
    await admin_panel_after_callback_action(query, context, message_text_prefix=text_to_show)


# --- Product Management (Admin) ---
async def admin_manage_products_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    products = db_fetch_all(
        "SELECT p.id, p.name, c.name as category_name FROM products p LEFT JOIN categories c ON p.category_id = c.id ORDER BY p.name")
    text = "Mahsulotlarni boshqarish:\n(Tahrirlash uchun mahsulot nomiga bosing)\n"
    keyboard = []
    if products:
        for prod_id, prod_name, cat_name in products:
            keyboard.append([
                InlineKeyboardButton(f"{prod_name[:20]}.. ({cat_name or 'Kategoriyasiz'})",
                                     callback_data=f"admin_view_prod_{prod_id}"),
            ])
    else:
        text += "\nHozircha mahsulotlar mavjud emas."
    keyboard.append([InlineKeyboardButton("📦 Yangi Mahsulot Qo'shish", callback_data="admin_add_product_start")])
    keyboard.append([InlineKeyboardButton("⬅️ Admin Panelga", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_or_edit_message(context, query.message.chat_id, text, reply_markup, query.message.message_id,
                               delete_previous=True)


async def admin_view_single_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split("_")[-1])
    product = db_fetch_one(
        "SELECT p.id, p.name, p.description, p.price, p.image_file_id, c.name as category_name FROM products p LEFT JOIN categories c ON p.category_id = c.id WHERE p.id = ?",
        (product_id,))
    if not product:
        await send_or_edit_message(context, query.message.chat_id, "Mahsulot topilmadi.",
                                   message_id_to_edit=query.message.message_id, delete_previous=True)
        return

    _id, name, desc, price, img_id, cat_name = product
    context.user_data['current_editing_product_id'] = _id

    caption = f"<b>Mahsulot: {name}</b>\n"
    if cat_name:
        caption += f"Kategoriya: {cat_name}\n"
    else:
        caption += "Kategoriya: Belgilanmagan\n"
    if desc: caption += f"Tavsif: <i>{desc}</i>\n"
    caption += f"Narxi: {price:,.0f} so'm"

    keyboard = [
        [InlineKeyboardButton("✏️ Nomini", callback_data=f"admin_edit_prod_field_name"),
         InlineKeyboardButton("✏️ Tavsifini", callback_data=f"admin_edit_prod_field_desc")],
        [InlineKeyboardButton("✏️ Narxini", callback_data=f"admin_edit_price_entry_{_id}"),
         InlineKeyboardButton("✏️ Rasmini", callback_data=f"admin_edit_prod_field_image")],
        [InlineKeyboardButton("✏️ Kategoriyasini", callback_data=f"admin_edit_prod_field_category")],
        [InlineKeyboardButton("🗑️ O'CHIRISH", callback_data=f"admin_delete_prod_confirm_{_id}")],
        [InlineKeyboardButton("⬅️ Mahsulotlar ro'yxatiga", callback_data="admin_manage_products_list")],
        [InlineKeyboardButton("🏠 Admin Panelga", callback_data="admin_panel")]
    ]
    await send_or_edit_message(context, query.message.chat_id, caption, InlineKeyboardMarkup(keyboard),
                               query.message.message_id, photo_file_id=img_id, delete_previous=True)


async def admin_add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query;
    await query.answer()
    categories = db_fetch_all("SELECT id, name FROM categories ORDER BY name")
    if not categories:
        await send_or_edit_message(context, query.message.chat_id, "Avval kategoriya qo'shing.", InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Admin Panelga", callback_data="admin_panel")]]), query.message.message_id,
                                   delete_previous=True)
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(name, callback_data=f"prodcat_{cat_id}")] for cat_id, name in categories]
    keyboard.append([InlineKeyboardButton("Kategoriyasiz qo'shish", callback_data="prodcat_None")])
    keyboard.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="admin_cancel_conv")])
    await send_or_edit_message(context, query.message.chat_id, "Mahsulot uchun kategoriyani tanlang:",
                               InlineKeyboardMarkup(keyboard), query.message.message_id, delete_previous=True)
    return SELECT_PRODUCT_CATEGORY


async def admin_ask_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query;
    await query.answer()
    category_id_str = query.data.split("_")[1]
    context.user_data['new_product_category_id'] = int(category_id_str) if category_id_str != "None" else None
    await send_or_edit_message(context, query.message.chat_id, "Mahsulot nomini kiriting (/cancel):",
                               message_id_to_edit=query.message.message_id, delete_previous=True)
    return ASK_PRODUCT_NAME


async def admin_ask_product_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name: await update.message.reply_text("Nom bo'sh bo'lmasligi kerak."); return ASK_PRODUCT_NAME
    context.user_data['new_product_name'] = name
    await update.message.reply_text("Mahsulot tavsifini kiriting (ixtiyoriy, /skip, /cancel):")
    return ASK_PRODUCT_DESCRIPTION


async def admin_ask_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text and update.message.text.lower() != "/skip":
        context.user_data['new_product_description'] = update.message.text.strip()
    else:
        context.user_data['new_product_description'] = None
    await update.message.reply_text("Mahsulot narxini kiriting (masalan: 250000, /cancel):")
    return ASK_PRODUCT_PRICE


async def admin_ask_product_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        price = float(update.message.text.replace(",", "."))
        if price <= 0: await update.message.reply_text("Narx > 0 bo'lishi kerak."); return ASK_PRODUCT_PRICE
        context.user_data['new_product_price'] = price
        await update.message.reply_text("Mahsulot rasmini yuboring (ixtiyoriy, /skip, /cancel):")
        return ASK_PRODUCT_IMAGE
    except ValueError:
        await update.message.reply_text("Narx noto'g'ri."); return ASK_PRODUCT_PRICE


async def admin_save_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    image_file_id = None
    if update.message.photo:
        image_file_id = update.message.photo[-1].file_id
    elif update.message.text and update.message.text.lower() == "/skip":
        image_file_id = None
    elif update.message.document and update.message.document.mime_type.startswith('image/'):
        image_file_id = update.message.document.file_id
    else:
        await update.message.reply_text("Rasm yuboring yoki /skip (/cancel):"); return ASK_PRODUCT_IMAGE
    category_id = context.user_data.get('new_product_category_id')
    name = context.user_data['new_product_name']
    description = context.user_data.get('new_product_description')
    price = context.user_data['new_product_price']
    try:
        db_query("INSERT INTO products (category_id, name, description, price, image_file_id) VALUES (?, ?, ?, ?, ?)",
                 (category_id, name, description, price, image_file_id))
        await update.message.reply_text(f"✅ '{name}' mahsuloti qo'shildi.")
    except Exception as e:
        await update.message.reply_text(f"Mahsulotni saqlashda xatolik: {e}")
    for key in ['new_product_category_id', 'new_product_name', 'new_product_description', 'new_product_price']:
        if key in context.user_data: del context.user_data[key]
    await admin_panel_after_conv_end(update, context);
    return ConversationHandler.END


async def admin_edit_product_field_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    field_action = query.data
    product_id = context.user_data.get('current_editing_product_id')
    if not product_id:
        await send_or_edit_message(context, query.message.chat_id, "Tahrirlanadigan mahsulot ID si topilmadi.",
                                   message_id_to_edit=query.message.message_id, delete_previous=True)
        await admin_panel_after_callback_action(query, context)
        return ConversationHandler.END

    product_name_tuple = db_fetch_one("SELECT name FROM products WHERE id = ?", (product_id,))
    product_name = product_name_tuple[0] if product_name_tuple else "Noma'lum"
    context.user_data['editing_product_id_for_field'] = product_id  # Bu ID ni keyingi stepda ishlatamiz

    if field_action == "admin_edit_prod_field_name":
        await send_or_edit_message(context, query.message.chat_id,
                                   f"'{product_name}' uchun yangi nomni kiriting (/cancel):",
                                   message_id_to_edit=query.message.message_id, delete_previous=True)
        return ASK_EDIT_PRODUCT_NEW_NAME
    elif field_action == "admin_edit_prod_field_desc":
        await send_or_edit_message(context, query.message.chat_id,
                                   f"'{product_name}' uchun yangi tavsifni kiriting (/skip, /cancel):",
                                   message_id_to_edit=query.message.message_id, delete_previous=True)
        return ASK_EDIT_PRODUCT_NEW_DESC
    elif field_action == "admin_edit_prod_field_image":
        await send_or_edit_message(context, query.message.chat_id,
                                   f"'{product_name}' uchun yangi rasmni yuboring (/skip, /cancel):",
                                   message_id_to_edit=query.message.message_id, delete_previous=True)
        return ASK_EDIT_PRODUCT_NEW_IMAGE
    elif field_action == "admin_edit_prod_field_category":
        categories = db_fetch_all("SELECT id, name FROM categories ORDER BY name")
        cat_keyboard_buttons = [[InlineKeyboardButton(name, callback_data=f"prod_setcat_{cat_id}")] for cat_id, name in
                                categories]
        cat_keyboard_buttons.append([InlineKeyboardButton("Kategoriyasiz qoldirish", callback_data="prod_setcat_None")])
        cat_keyboard_buttons.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="admin_cancel_conv")])
        await send_or_edit_message(context, query.message.chat_id,
                                   f"'{product_name}' uchun yangi kategoriyani tanlang:",
                                   InlineKeyboardMarkup(cat_keyboard_buttons), query.message.message_id,
                                   delete_previous=True)
        return ASK_EDIT_PRODUCT_NEW_CATEGORY
    # Narx uchun alohida handler chaqiriladi, bu routerga kirmaydi.
    return ConversationHandler.END


async def admin_save_edited_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_name = update.message.text.strip()
    product_id = context.user_data.get('editing_product_id_for_field')
    if not product_id: await update.message.reply_text("Mahsulot ID topilmadi."); await admin_panel_after_conv_end(
        update, context); return ConversationHandler.END
    if not new_name: await update.message.reply_text("Nom bo'sh bo'lmasligi kerak."); return ASK_EDIT_PRODUCT_NEW_NAME
    db_query("UPDATE products SET name = ? WHERE id = ?", (new_name, product_id))
    await update.message.reply_text("✅ Mahsulot nomi yangilandi.")
    if 'editing_product_id_for_field' in context.user_data: del context.user_data['editing_product_id_for_field']
    # current_editing_product_id qoladi
    await admin_panel_after_conv_end(update, context);
    return ConversationHandler.END  # Yoki mahsulotni ko'rsatishga qaytish


async def admin_save_edited_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_desc = None
    if update.message.text and update.message.text.lower() != "/skip":
        new_desc = update.message.text.strip()
    product_id = context.user_data.get('editing_product_id_for_field')
    if not product_id: await update.message.reply_text("Mahsulot ID topilmadi."); await admin_panel_after_conv_end(
        update, context); return ConversationHandler.END
    db_query("UPDATE products SET description = ? WHERE id = ?", (new_desc, product_id))
    await update.message.reply_text("✅ Mahsulot tavsifi yangilandi.")
    if 'editing_product_id_for_field' in context.user_data: del context.user_data['editing_product_id_for_field']
    await admin_panel_after_conv_end(update, context);
    return ConversationHandler.END


async def admin_save_edited_product_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_image_id = None
    if update.message.photo:
        new_image_id = update.message.photo[-1].file_id
    elif update.message.document and update.message.document.mime_type.startswith('image/'):
        new_image_id = update.message.document.file_id
    elif update.message.text and update.message.text.lower() == "/skip":
        new_image_id = None
    else:
        await update.message.reply_text("Rasm yuboring yoki /skip."); return ASK_EDIT_PRODUCT_NEW_IMAGE
    product_id = context.user_data.get('editing_product_id_for_field')
    if not product_id: await update.message.reply_text("Mahsulot ID topilmadi."); await admin_panel_after_conv_end(
        update, context); return ConversationHandler.END
    db_query("UPDATE products SET image_file_id = ? WHERE id = ?", (new_image_id, product_id))
    await update.message.reply_text("✅ Mahsulot rasmi yangilandi.")
    if 'editing_product_id_for_field' in context.user_data: del context.user_data['editing_product_id_for_field']
    await admin_panel_after_conv_end(update, context);
    return ConversationHandler.END


async def admin_save_edited_product_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query;
    await query.answer()
    product_id = context.user_data.get('editing_product_id_for_field')
    new_cat_id_str = query.data.split("_")[-1]
    new_cat_id = None if new_cat_id_str == "None" else int(new_cat_id_str)
    if not product_id:
        text_error = "Xatolik: Mahsulot ID topilmadi (kategoriyani saqlash)."
        logger.error(text_error)
        await send_or_edit_message(context, query.message.chat_id, text_error,
                                   message_id_to_edit=query.message.message_id, delete_previous=True)
        await admin_panel_after_callback_action(query, context, message_text_prefix="Xatolik yuz berdi.")
        return ConversationHandler.END

    db_query("UPDATE products SET category_id = ? WHERE id = ?", (new_cat_id, product_id))
    cat_name_tuple = db_fetch_one("SELECT name FROM categories WHERE id = ?", (new_cat_id,)) if new_cat_id else None
    cat_name = cat_name_tuple[0] if cat_name_tuple else "Kategoriyasiz"
    text_to_show = f"✅ Mahsulot kategoriyasi '{cat_name}' ga o'zgartirildi."

    if 'editing_product_id_for_field' in context.user_data: del context.user_data['editing_product_id_for_field']

    # Endi mahsulotni ko'rish ekraniga qaytamiz
    fake_callback_query_data = f"admin_view_prod_{product_id}"
    # `update` obyektini qayta ishlatishda ehtiyot bo'lish kerak, yangi Update yaratish yaxshiroq
    # Lekin bu yerda query.message ni ishlatsak bo'ladi
    new_update_for_view = Update(update_id=query.update_id,
                                 # update.update_id callback query da yo'q, shuning uchun query.update_id
                                 callback_query=type('FakeCallbackQuery', (object,), {
                                     'id': query.id,
                                     'from_user': query.from_user,
                                     'message': query.message,  # Eski xabarni ishlatamiz
                                     'data': fake_callback_query_data,
                                     'answer': query.answer  # await qilingan answer
                                 })())
    await send_or_edit_message(context, query.message.chat_id, text_to_show,  # Avval natijani ko'rsatamiz
                               message_id_to_edit=query.message.message_id, delete_previous=True)
    # Keyin mahsulotni ko'rsatishni chaqiramiz
    # await admin_view_single_product(new_update_for_view, context) # Bu xatolik berishi mumkin, chunki context da current_editing_product_id bo'lmasligi mumkin
    # Yaxshisi admin panelga qaytamiz
    await admin_panel_after_callback_action(query, context, message_text_prefix=text_to_show)
    return ConversationHandler.END


async def admin_cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text_to_send = "Amal bekor qilindi."
    product_id_to_return_to = context.user_data.get('current_editing_product_id')

    keys_to_clear = [k for k in context.user_data if
                     k.startswith('new_product_') or k.startswith('edit_') or k.startswith('editing_') or k.startswith(
                         'current_editing_')]
    for key in keys_to_clear:
        if key in context.user_data: del context.user_data[key]

    if update.callback_query:
        await update.callback_query.answer(text_to_send)
        if product_id_to_return_to:
            # Mahsulotni ko'rish ekraniga qaytish
            context.user_data['current_editing_product_id'] = product_id_to_return_to  # Qayta tiklaymiz
            # "Soxta" callback bilan admin_view_single_product ni chaqiramiz
            # Bu ideal emas, lekin ishlaydi. Yaxshiroq yechim - state'larni to'g'ri boshqarish.
            fake_callback_data = f"admin_view_prod_{product_id_to_return_to}"
            # Eski xabar ID sini ishlatish uchun query.message kerak
            await send_or_edit_message(context, update.callback_query.message.chat_id, text_to_send,
                                       message_id_to_edit=update.callback_query.message.message_id,
                                       delete_previous=True)

            # Agar admin_view_single_product ga qaytmoqchi bo'lsak, unga to'g'ri update yuborishimiz kerak
            # Bu biroz murakkab, shuning uchun hozircha admin paneliga qaytamiz
            await admin_panel_after_callback_action(update.callback_query, context, message_text_prefix=text_to_send)

        else:
            await admin_panel_after_callback_action(update.callback_query, context)
    elif update.message:
        await update.message.reply_text(text_to_send)
        await admin_panel_after_conv_end(update, context)
    return ConversationHandler.END


async def admin_edit_price_entry_point(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query;
    await query.answer()
    product_id = int(query.data.split("_")[-1])
    product = db_fetch_one("SELECT name, price FROM products WHERE id = ?", (product_id,))
    if not product:
        await send_or_edit_message(context, query.message.chat_id, "Mahsulot topilmadi.",
                                   message_id_to_edit=query.message.message_id, delete_previous=True)
        return ConversationHandler.END
    context.user_data[EDIT_PRICE_ENTRY_PRODUCT_ID] = product_id
    await send_or_edit_message(context, query.message.chat_id,
                               f"'{product[0]}' uchun yangi narxni kiriting (hozirgi: {product[1]:,.0f} so'm, /cancel):",
                               message_id_to_edit=query.message.message_id, delete_previous=True)
    return EDIT_PRICE_ASK_NEW_PRICE


async def admin_save_edited_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    product_id = context.user_data.get(EDIT_PRICE_ENTRY_PRODUCT_ID)
    if not product_id: await update.message.reply_text("Mahsulot ID topilmadi."); await admin_panel_after_conv_end(
        update, context); return ConversationHandler.END
    try:
        new_price = float(update.message.text.replace(",", "."))
        if new_price <= 0: await update.message.reply_text("Narx > 0 bo'lishi kerak."); return EDIT_PRICE_ASK_NEW_PRICE
        db_query("UPDATE products SET price = ? WHERE id = ?", (new_price, product_id))
        product_name_tuple = db_fetch_one("SELECT name FROM products WHERE id = ?", (product_id,))
        product_name = product_name_tuple[0] if product_name_tuple else "Noma'lum"
        await update.message.reply_text(f"✅ '{product_name}' narxi {new_price:,.0f} so'mga o'zgartirildi.")
    except ValueError:
        await update.message.reply_text("Narx noto'g'ri."); return EDIT_PRICE_ASK_NEW_PRICE
    except Exception as e:
        await update.message.reply_text(f"Xatolik: {e}")

    if EDIT_PRICE_ENTRY_PRODUCT_ID in context.user_data: del context.user_data[EDIT_PRICE_ENTRY_PRODUCT_ID]
    # current_editing_product_id qoladi, chunki biz mahsulotni ko'rish menyusiga qaytishimiz mumkin
    # Hozircha admin paneliga qaytamiz
    await admin_panel_after_conv_end(update, context)
    return ConversationHandler.END


async def admin_delete_prod_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query;
    await query.answer()
    try:
        product_id = int(query.data.split("_")[-1])
    except:
        await send_or_edit_message(context, query.message.chat_id, "Xato ID.",
                                   message_id_to_edit=query.message.message_id, delete_previous=True); return
    product = db_fetch_one("SELECT name FROM products WHERE id = ?", (product_id,))
    if not product: await send_or_edit_message(context, query.message.chat_id, "Mahsulot topilmadi.",
                                               message_id_to_edit=query.message.message_id,
                                               delete_previous=True); return
    keyboard = [
        [InlineKeyboardButton("✅ Ha, o'chirish", callback_data=f"admin_delete_prod_execute_{product_id}")],
        [InlineKeyboardButton("❌ Yo'q, bekor qilish", callback_data=f"admin_view_prod_{product_id}")]
    ]
    await send_or_edit_message(context, query.message.chat_id,
                               f"Haqiqatan ham '{product[0]}' mahsulotini o'chirmoqchimisiz?",
                               InlineKeyboardMarkup(keyboard), query.message.message_id, delete_previous=True)


async def admin_delete_prod_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query;
    await query.answer()
    try:
        product_id = int(query.data.split("_")[-1])
    except:
        await send_or_edit_message(context, query.message.chat_id, "Xato ID (exec).",
                                   message_id_to_edit=query.message.message_id, delete_previous=True); return
    product_name_tuple = db_fetch_one("SELECT name FROM products WHERE id = ?", (product_id,))
    product_name = product_name_tuple[0] if product_name_tuple else "Noma'lum"
    text_to_show = ""
    try:
        db_query("DELETE FROM products WHERE id = ?", (product_id,))
        text_to_show = f"🗑️ '{product_name}' mahsuloti o'chirildi."
    except Exception as e:
        text_to_show = f"Mahsulotni o'chirishda xatolik: {e}"
    if 'current_editing_product_id' in context.user_data: del context.user_data['current_editing_product_id']
    await admin_panel_after_callback_action(query, context, message_text_prefix=text_to_show)


async def admin_view_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await save_user_info(query.from_user)
    logger.info(f"admin_view_orders: Admin {query.from_user.id} buyurtmalarni ko'rmoqda.")

    orders_data = db_fetch_all("""
                               SELECT o.id,
                                      o.user_id,
                                      COALESCE(u.first_name, '')                                      AS u_fname,
                                      COALESCE(u.last_name, '')                                       AS u_lname,
                                      o.user_username,
                                      o.phone_number,
                                      COALESCE(o.product_name_at_order, p.name, 'Noma''lum mahsulot') as product_display_name, -- TIRNOQ TO'G'RILANDI
                                      COALESCE(o.product_price_at_order, p.price, 0)                  as product_display_price,
                                      o.timestamp
                               FROM orders o
                                        LEFT JOIN products p ON o.product_id = p.id
                                        LEFT JOIN users u ON o.user_id = u.id
                               ORDER BY o.timestamp DESC LIMIT 30
                               """)
    logger.info(f"admin_view_orders: Bazadan {len(orders_data)} ta buyurtma olindi.")
    if not orders_data:
        logger.info("admin_view_orders: Bazada buyurtmalar topilmadi.")
        await send_or_edit_message(context, query.message.chat_id,
                                   "Hozircha buyurtmalar mavjud emas.",
                                   reply_markup=InlineKeyboardMarkup(
                                       [[InlineKeyboardButton("⬅️ Admin Panelga", callback_data="admin_panel")]]),
                                   message_id_to_edit=query.message.message_id, delete_previous=True)
        return

    message_text = "<b>Oxirgi buyurtmalar:</b>\n"
    current_message_id_for_parts = query.message.message_id

    for i, order_tuple in enumerate(orders_data):
        logger.debug(f"admin_view_orders: Formatlanayotgan buyurtma: {order_tuple}")
        (order_id, user_id_db, u_fname, u_lname, user_username_from_orders,
         phone, prod_name, prod_price, timestamp_str) = order_tuple

        user_full_name_from_users = (f"{u_fname} {u_lname}").strip()
        display_name = user_full_name_from_users
        if not display_name:
            if user_username_from_orders and user_username_from_orders.lower() != 'n/a':
                display_name = f"@{user_username_from_orders}"
            else:
                display_name = f"Mijoz ID: <code>{user_id_db}</code>"
        elif user_username_from_orders and user_username_from_orders.lower() != 'n/a' and f"@{user_username_from_orders}" not in display_name:
            display_name += f" (@{user_username_from_orders})"

        formatted_timestamp = ""
        try:
            dt_object = datetime.fromisoformat(timestamp_str.split('.')[0])
            formatted_timestamp = dt_object.strftime("%Y-%m-%d %H:%M:%S")
        except:
            formatted_timestamp = timestamp_str.split('.')[0] if '.' in timestamp_str else timestamp_str

        order_info = (
            f"\n➖➖➖➖➖➖➖➖➖➖➖\n"
            f"🆔 Buyurtma Raqami: <b>{order_id}</b>\n"
            f"👤 Mijoz: {display_name}\n"
            f"📞 Telefon: <code>{phone}</code>\n"
            f"🛍️ Mahsulot: {prod_name}\n"
            f"💰 Narxi: {prod_price or 0:,.0f} so'm\n"
            f"🕒 Vaqti: {formatted_timestamp}"
        )

        if len(message_text + order_info) > 4050:
            logger.info("admin_view_orders: Xabar uzunligi chegaraga yetdi, qisman yuborilmoqda.")
            delete_flag_for_part = (i == 0 and current_message_id_for_parts == query.message.message_id)
            await send_or_edit_message(context, query.message.chat_id, message_text, parse_mode='HTML',
                                       message_id_to_edit=current_message_id_for_parts,
                                       delete_previous=delete_flag_for_part)
            message_text = "<i>(davomi...)</i>\n"
            current_message_id_for_parts = None
        message_text += order_info

    message_text += "\n➖➖➖➖➖➖➖➖➖➖➖"
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panelga", callback_data="admin_panel")]])

    logger.info("admin_view_orders: Buyurtmalar ro'yxati adminga yuborilmoqda (oxirgi qism).")
    delete_flag_for_final = False
    if query.message and current_message_id_for_parts == query.message.message_id and query.message.text != message_text:
        delete_flag_for_final = True  # Faqat agar xabar o'zgargan bo'lsa va birinchi xabar bo'lsa o'chiramiz

    if query.message and current_message_id_for_parts == query.message.message_id and query.message.text == message_text and query.message.reply_markup == reply_markup:
        logger.debug("admin_view_orders: Xabar va tugmalar o'zgarmagan, yuborilmaydi yoki tahrirlanmaydi.")
    else:
        await send_or_edit_message(context, query.message.chat_id, message_text, reply_markup,
                                   message_id_to_edit=current_message_id_for_parts,
                                   parse_mode='HTML',
                                   delete_previous=delete_flag_for_final)


async def admin_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query;
    await query.answer()


def main() -> None:
    setup_database()
    application = Application.builder().token(BOT_TOKEN).build()

    cancel_command_filter = filters.COMMAND & filters.Regex(r'^/cancel$')
    skip_command_filter = filters.COMMAND & filters.Regex(r'^/skip$')
    conv_fallbacks = [CommandHandler("cancel", admin_cancel_conv),
                      CallbackQueryHandler(admin_cancel_conv, pattern="^admin_cancel_conv$")]

    add_category_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_category_prompt, pattern="^admin_add_category_prompt$")],
        states={ASK_CATEGORY_NAME: [MessageHandler(filters.TEXT & ~cancel_command_filter, admin_save_category)]},
        fallbacks=conv_fallbacks, allow_reentry=True
    )
    edit_category_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_edit_category_prompt, pattern="^admin_edit_cat_prompt_")],
        states={ASK_CATEGORY_EDIT_NAME: [
            MessageHandler(filters.TEXT & ~cancel_command_filter, admin_save_edited_category)]},
        fallbacks=conv_fallbacks, allow_reentry=True
    )
    add_product_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_product_start, pattern="^admin_add_product_start$")],
        states={
            SELECT_PRODUCT_CATEGORY: [CallbackQueryHandler(admin_ask_product_name, pattern="^prodcat_")],
            ASK_PRODUCT_NAME: [MessageHandler(filters.TEXT & ~cancel_command_filter, admin_ask_product_description)],
            ASK_PRODUCT_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~cancel_command_filter & ~skip_command_filter, admin_ask_product_price),
                CommandHandler("skip", admin_ask_product_price)],
            ASK_PRODUCT_PRICE: [MessageHandler(filters.TEXT & ~cancel_command_filter, admin_ask_product_image)],
            ASK_PRODUCT_IMAGE: [MessageHandler(
                filters.PHOTO | (filters.TEXT & ~cancel_command_filter & ~skip_command_filter) | filters.Document.IMAGE,
                admin_save_product), CommandHandler("skip", admin_save_product)],
        },
        fallbacks=conv_fallbacks, allow_reentry=True
    )
    edit_product_field_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_edit_product_field_router, pattern="^admin_edit_prod_field_name$"),
            CallbackQueryHandler(admin_edit_product_field_router, pattern="^admin_edit_prod_field_desc$"),
            CallbackQueryHandler(admin_edit_product_field_router, pattern="^admin_edit_prod_field_image$"),
            CallbackQueryHandler(admin_edit_product_field_router, pattern="^admin_edit_prod_field_category$"),
        ],
        states={
            ASK_EDIT_PRODUCT_NEW_NAME: [
                MessageHandler(filters.TEXT & ~cancel_command_filter, admin_save_edited_product_name)],
            ASK_EDIT_PRODUCT_NEW_DESC: [MessageHandler(filters.TEXT & ~cancel_command_filter & ~skip_command_filter,
                                                       admin_save_edited_product_desc),
                                        CommandHandler("skip", admin_save_edited_product_desc)],
            ASK_EDIT_PRODUCT_NEW_IMAGE: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE | (filters.TEXT & skip_command_filter),
                               admin_save_edited_product_image),
                CommandHandler("skip", admin_save_edited_product_image)],
            ASK_EDIT_PRODUCT_NEW_CATEGORY: [
                CallbackQueryHandler(admin_save_edited_product_category_callback, pattern="^prod_setcat_")],
        },
        fallbacks=conv_fallbacks, allow_reentry=True
    )
    edit_price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_edit_price_entry_point, pattern="^admin_edit_price_entry_")],
        states={
            EDIT_PRICE_ASK_NEW_PRICE: [MessageHandler(filters.TEXT & ~cancel_command_filter, admin_save_edited_price)]},
        fallbacks=conv_fallbacks, allow_reentry=True
    )

    application.add_handler(add_category_conv)
    application.add_handler(edit_category_conv)
    application.add_handler(add_product_conv)
    application.add_handler(edit_product_field_conv)
    application.add_handler(edit_price_conv)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel, filters=filters.User(user_id=ADMIN_ID)))

    application.add_handler(CallbackQueryHandler(view_categories, pattern="^view_categories$"))
    application.add_handler(CallbackQueryHandler(show_products_in_category, pattern="^category_"))
    application.add_handler(CallbackQueryHandler(next_product, pattern="^next_product$"))
    application.add_handler(CallbackQueryHandler(prev_product, pattern="^prev_product$"))
    application.add_handler(CallbackQueryHandler(buy_product_prompt, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))

    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_manage_categories, pattern="^admin_manage_categories$"))
    application.add_handler(CallbackQueryHandler(admin_delete_category_confirm, pattern="^admin_delete_cat_confirm_"))
    application.add_handler(CallbackQueryHandler(admin_delete_category_execute, pattern="^admin_delete_cat_execute_"))
    application.add_handler(CallbackQueryHandler(admin_manage_products_list, pattern="^admin_manage_products_list$"))
    application.add_handler(CallbackQueryHandler(admin_view_single_product, pattern="^admin_view_prod_"))
    application.add_handler(CallbackQueryHandler(admin_delete_prod_confirm, pattern="^admin_delete_prod_confirm_"))
    application.add_handler(CallbackQueryHandler(admin_delete_prod_execute, pattern="^admin_delete_prod_execute_"))
    application.add_handler(CallbackQueryHandler(admin_view_orders, pattern="^admin_view_orders$"))
    application.add_handler(CallbackQueryHandler(admin_noop, pattern="^admin_noop$"))

    application.add_handler(MessageHandler(
        filters.CONTACT | (filters.TEXT & ~filters.COMMAND & ~cancel_command_filter & ~skip_command_filter),
        process_contact))

    logger.info("Bot ishga tushdi...")
    application.run_polling()


if __name__ == "__main__":
    main()