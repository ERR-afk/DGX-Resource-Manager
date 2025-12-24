#!/bin/bash

# Get the directory where this script is running
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

while true; do
    # Run the generic python script
    python3 "$DIR/gpu_manager.py" > "$DIR/current_gpu_status.log" 2>&1
    sleep 60
done
