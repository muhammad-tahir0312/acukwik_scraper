#!/bin/bash

# --- Configuration ---
CSV_FOLDER="countries"              # Change to your folder containing CSVs
SPIDER_NAME="driver_scraper"          # Change to your spider's name
COMMON_ARGS="-a scrape_images=true"   # Arguments common to all runs
OUTPUT_DIR="scraped_outputs"          # Folder to store individual outputs
LOG_DIR="scraped_logs"                # Folder to store logs

# --- Setup ---
mkdir -p "$OUTPUT_DIR"
mkdir -p "$LOG_DIR"

# Check if the CSV folder exists
if [ ! -d "$CSV_FOLDER" ]; then
  echo "Error: CSV folder '$CSV_FOLDER' not found."
  exit 1
fi

# Find all CSV files and sort them
# mapfile reads lines from stdin into an array
mapfile -t CSV_FILES < <(find "$CSV_FOLDER" -maxdepth 1 -type f -name "*.csv" | sort)

# Check if any CSV files were found
if [ ${#CSV_FILES[@]} -eq 0 ]; then
    echo "No CSV files found in '$CSV_FOLDER'."
    exit 0
fi

echo "Found ${#CSV_FILES[@]} CSV files. Starting sequential scrape..."

# --- Main Loop ---
COUNTER=1
TOTAL=${#CSV_FILES[@]}

for CSV_FILE_PATH in "${CSV_FILES[@]}"; do
    # Extract just the filename (e.g., data_AFGHANISTAN.csv)
    CSV_FILENAME=$(basename "$CSV_FILE_PATH")
    # Create a base name for output/log files (e.g., data_AFGHANISTAN)
    BASENAME="${CSV_FILENAME%.*}"
    
    echo "----------------------------------------"
    echo "[$COUNTER/$TOTAL] Processing: $CSV_FILENAME"
    echo "----------------------------------------"
    
    # Define output and log filenames for this run
    OUTPUT_FILE="$OUTPUT_DIR/${BASENAME}_output.json"
    LOG_FILE="$LOG_DIR/${BASENAME}_log.txt"
    
    # Build the Scrapy command
    CMD="scrapy crawl $SPIDER_NAME -a f=\"$CSV_FILE_PATH\" $COMMON_ARGS -o \"$OUTPUT_FILE\""
    
    echo "Running command: $CMD"
    echo "Log file: $LOG_FILE"
    echo "Output file: $OUTPUT_FILE"
    
    # Execute the command and wait for it to finish
    # Redirect stdout and stderr to the log file
    if eval "$CMD" > "$LOG_FILE" 2>&1; then
        echo "SUCCESS: Completed scraping for $CSV_FILENAME"
    else
        echo "ERROR: Failed scraping for $CSV_FILENAME (see $LOG_FILE for details)"
        # Optional: Uncomment the next line to stop the script on the first error
        # exit 1 
    fi
    
    echo ""
    ((COUNTER++))
done

echo "----------------------------------------"
echo "All countries processed."
echo "----------------------------------------"