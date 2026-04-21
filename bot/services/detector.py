def detect_intent(text: str):
    """Identify the likely intent of the message."""
    text = text.lower()
    if any(greet in text for greet in ["hello", "hi", "hey"]):
        return "greeting"
    if any(help_req in text for help_req in ["help", "what can you do"]):
        return "help"
    return "general_message"
