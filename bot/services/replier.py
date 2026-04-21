def format_welcome_message(name: str):
    return f"Welcome, {name}! I am Telebot. How can I help you today?"

def format_help_message():
    return (
        "Here's what I can do:\n"
        "- /start - Get started\n"
        "- Send me any message to get a response\n"
        "- I can extract URLs and hashtags from your text!"
    )

def format_general_reply(intent: str):
    if intent == "greeting":
        return "Hello there! Nice to meet you."
    return "I received your message! Thanks for sharing."
