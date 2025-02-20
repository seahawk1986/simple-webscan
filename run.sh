#!/usr/bin/env sh
export CONFIG_FILE=./config.yml
python3 -m simple_webscan --host=0.0.0.0 --port=8000 --loglevel=DEBUG

