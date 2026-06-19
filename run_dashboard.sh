#!/bin/bash
# Setup and run script for Linux/macOS
if [ ! -d "venv" ]; then
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

export PYTHONPATH=$PYTHONPATH:$(pwd)/src
python src/dashboard.py
