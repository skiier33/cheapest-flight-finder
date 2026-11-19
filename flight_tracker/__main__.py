"""Entry point: `python -m flight_tracker` or `python run_tracker.py`."""
from .gui import default_db_path, run_app


def main() -> None:
    run_app(default_db_path())


if __name__ == "__main__":
    main()
