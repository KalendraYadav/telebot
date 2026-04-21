# Telebot

A modular, production-ready Telegram bot built with `python-telegram-bot` and `SQLAlchemy`.

## Features
- **Modular Handlers**: Easy to extend bot functionality.
- **Service Layer**: Separation of concerns for business logic.
- **Database Integration**: SQLAlchemy models with SQLite persistence.
- **Environment Driven**: Configuration via `.env`.

## Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment**:
   - Copy `.env.example` to `.env`.
   - Add your `BOT_TOKEN` from [@BotFather](https://t.me/botfather).

3. **Run the Bot**:
   ```bash
   python run.py
   ```

## Project Structure
- `bot/`: Core bot logic.
  - `handlers/`: Interaction logic (commands, messages).
  - `services/`: Business logic.
  - `database/`: Database models and connection.
- `data/`: SQLite database storage.
- `run.py`: Application entry point.
