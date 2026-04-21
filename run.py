import logging
from telegram.ext import ApplicationBuilder
from bot.config import config
from bot.handlers.message_handler import register_handlers
from bot.database.connection import init_db

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def main():
    """Start the bot."""
    print("--- Starting Telebot Reconstructed ---")

    # 1. Validate configuration
    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        print(f"\n[ERROR] {e}")
        print("Please configure your BOT_TOKEN in the .env file.\n")
        return

    # 2. Initialize database
    logger.info("Initializing database...")
    init_db()

    # 3. Create bot application
    logger.info("Building bot application...")
    application = ApplicationBuilder().token(config.BOT_TOKEN).build()

    # 4. Register handlers
    logger.info("Registering handlers...")
    register_handlers(application)

    # 5. Start the bot (FIXED)
    logger.info("Bot is polling...")
    print("\n[SUCCESS] Telebot is now running. Press Ctrl+C to stop.\n")

    application.run_polling()  # ✅ THIS IS THE CORRECT WAY


if __name__ == '__main__':
    main()