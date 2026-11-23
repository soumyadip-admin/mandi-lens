#!/usr/bin/env python3
"""Convenience entry point: python run.py"""
import os
from app import config, db
from app.main import app

if __name__ == "__main__":
    db.init()
    port = int(os.environ.get("PORT", 5000))
    print(f"  Mandi Lens → http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port,
            debug=os.environ.get("FLASK_DEBUG", "0") == "1")
