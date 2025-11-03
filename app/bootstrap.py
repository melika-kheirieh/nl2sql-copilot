try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # optional: silently continue if python-dotenv is not installed
    pass
