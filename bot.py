import asyncio
import csv
import json
import os
import logging
from pathlib import Path
from typing import List, Dict, Any

from telethon import TelegramClient, errors, functions
from telethon.sessions import StringSession

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)
from telegram.constants import ParseMode

from apify_client import ApifyClient
from dotenv import load_dotenv

# ============================
# Configuration and Setup
# ============================

# Load environment variables from .env file
load_dotenv()

# Telegram Bot Token obtained from BotFather
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")

# Webhook URL set to https://yourdomain.com/{BOT_TOKEN}
WEBHOOK_URL = os.getenv("WEBHOOK_URL", f"https://yourdomain.com/{BOT_TOKEN}")

# List of Admin Telegram User IDs (JSON Array)
try:
    ADMINS = json.loads(os.getenv("ADMINS", "[123456789, 987654321]"))  # Replace with actual admin user IDs
except json.JSONDecodeError:
    ADMINS = []
    logging.error("ADMINS environment variable is not a valid JSON array. Using an empty admin list.")

# ==========================
# End of Configuration
# ==========================

# Configure logging with rotation to prevent log file from growing indefinitely
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.handlers.RotatingFileHandler(
    'bot.log', maxBytes=5*1024*1024, backupCount=2, encoding='utf-8'
)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

# File to store blocked users and user sessions
CONFIG_FILE = 'config.json'

# Initialize or load configurations
default_config = {
    "blocked_users": [],
    "user_sessions": {},
    "telegram_api_id": None,
    "telegram_api_hash": None,
    "telegram_string_session": None,
    "target_channel_username": None,
    "apify_api_token": None
}

if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        try:
            config = json.load(f)
            # Ensure all keys are present
            for key, value in default_config.items():
                if key not in config:
                    config[key] = value
        except json.JSONDecodeError:
            logger.error("config.json is corrupted. Resetting configurations.")
            config = default_config.copy()
            with open(CONFIG_FILE, 'w', encoding='utf-8') as fw:
                json.dump(config, fw, indent=4, ensure_ascii=False)
else:
    config = default_config.copy()
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

# Helper functions to manage configurations
def save_config():
    """
    Save the current configuration to config.json.
    """
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        logger.info("Configuration saved successfully.")
    except Exception as e:
        logger.error(f"Failed to save config.json: {e}")

def is_admin(user_id: int) -> bool:
    """
    Check if a user is an admin.

    Args:
        user_id (int): Telegram user ID.

    Returns:
        bool: True if the user is an admin, False otherwise.
    """
    return user_id in ADMINS

def get_session(user_id: int) -> Dict[str, Any]:
    """
    Retrieve session data for a user.

    Args:
        user_id (int): Telegram user ID.

    Returns:
        dict: Session data.
    """
    return config.get("user_sessions", {}).get(str(user_id), {})

def set_session(user_id: int, session_data: Dict[str, Any]):
    """
    Set session data for a user.

    Args:
        user_id (int): Telegram user ID.
        session_data (dict): Session data to set.
    """
    if "user_sessions" not in config:
        config["user_sessions"] = {}
    config["user_sessions"][str(user_id)] = session_data
    save_config()

# =====================
# TelegramChecker Class
# =====================

class TelegramChecker:
    """
    A class to check if phone numbers are registered on Telegram using Apify.
    """

    def __init__(self, api_token: str, proxy_config: Dict[str, Any] = None):
        """
        Initialize the TelegramChecker with API token and optional proxy configuration.

        Args:
            api_token (str): Your Apify API token.
            proxy_config (dict, optional): Proxy configuration for Apify. Defaults to None.
        """
        self.client = ApifyClient(api_token)
        self.proxy_config = proxy_config or {"useApifyProxy": True, "apifyProxyGroups": ["SHADER"]}
        logger.info("TelegramChecker initialized.")

    def read_csv(self, file_path: str) -> List[str]:
        """
        Read phone numbers from a CSV file.

        Args:
            file_path (str): Path to the CSV file.

        Returns:
            list: List of phone numbers.
        """
        phone_numbers = []
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                csv_reader = csv.reader(file)
                for row in csv_reader:
                    if row:
                        phone = row[0].strip()
                        if phone:
                            phone_numbers.append(phone)
            logger.info(f"Read {len(phone_numbers)} phone numbers from CSV.")
        except Exception as e:
            logger.error(f"Error reading CSV file {file_path}: {e}")
        return phone_numbers

    def check_telegram_status(self, phone_numbers: List[str]) -> List[Dict[str, Any]]:
        """
        Check if phone numbers are registered on Telegram.

        Args:
            phone_numbers (list): List of phone numbers to check.

        Returns:
            list: Results from the Telegram checker.
        """
        results = []
        for i in range(0, len(phone_numbers), 10):  # Process in batches of 10
            batch = phone_numbers[i:i+10]
            logger.info(f"Checking batch: {batch}")
            run_input = {
                "phoneNumbers": batch,
                "proxyConfiguration": self.proxy_config
            }
            try:
                run = self.client.actor("wilcode/telegram-phone-number-checker").call(run_input=run_input)
                dataset_id = run["defaultDatasetId"]
                dataset = self.client.dataset(dataset_id)
                for item in dataset.iterate_items():
                    results.append(item)
                logger.info(f"Batch {i//10 + 1} processed successfully.")
            except Exception as e:
                logger.error(f"Error processing batch {batch}: {e}")
        logger.info(f"Total results obtained: {len(results)}")
        return results

    def save_results(self, results: List[Dict[str, Any]], output_file: str):
        """
        Save the results to a CSV file.

        Args:
            results (list): Results from the Telegram checker.
            output_file (str): Path to the output CSV file.
        """
        try:
            with open(output_file, "w", newline="", encoding="utf-8") as file:
                csv_writer = csv.writer(file)
                csv_writer.writerow(["Phone Number", "Registered on Telegram", "Telegram User ID"])
                for result in results:
                    phone = result.get("phoneNumber")
                    is_registered = result.get("isRegistered")
                    user_id = result.get("userId") if is_registered else ""
                    csv_writer.writerow([phone, is_registered, user_id])
            logger.info(f"Results saved to {output_file}.")
        except Exception as e:
            logger.error(f"Failed to save results to {output_file}: {e}")

    def display_results(self, results: List[Dict[str, Any]]):
        """
        Display the results in the console.

        Args:
            results (list): Results from the Telegram checker.
        """
        logger.info("Telegram Checker Results:")
        for result in results:
            logger.info(f"Phone Number: {result.get('phoneNumber')} - Registered: {result.get('isRegistered')} - User ID: {result.get('userId', 'N/A')}")

