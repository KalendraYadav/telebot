import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/bot.db")

    @classmethod
    def validate(cls):
        if not cls.BOT_TOKEN or cls.BOT_TOKEN == "your_bot_token_here":
            raise ValueError("BOT_TOKEN is not set in environment variables.")

config = Config()
