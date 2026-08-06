from loguru import logger

logger.add("logs/app.log", rotation="1 MB")

def get_logger():
    return logger