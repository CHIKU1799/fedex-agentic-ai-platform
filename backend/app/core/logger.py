from loguru import logger

# File sink is best-effort: serverless bundles (e.g. Vercel) have a
# read-only filesystem, where creating logs/ raises OSError. Console
# logging (loguru's default stderr sink) always works.
try:
    logger.add("logs/app.log", rotation="1 MB")
except OSError:
    pass


def get_logger():
    return logger
