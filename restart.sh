#!/bin/bash
cd "$(dirname "$0")"
pkill -f 'python main.py'
sleep 2
nohup python main.py > bot.log 2>&1 &
