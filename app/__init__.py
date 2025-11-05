import os
import pathlib
from flask import Flask

LOG_PATH = pathlib.Path(os.getenv("PROGRESS_LOG_PATH", "progress_events.jsonl"))

def create_app():
    app = Flask(__name__)

    from .routes_dashboard import bp as dashboard_bp
    from .routes_api import bp as api_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    return app
