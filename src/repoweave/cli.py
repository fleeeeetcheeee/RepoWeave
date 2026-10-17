import argparse

from repoweave.config import ConfigurationError, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="repoweave",
        description="Repository-aware lossless compression experiments",
    )
    parser.parse_args()
    try:
        load_settings()
    except ConfigurationError as error:
        parser.exit(2, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()