# =====================
# TelegramAdder Class
# =====================

class TelegramAdder:
    """
    A class to add users to a Telegram channel using Telethon.
    """

    def __init__(self, api_id: int, api_hash: str, string_session: str, target_channel_username: str):
        """
        Initialize the TelegramAdder with API credentials and target channel.

        Args:
            api_id (int): Telegram API ID.
            api_hash (str): Telegram API Hash.
            string_session (str): StringSession for Telethon.
            target_channel_username (str): Username of the target channel (e.g., @yourchannel).
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.string_session = string_session
        self.target_channel_username = target_channel_username
        self.client = TelegramClient(StringSession(self.string_session), self.api_id, self.api_hash)
        logger.info("TelegramAdder initialized.")

    async def connect(self):
        """
        Connect to Telegram.
        """
        await self.client.connect()
        if not await self.client.is_user_authorized():
            logger.error("Telethon client is not authorized. Please ensure the bot is authorized.")
            raise ValueError("Telethon client is not authorized.")

    async def disconnect(self):
        """
        Disconnect from Telegram.
        """
        await self.client.disconnect()
        logger.info("Telethon client disconnected.")

    async def add_users_to_channel(self, user_ids: List[int], blocked_users: List[int]) -> Dict[str, List[int]]:
        """
        Add users to the target channel.

        Args:
            user_ids (list): List of Telegram user IDs to add.
            blocked_users (list): List of Telegram user IDs to block.

        Returns:
            dict: Summary of added and failed users.
        """
        summary = {
            "added": [],
            "failed": []
        }
        try:
            target_channel = await self.client.get_entity(self.target_channel_username)
            logger.info(f"Target channel {self.target_channel_username} retrieved.")
        except Exception as e:
            logger.error(f"Failed to get target channel {self.target_channel_username}: {e}")
            raise ValueError(f"Failed to get target channel {self.target_channel_username}: {e}")

        for user_id in user_ids:
            if user_id in blocked_users:
                logger.info(f"User {user_id} is blocked. Skipping.")
                continue
            try:
                user = await self.client.get_entity(user_id)
                await self.client(functions.channels.InviteToChannelRequest(
                    channel=target_channel,
                    users=[user]
                ))
                summary["added"].append(user_id)
                logger.info(f"Added user {user_id} to channel.")
                await asyncio.sleep(1)  # To respect rate limits
            except errors.FloodWaitError as e:
                logger.warning(f"Flood wait error: {e}. Sleeping for {e.seconds} seconds.")
                await asyncio.sleep(e.seconds)
                continue
            except errors.UserPrivacyRestrictedError:
                logger.warning(f"User {user_id} has privacy settings that prevent adding to channels.")
                summary["failed"].append(user_id)
                continue
            except errors.UserAlreadyParticipantError:
                logger.info(f"User {user_id} is already a participant of the channel.")
                summary["failed"].append(user_id)
                continue
            except Exception as e:
                logger.error(f"Failed to add user {user_id} to channel: {e}")
                summary["failed"].append(user_id)
                continue

        logger.info(f"Users added: {summary['added']}, Users failed: {summary['failed']}")
        return summary

# =====================
# Main Telegram Bot Class
# =====================

class TelegramBot:
    """
    The main Telegram Bot class handling all interactions and functionalities.
    """

    # Define unique states for ConversationHandlers
    # Generate String Session Conversation States
    GENERATE_SS_API_ID = 1
    GENERATE_SS_API_HASH = 2
    GENERATE_SS_PHONE = 3
    GENERATE_SS_CODE = 4
    GENERATE_SS_PASSWORD = 5

    # Set Apify Token Conversation States
    SET_APIFY_TOKEN_STATE = 6

    # Set Channel Username Conversation States
    SET_CHANNEL_USERNAME_STATE = 7

    # Block User Conversation States
    BLOCK_USER_ID_STATE = 8

    def __init__(self, bot_token: str, webhook_url: str, host: str = "0.0.0.0", port: int = 8443):
        """
        Initialize the TelegramBot with necessary configurations.

        Args:
            bot_token (str): Telegram bot token.
            webhook_url (str): Webhook URL for Telegram updates.
            host (str, optional): Host address. Defaults to "0.0.0.0".
            port (int, optional): Port number. Defaults to 8443.
        """
        self.bot_token = bot_token
        self.webhook_url = webhook_url
        self.host = host
        self.port = port

        # Initialize the TelegramAdder and TelegramChecker as None; will be initialized based on config
        self.adder: TelegramAdder = None
        self.checker: TelegramChecker = None

        # Initialize the Telegram bot application
        self.application = ApplicationBuilder().token(bot_token).build()

        # Register handlers
        self.register_handlers()

        # Initialize components based on existing config
        self.initialize_components()

    def initialize_components(self):
        """
        Initialize TelegramAdder and TelegramChecker based on config.
        """
        telegram_api_id = config.get("telegram_api_id")
        telegram_api_hash = config.get("telegram_api_hash")
        telegram_string_session = config.get("telegram_string_session")
        target_channel_username = config.get("target_channel_username")
        apify_api_token = config.get("apify_api_token")

        if all([telegram_api_id, telegram_api_hash, telegram_string_session, target_channel_username]):
            try:
                self.adder = TelegramAdder(
                    api_id=telegram_api_id,
                    api_hash=telegram_api_hash,
                    string_session=telegram_string_session,
                    target_channel_username=target_channel_username
                )
                logger.info("TelegramAdder initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize TelegramAdder: {e}")
        else:
            logger.warning("TelegramAdder not initialized. Missing configurations.")

        if apify_api_token:
            try:
                self.checker = TelegramChecker(apify_api_token)
                logger.info("TelegramChecker initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize TelegramChecker: {e}")
        else:
            logger.warning("TelegramChecker not initialized. Missing Apify API Token.")

    def register_handlers(self):
        """
        Register all handlers (commands, callbacks, message handlers).
        """

        # -------- Command Handlers --------
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("cancel", self.cancel))

        # -------- CallbackQueryHandlers for Buttons --------
        # Define patterns for callbacks
        callback_patterns = [
            "settings",
            "upload_csv",
            "add_to_channel",
            "manage_blocked",
            "export_data",
            "exit",
            "generate_string_session",
            r"^unblock_user_\d+$",
            "block_user_prompt",
            "back_to_main",
            "set_api_id",
            "set_api_hash",
            "set_string_session",
            "set_apify_token",
            "set_channel_username",
            "export_registered_users",
            "list_user_ids"
        ]

        for pattern in callback_patterns:
            self.application.add_handler(
                CallbackQueryHandler(self.button_handler, pattern=pattern)
            )

        # -------- Conversation Handlers --------
        # Handler for generating StringSession
        conv_handler_generate_ss = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_generate_string_session, pattern='generate_string_session')],
            states={
                self.GENERATE_SS_API_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.generate_ss_api_id)],
                self.GENERATE_SS_API_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.generate_ss_api_hash)],
                self.GENERATE_SS_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.generate_ss_phone)],
                self.GENERATE_SS_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.generate_ss_code)],
                self.GENERATE_SS_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.generate_ss_password)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            allow_reentry=True
        )
        self.application.add_handler(conv_handler_generate_ss)

        # Handler for setting Apify API Token
        conv_handler_set_apify = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_set_apify_token, pattern='set_apify_token')],
            states={
                self.SET_APIFY_TOKEN_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.set_apify_token)]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            allow_reentry=True
        )
        self.application.add_handler(conv_handler_set_apify)

        # Handler for setting Channel Username
        conv_handler_set_channel = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_set_channel_username, pattern='set_channel_username')],
            states={
                self.SET_CHANNEL_USERNAME_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.set_channel_username)]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            allow_reentry=True
        )
        self.application.add_handler(conv_handler_set_channel)

        # Handler for blocking a user
        conv_handler_block_user = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.block_user_prompt, pattern='block_user_prompt')],
            states={
                self.BLOCK_USER_ID_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.block_user_input_handler)]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            allow_reentry=True
        )
        self.application.add_handler(conv_handler_block_user)

        # -------- Message Handlers --------
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.upload_csv_handler))

        # -------- Error Handler --------
        self.application.add_error_handler(self.error_handler)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle the /start command.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        user_id = update.effective_user.id
        if not is_admin(user_id):
            await update.message.reply_text("❌ شما اجازه استفاده از این ربات را ندارید.")
            return

        # Show the main menu keyboard
        keyboard = self.get_main_menu_keyboard()
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "سلام! لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=reply_markup
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle the /help command.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        user_id = update.effective_user.id
        if not is_admin(user_id):
            await update.message.reply_text("❌ شما اجازه استفاده از این ربات را ندارید.")
            return

        help_text = (
            "📄 **دستورات و گزینه‌ها:**\n\n"
            "/start - شروع ربات و نمایش گزینه‌ها\n"
            "/help - نمایش پیام راهنما\n"
            "/cancel - لغو عملیات جاری\n\n"
            "**گزینه‌ها (از طریق دکمه‌ها):**\n"
            "• ⚙️ تنظیمات\n"
            "• 📂 آپلود مخاطبین CSV\n"
            "• ➕ افزودن کاربران به کانال هدف\n"
            "• 🛑 مدیریت کاربران مسدود شده\n"
            "• 📤 صادرات داده‌ها\n"
            "• ❌ خروج کامل\n\n"
            "**نکات:**\n"
            "- فایل‌های CSV باید حاوی شماره تلفن‌ها در فرمت بین‌المللی (مثلاً +1234567890) باشند.\n"
            "- فقط کاربرانی که در لیست ادمین‌ها هستند می‌توانند از این ربات استفاده کنند.\n"
            "- پس از آپلود CSV و پردازش، می‌توانید کاربران ثبت‌شده را به کانال هدف اضافه کنید."
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle the /cancel command to cancel ongoing operations.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        user_id = update.effective_user.id
        if not is_admin(user_id):
            await update.message.reply_text("❌ شما اجازه استفاده از این ربات را ندارید.")
            return

        if update.message:
            await update.message.reply_text('📴 عملیات جاری لغو شد.')
        elif update.callback_query:
            await update.callback_query.edit_message_text('📴 عملیات جاری لغو شد.')
        # Clear any user data state
        context.user_data.clear()
        # Show main menu again
        keyboard = self.get_main_menu_keyboard()
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.message:
            await update.message.reply_text(
                "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
                reply_markup=reply_markup
            )
        elif update.callback_query:
            await update.callback_query.edit_message_text(
                "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
                reply_markup=reply_markup
            )

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle all callback queries from inline buttons.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        query = update.callback_query
        await query.answer()

        data = query.data

        user_id = update.effective_user.id
        if not is_admin(user_id):
            await query.edit_message_text("❌ شما اجازه استفاده از این ربات را ندارید.")
            return

        # Settings Button
        if data == "settings":
            await self.settings_menu(update, context)

        elif data == "upload_csv":
            if not self.checker:
                await query.edit_message_text("❌ لطفاً ابتدا در تنظیمات ربات Apify API Token را تنظیم کنید.")
                return
            await query.edit_message_text("📂 لطفاً فایل CSV حاوی شماره تلفن‌ها را ارسال کنید.")

        elif data == "add_to_channel":
            # Check if CSV has been uploaded and processed
            session_data = get_session(user_id)
            if not session_data.get("results"):
                await query.edit_message_text(
                    "❌ لطفاً ابتدا یک فایل CSV آپلود و پردازش کنید."
                )
                return
            await self.add_to_channel(update, context)

        elif data == "manage_blocked":
            await self.manage_blocked_menu(update, context)

        elif data == "export_data":
            await self.export_data_menu(update, context)

        elif data == "exit":
            await query.edit_message_text("❌ ربات با موفقیت متوقف شد.")
            await self.application.stop()

        elif data.startswith("unblock_user_"):
            try:
                target_user_id = int(data.split("_")[-1])
                await self.unblock_user(update, context, target_user_id)
            except ValueError:
                await query.edit_message_text("❌ شناسه کاربری نامعتبر است.")

        elif data == "back_to_main":
            await self.start_command(update, context)

        # Settings Submenu
        elif data == "set_api_id":
            await self.start_generate_string_session(update, context)

        elif data == "set_api_hash":
            # Start setting API Hash
            await query.edit_message_text("🔧 لطفاً Telegram API Hash را وارد کنید:")
            return

        elif data == "set_string_session":
            # Start setting String Session
            await query.edit_message_text("🔧 لطفاً Telegram String Session را وارد کنید:")
            context.user_data['setting'] = 'string_session'
            return

        elif data == "set_apify_token":
            # Start setting Apify Token
            await query.edit_message_text("🔧 لطفاً Apify API Token را وارد کنید:")
            return  # Transition to Apify Token setting state

        elif data == "set_channel_username":
            # Start setting Channel Username
            await query.edit_message_text("🔧 لطفاً نام کاربری کانال هدف را وارد کنید (با @ شروع کنید، مثلاً @yourchannelusername):")
            return  # Transition to Channel Username setting state

        # Export Data Handlers
        elif data == "export_registered_users":
            await self.export_registered_users(update, context)

        elif data == "list_user_ids":
            await self.list_user_ids(update, context)

        else:
            await query.edit_message_text("❓ گزینه انتخابی نامعتبر است. لطفاً دوباره تلاش کنید.")

    async def settings_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Display the settings menu.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        query = update.callback_query
        await query.answer()

        keyboard = [
            [InlineKeyboardButton("🔧 تنظیم Telegram API ID", callback_data="set_api_id"),
             InlineKeyboardButton("🔧 تنظیم Telegram API Hash", callback_data="set_api_hash")],
            [InlineKeyboardButton("🔧 تنظیم Telegram String Session", callback_data="set_string_session"),
             InlineKeyboardButton("🔧 تنظیم Apify API Token", callback_data="set_apify_token")],
            [InlineKeyboardButton("🔧 تنظیم Target Channel Username", callback_data="set_channel_username")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_main")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "⚙️ **تنظیمات ربات:**\n\n"
            "لطفاً یکی از تنظیمات زیر را انتخاب کنید تا مقدار آن را وارد یا به‌روزرسانی کنید:",
            reply_markup=reply_markup
        )

    async def start_generate_string_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Start the process to generate StringSession.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "🔧 **تولید String Session**\n\n"
            "لطفاً مراحل زیر را دنبال کنید تا String Session خود را تولید و تنظیم کنید.",
            parse_mode=ParseMode.MARKDOWN
        )
        # Start by asking for API ID
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="1️⃣ لطفاً Telegram API ID را وارد کنید:"
        )
        return self.GENERATE_SS_API_ID

    async def generate_ss_api_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle Telegram API ID input.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        text = update.message.text.strip()
        if not text.isdigit():
            await update.message.reply_text("❌ لطفاً یک عدد معتبر برای Telegram API ID وارد کنید:")
            return self.GENERATE_SS_API_ID

        context.user_data['generate_ss_api_id'] = int(text)
        await update.message.reply_text("2️⃣ لطفاً Telegram API Hash را وارد کنید:")
        return self.GENERATE_SS_API_HASH

    async def generate_ss_api_hash(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle Telegram API Hash input.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        text = update.message.text.strip()
        if not text:
            await update.message.reply_text("❌ لطفاً یک Telegram API Hash معتبر وارد کنید:")
            return self.GENERATE_SS_API_HASH

        context.user_data['generate_ss_api_hash'] = text
        await update.message.reply_text("3️⃣ لطفاً شماره تلفن را وارد کنید (با کد کشور، مثلاً +1234567890):")
        return self.GENERATE_SS_PHONE

    async def generate_ss_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle phone number input.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        text = update.message.text.strip()
        if not text.startswith("+") or not text[1:].isdigit():
            await update.message.reply_text("❌ لطفاً یک شماره تلفن معتبر با کد کشور وارد کنید (مثلاً +1234567890):")
            return self.GENERATE_SS_PHONE

        phone_number = text
        context.user_data['generate_ss_phone'] = phone_number
        await update.message.reply_text("🔄 در حال ارسال کد تایید به شماره تلفن شما...")
        await update.message.reply_text("📩 لطفاً کدی که دریافت کردید را وارد کنید:")
        return self.GENERATE_SS_CODE

    async def generate_ss_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle verification code input.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        code = update.message.text.strip()
        phone_number = context.user_data.get('generate_ss_phone')
        api_id = context.user_data.get('generate_ss_api_id')
        api_hash = context.user_data.get('generate_ss_api_hash')

        if not code:
            await update.message.reply_text("❌ لطفاً کد تایید را وارد کنید:")
            return self.GENERATE_SS_CODE

        # Initialize Telethon client for this session
        try:
            telethon_client = TelegramClient(StringSession(), api_id, api_hash)
            await telethon_client.connect()
            if not await telethon_client.is_user_authorized():
                await telethon_client.send_code_request(phone_number)
        except Exception as e:
            logger.error(f"Telethon connection error: {e}")
            await update.message.reply_text("❌ خطا در اتصال به Telegram. لطفاً مجدداً تلاش کنید.")
            return ConversationHandler.END

        try:
            await telethon_client.sign_in(phone=phone_number, code=code)
        except errors.SessionPasswordNeededError:
            await update.message.reply_text("🔐 احراز هویت دو مرحله‌ای فعال است. لطفاً رمز عبور خود را وارد کنید:")
            return self.GENERATE_SS_PASSWORD
        except errors.PhoneCodeInvalidError:
            await update.message.reply_text("❌ کد تایید نامعتبر است. لطفاً دوباره تلاش کنید:")
            return self.GENERATE_SS_CODE
        except Exception as e:
            logger.error(f"Telethon sign_in error: {e}")
            await update.message.reply_text("❌ خطا در احراز هویت. لطفاً مجدداً تلاش کنید.")
            await telethon_client.disconnect()
            return ConversationHandler.END

        # If sign_in is successful
        string_session = telethon_client.session.save()
        config["telegram_string_session"] = string_session
        save_config()
        await update.message.reply_text("✅ **String Session با موفقیت تولید و تنظیم شد!**")
        await telethon_client.disconnect()
        # Reinitialize TelegramAdder with new String Session
        self.initialize_components()
        return ConversationHandler.END

    async def generate_ss_password(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle two-factor authentication password input.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        password = update.message.text.strip()
        if not password:
            await update.message.reply_text("❌ لطفاً رمز عبور را وارد کنید:")
            return self.GENERATE_SS_PASSWORD

        phone_number = context.user_data.get('generate_ss_phone')
        api_id = context.user_data.get('generate_ss_api_id')
        api_hash = context.user_data.get('generate_ss_api_hash')

        try:
            telethon_client = TelegramClient(StringSession(), api_id, api_hash)
            await telethon_client.connect()
            await telethon_client.sign_in(phone=phone_number, password=password)
        except errors.PasswordHashInvalidError:
            await update.message.reply_text("❌ رمز عبور نامعتبر است. لطفاً دوباره تلاش کنید:")
            return self.GENERATE_SS_PASSWORD
        except Exception as e:
            logger.error(f"Telethon sign_in password error: {e}")
            await update.message.reply_text("❌ خطا در احراز هویت. لطفاً مجدداً تلاش کنید.")
            await telethon_client.disconnect()
            return ConversationHandler.END

        # If password sign_in is successful
        string_session = telethon_client.session.save()
        config["telegram_string_session"] = string_session
        save_config()
        await update.message.reply_text("✅ **String Session با موفقیت تولید و تنظیم شد!**")
        await telethon_client.disconnect()
        # Reinitialize TelegramAdder with new String Session
        self.initialize_components()
        return ConversationHandler.END

    async def start_set_apify_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Start the process to set Apify API Token.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "🔧 **تنظیم Apify API Token**\n\n"
            "لطفاً Apify API Token خود را وارد کنید:",
            parse_mode=ParseMode.MARKDOWN
        )
        return self.SET_APIFY_TOKEN_STATE

    async def set_apify_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle Apify API Token input.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        api_token = update.message.text.strip()
        if not api_token:
            await update.message.reply_text("❌ لطفاً یک Apify API Token معتبر وارد کنید:")
            return self.SET_APIFY_TOKEN_STATE

        # Basic validation (Apify tokens are typically long alphanumeric strings)
        if not isinstance(api_token, str) or len(api_token) < 20:
            await update.message.reply_text("❌ لطفاً یک Apify API Token معتبر وارد کنید:")
            return self.SET_APIFY_TOKEN_STATE

        config["apify_api_token"] = api_token
        save_config()

        # Initialize or reinitialize TelegramChecker
        self.checker = TelegramChecker(api_token)
        logger.info("TelegramChecker re-initialized with new Apify API Token.")

        await update.message.reply_text("✅ Apify API Token با موفقیت تنظیم شد.")
        # Return to settings menu
        await self.settings_menu(update, context)
        return ConversationHandler.END

    async def start_set_channel_username(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Start the process to set Target Channel Username.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "🔧 **تنظیم Target Channel Username**\n\n"
            "لطفاً نام کاربری کانال هدف خود را وارد کنید (با @ شروع کنید، مثلاً @yourchannelusername):",
            parse_mode=ParseMode.MARKDOWN
        )
        return self.SET_CHANNEL_USERNAME_STATE

    async def set_channel_username(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle setting the target channel username.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        text = update.message.text.strip()
        if not text.startswith("@"):
            await update.message.reply_text("❌ لطفاً نام کاربری کانال را با @ شروع کنید (مثلاً @yourchannelusername):")
            return self.SET_CHANNEL_USERNAME_STATE  # Reuse the same state

        config["target_channel_username"] = text
        save_config()
        await update.message.reply_text("✅ نام کاربری کانال هدف با موفقیت تنظیم شد.")
        # Reinitialize TelegramAdder with new channel
        self.initialize_components()
        # Return to settings menu
        await self.settings_menu(update, context)
        return ConversationHandler.END

    async def upload_csv_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle CSV file uploads.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        user_id = update.effective_user.id
        if not is_admin(user_id):
            await update.message.reply_text("❌ شما اجازه استفاده از این ربات را ندارید.")
            return

        if update.message.document:
            file = update.message.document
            if not file.file_name.lower().endswith(".csv"):
                await update.message.reply_text("❌ لطفاً یک فایل CSV معتبر ارسال کنید.")
                return

            try:
                # Define a temporary path to save the file
                temp_dir = Path("temp")
                temp_dir.mkdir(exist_ok=True)
                temp_file_path = temp_dir / f"{user_id}_{file.file_name}"

                # Download the file
                await file.get_file().download_to_drive(custom_path=str(temp_file_path))
                await update.message.reply_text("🔄 در حال پردازش فایل CSV شما. لطفاً صبر کنید...")

                # Read phone numbers from CSV
                phone_numbers = self.checker.read_csv(str(temp_file_path))
                if not phone_numbers:
                    await update.message.reply_text("❌ فایل CSV خالی یا نامعتبر است.")
                    return

                # Check Telegram status using Apify
                results = self.checker.check_telegram_status(phone_numbers)

                # Save results in session
                session = get_session(user_id)
                session["results"] = results
                set_session(user_id, session)

                # Save results to CSV
                result_file = Path(f"telegram_results_{user_id}.csv")
                self.checker.save_results(results, str(result_file))

                # Prepare a summary
                total = len(results)
                registered = len([r for r in results if r.get("isRegistered")])
                not_registered = total - registered
                summary = (
                    f"✅ **پردازش کامل شد!**\n\n"
                    f"کل شماره‌ها: {total}\n"
                    f"ثبت‌شده در تلگرام: {registered}\n"
                    f"ثبت‌نشده: {not_registered}"
                )

                # Send summary and the results file
                await update.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN)
                await update.message.reply_document(
                    document=InputFile(str(result_file)),
                    filename=result_file.name,
                    caption="📁 این نتایج بررسی شماره تلفن‌های شما است."
                )

                # Clean up temporary files
                try:
                    temp_file_path.unlink(missing_ok=True)
                    result_file.unlink(missing_ok=True)
                    if not any(temp_dir.iterdir()):
                        temp_dir.rmdir()
                except Exception as e:
                    logger.warning(f"Failed to clean up temporary files: {e}")

            except Exception as e:
                logger.error(f"Error processing CSV: {e}")
                await update.message.reply_text("❌ هنگام پردازش فایل CSV خطایی رخ داد.")
        else:
            await update.message.reply_text("❌ لطفاً یک فایل CSV ارسال کنید.")

    async def add_to_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Add verified users to the target channel.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        query = update.callback_query
        await query.edit_message_text("🔄 در حال افزودن کاربران به کانال هدف. لطفاً صبر کنید...")

        user_id = update.effective_user.id
        session_data = get_session(user_id)
        results = session_data.get("results", [])

        if not results:
            await query.edit_message_text("❌ هیچ داده‌ای برای افزودن وجود ندارد.")
            return

        # Filter registered users with valid user IDs
        registered_users = [r for r in results if r.get("isRegistered") and r.get("userId")]
        if not registered_users:
            await query.edit_message_text("❌ هیچ شماره تلفنی ثبت‌شده در تلگرام یافت نشد.")
            return

        # Get blocked users
        blocked_users = config.get("blocked_users", [])

        # Extract user IDs
        user_ids = [r.get("userId") for r in registered_users if r.get("userId")]

        # Initialize TelegramAdder client
        if not self.adder:
            await query.edit_message_text("❌ ربات به درستی تنظیم نشده است. لطفاً با مدیر تماس بگیرید.")
            return

        try:
            await self.adder.connect()
        except errors.RPCError as e:
            logger.error(f"Telethon connection error: {e}")
            await query.edit_message_text("❌ خطا در اتصال به Telegram. لطفاً بررسی کنید.")
            return
        except Exception as e:
            logger.error(f"Unexpected error during Telethon connection: {e}")
            await query.edit_message_text("❌ خطای غیرمنتظره رخ داد. لطفاً دوباره تلاش کنید.")
            return

        # Add users to channel
        try:
            summary = await self.adder.add_users_to_channel(user_ids, blocked_users)
        except errors.FloodWaitError as e:
            logger.warning(f"Flood wait error: {e}. Sleeping for {e.seconds} seconds.")
            await asyncio.sleep(e.seconds)
            await query.edit_message_text("❌ ربات در حال حاضر با محدودیت سرعت مواجه شده است. لطفاً بعداً دوباره تلاش کنید.")
            return
        except Exception as e:
            logger.error(f"Error adding users to channel: {e}")
            await query.edit_message_text(f"❌ خطایی رخ داد: {e}")
            return
        finally:
            await self.adder.disconnect()

        # Prepare a summary message
        success_count = len(summary["added"])
        failure_count = len(summary["failed"])
        summary_message = (
            f"✅ **افزودن کاربران به کانال کامل شد!**\n\n"
            f"تعداد موفق: {success_count}\n"
            f"تعداد ناموفق: {failure_count}"
        )
        await query.edit_message_text(summary_message, parse_mode=ParseMode.MARKDOWN)

        if summary["added"]:
            added_list = ", ".join(map(str, summary["added"]))
            await query.message.reply_text(f"🟢 **کاربران اضافه شده:**\n{added_list}")

        if summary["failed"]:
            failed_list = ", ".join(map(str, summary["failed"]))
            await query.message.reply_text(f"🔴 **کاربران اضافه نشده:**\n{failed_list}")

    async def manage_blocked_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Display the manage blocked users menu.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        query = update.callback_query
        await query.answer()

        user_id = update.effective_user.id

        blocked_users = config.get("blocked_users", [])
        if not blocked_users:
            blocked_text = "🛑 **لیست کاربران مسدود شده خالی است.**"
        else:
            blocked_text = (
                "🛑 **لیست کاربران مسدود شده:**\n\n"
                + "\n".join([f"• {uid}" for uid in blocked_users])
            )

        keyboard = [
            [InlineKeyboardButton("➕ مسدود کردن کاربر جدید", callback_data="block_user_prompt")]
        ]
        # Dynamically add unblock buttons
        for uid in blocked_users:
            keyboard.append([
                InlineKeyboardButton(
                    f"🔓 بازگشایی مسدودیت کاربر {uid}",
                    callback_data=f"unblock_user_{uid}"
                )
            ])

        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(blocked_text, reply_markup=reply_markup)

    async def block_user_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Prompt admin to enter a user ID to block.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "➕ لطفاً شناسه کاربری تلگرام کاربری که می‌خواهید مسدود کنید را وارد کنید (عدد):"
        )
        return self.BLOCK_USER_ID_STATE

    async def block_user_input_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle input for blocking a new user.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        user_id = update.effective_user.id
        target_user_id_text = update.message.text.strip()
        if not target_user_id_text.isdigit():
            await update.message.reply_text(
                "❌ لطفاً یک شناسه کاربری تلگرام معتبر (عدد) وارد کنید:"
            )
            return self.BLOCK_USER_ID_STATE

        target_user_id = int(target_user_id_text)

        if target_user_id in config.get("blocked_users", []):
            await update.message.reply_text(
                f"🔍 کاربر با شناسه {target_user_id} قبلاً مسدود شده است."
            )
        else:
            config.setdefault("blocked_users", []).append(target_user_id)
            save_config()
            await update.message.reply_text(
                f"✅ کاربر با شناسه {target_user_id} با موفقیت مسدود شد."
            )

        # Return to manage blocked menu
        await self.manage_blocked_menu(update, context)
        return ConversationHandler.END

    async def unblock_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int):
        """
        Unblock a user.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
            target_user_id (int): Telegram user ID to unblock.
        """
        user_id = update.effective_user.id
        blocked_users = config.get("blocked_users", [])

        if target_user_id in blocked_users:
            blocked_users.remove(target_user_id)
            config["blocked_users"] = blocked_users
            save_config()
            await update.callback_query.edit_message_text(
                f"✅ کاربر با شناسه {target_user_id} از لیست مسدود شده‌ها حذف شد."
            )
        else:
            await update.callback_query.edit_message_text(
                f"🔍 کاربر با شناسه {target_user_id} در لیست مسدود شده‌ها یافت نشد."
            )

        await self.manage_blocked_menu(update, context)

    async def export_data_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Display the export data menu.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        query = update.callback_query
        await query.answer()

        user_id = update.effective_user.id
        if not is_admin(user_id):
            await query.edit_message_text("❌ شما اجازه استفاده از این ربات را ندارید.")
            return

        keyboard = [
            [InlineKeyboardButton("📥 صادرات لیست کاربران ثبت‌شده", callback_data="export_registered_users")],
            [InlineKeyboardButton("🔢 لیست شناسه‌های کاربران ثبت‌شده", callback_data="list_user_ids")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_main")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "📤 لطفاً گزینه مورد نظر برای صادرات داده‌ها را انتخاب کنید:",
            reply_markup=reply_markup
        )

    async def export_registered_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Export the list of registered users as a JSON file.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        query = update.callback_query
        await query.answer()

        user_id = update.effective_user.id
        session_data = get_session(user_id)
        results = session_data.get("results", [])
        if not results:
            await query.edit_message_text("❌ هیچ داده‌ای برای صادرات وجود ندارد.")
            return

        registered_users = [r for r in results if r.get("isRegistered") and r.get("userId")]
        if not registered_users:
            await query.edit_message_text("❌ هیچ کاربر ثبت‌شده‌ای یافت نشد.")
            return

        # Save to JSON
        output_file = Path(f"registered_users_{user_id}.json")
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(registered_users, f, indent=4, ensure_ascii=False)
            logger.info(f"Registered users exported to {output_file}.")
        except Exception as e:
            logger.error(f"Failed to export registered users: {e}")
            await query.edit_message_text("❌ خطایی در هنگام صادرات داده‌ها رخ داد.")
            return

        await query.edit_message_text("📤 در حال ارسال فایل صادرات...")
        await query.message.reply_document(
            document=InputFile(str(output_file)),
            filename=output_file.name,
            caption="📁 لیست کاربران ثبت‌شده"
        )

        # Clean up the exported file
        try:
            output_file.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Failed to delete exported file {output_file}: {e}")

    async def list_user_ids(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        List all user IDs processed.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        query = update.callback_query
        await query.answer()

        user_id = update.effective_user.id
        session_data = get_session(user_id)
        results = session_data.get("results", [])
        if not results:
            await query.edit_message_text("❌ هیچ داده‌ای برای نمایش وجود ندارد.")
            return

        user_ids = [str(r.get("userId")) for r in results if r.get("isRegistered") and r.get("userId")]
        if not user_ids:
            user_ids_str = "هیچ کاربری ثبت نشده است."
        else:
            user_ids_str = ", ".join(user_ids)

        await query.edit_message_text(f"🔢 **لیست شناسه‌های کاربران ثبت‌شده:**\n{user_ids_str}")

    async def handle_text_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle general text messages based on user state.

        Args:
            update (Update): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        user_id = update.effective_user.id
        if not is_admin(user_id):
            await update.message.reply_text("❌ شما اجازه استفاده از این ربات را ندارید.")
            return

        # Other text messages can be handled as needed
        await update.message.reply_text(
            "❓ لطفاً از دکمه‌های ارائه شده استفاده کنید یا یک دستور معتبر ارسال کنید."
        )

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """
        Log the error and send a message to the user.

        Args:
            update (object): Telegram update.
            context (ContextTypes.DEFAULT_TYPE): Context for the update.
        """
        logger.error(msg="Exception while handling an update:", exc_info=context.error)
        if isinstance(update, Update) and update.effective_user:
            try:
                await update.effective_message.reply_text(
                    "❌ متاسفانه یک خطا رخ داد. لطفاً دوباره تلاش کنید."
                )
            except Exception as e:
                logger.error(f"Failed to send error message: {e}")

    def get_main_menu_keyboard(self) -> List[List[InlineKeyboardButton]]:
        """
        Return the main menu keyboard.

        Returns:
            list: List of lists containing InlineKeyboardButtons.
        """
        keyboard = [
            [InlineKeyboardButton("⚙️ تنظیمات", callback_data="settings")],
            [
                InlineKeyboardButton("📂 آپلود مخاطبین CSV", callback_data="upload_csv"),
                InlineKeyboardButton("➕ افزودن کاربران به کانال هدف", callback_data="add_to_channel")
            ],
            [InlineKeyboardButton("🛑 مدیریت کاربران مسدود شده", callback_data="manage_blocked")],
            [
                InlineKeyboardButton("📤 صادرات داده‌ها", callback_data="export_data"),
                InlineKeyboardButton("❌ خروج کامل", callback_data="exit")
            ],
        ]
        return keyboard

    async def run(self):
        """
        Start the bot and set the webhook.
        """
        try:
            # Start the application
            await self.application.initialize()
            await self.application.start()

            # Run the webhook
            await self.application.run_webhook(
                listen=self.host,
                port=self.port,
                url_path=self.bot_token,
                webhook_url=self.webhook_url
            )

            logger.info("Bot is running and listening for updates.")
        except Exception as e:
            logger.error(f"Failed to start the bot: {e}")
        finally:
            await self.application.stop()
            logger.info("Bot stopped.")

    # ========================
    # Core Functionalities
    # ========================

    # (The add_to_channel method is already implemented above)

    # ========================
    # Utility Functions
    # ========================

    # (Included within respective methods)

    # ========================
    # Additional Handlers
    # ========================

    # No additional handlers needed beyond ConversationHandlers and existing methods.

# ========================
# Running the Bot
# ========================

def main():
    """
    Initialize and run the Telegram bot.
    """
    # Initialize and run the bot
    bot = TelegramBot(BOT_TOKEN, webhook_url=WEBHOOK_URL)
    asyncio.run(bot.run())

if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.exception(f"Unhandled exception: {e}")
