import re

def extract_urls(text: str):
    """Simple URL extraction logic."""
    url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    return re.findall(url_pattern, text)

def extract_hashtags(text: str):
    """Extract hashtags from text."""
    return re.findall(r'#(\w+)', text)
