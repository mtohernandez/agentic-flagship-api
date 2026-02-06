import logging


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        force=True,
    )

    for noisy in ("httpx", "httpcore", "playwright", "langchain", "langgraph"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
