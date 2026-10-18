"""Export contracts without starting the API, reading .env or connecting to services."""

import json
from pathlib import Path

from repoweave.api.app import create_app

if __name__ == '__main__':
    target = Path(__file__).resolve().parents[1] / 'web' / 'openapi.json'
    target.write_text(json.dumps(create_app().openapi(), indent=2) + '\n')
