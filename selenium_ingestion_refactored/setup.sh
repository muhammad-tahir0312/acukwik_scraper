#!/bin/bash
# Aviation Data Scraper - Quick Start Script

set -e  # Exit on error

echo "=========================================="
echo "Aviation Data Scraper - Quick Start"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

# Check Python installation
echo "Checking prerequisites..."

if ! command -v python3 &> /dev/null; then
    print_error "Python 3 is not installed!"
    echo "Please install Python 3.7 or higher"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
print_status "Python $PYTHON_VERSION found"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    print_status "Creating virtual environment..."
    python3 -m venv venv
else
    print_warning "Virtual environment already exists"
fi

# Activate virtual environment
print_status "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
print_status "Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Check for Selenium driver
print_status "Checking for Selenium WebDriver..."

if command -v chromedriver &> /dev/null; then
    print_status "ChromeDriver found"
    DRIVER_OK=1
elif command -v geckodriver &> /dev/null; then
    print_status "GeckoDriver (Firefox) found"
    DRIVER_OK=1
else
    print_warning "No WebDriver found!"
    echo ""
    echo "Please install ChromeDriver or GeckoDriver:"
    echo ""
    echo "For Chrome:"
    echo "  Ubuntu/Debian: sudo apt-get install chromium-chromedriver"
    echo "  macOS:         brew install --cask chromedriver"
    echo ""
    echo "For Firefox:"
    echo "  Ubuntu/Debian: sudo apt-get install firefox-geckodriver"
    echo "  macOS:         brew install geckodriver"
    echo ""
    DRIVER_OK=0
fi

# Create necessary directories
print_status "Creating directories..."
mkdir -p output
mkdir -p logs
mkdir -p progress

# Check for config file
if [ ! -f "config.yaml" ]; then
    print_error "config.yaml not found!"
    echo "Please create config.yaml before running the scraper"
    exit 1
fi

print_status "Configuration file found"

# Check for input files
INPUT_FILES=$(grep -A 10 "^input:" config.yaml | grep "csv_paths:" -A 5 | grep "\.csv" | wc -l)

if [ "$INPUT_FILES" -eq 0 ]; then
    print_warning "No input CSV files configured in config.yaml"
    echo ""
    read -p "Would you like to create a sample input file? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        python3 -c "from examples import create_sample_input; create_sample_input()"
        print_status "Created sample_input.csv"
        echo ""
        echo "Update config.yaml to use sample_input.csv:"
        echo "  input:"
        echo "    csv_paths:"
        echo "      - \"sample_input.csv\""
    fi
else
    print_status "Input files configured: $INPUT_FILES"
fi

# Summary
echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""

if [ $DRIVER_OK -eq 1 ]; then
    print_status "All prerequisites met"
    echo ""
    echo "To run the scraper:"
    echo "  1. Activate virtual environment: source venv/bin/activate"
    echo "  2. Run scraper: python scraper.py"
    echo ""
    echo "Optional: Run examples first:"
    echo "  python examples.py"
    echo ""
    
    read -p "Would you like to run the scraper now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo ""
        print_status "Starting scraper..."
        echo ""
        python3 scraper.py
    fi
else
    print_warning "Please install a WebDriver before running the scraper"
    echo ""
    echo "After installing WebDriver, run:"
    echo "  source venv/bin/activate"
    echo "  python scraper.py"
fi

echo ""
print_status "Done!"
