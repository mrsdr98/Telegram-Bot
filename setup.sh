#!/bin/bash

# ==============================
# Telegram Bot Setup Script
# ==============================

# Exit immediately if a command exits with a non-zero status
set -e

# Variables
REPO_URL="https://github.com/mrsdr98/telegram-bot.git"  # Replace with your repository URL
INSTALL_DIR="/opt/telegram-bot"
SERVICE_NAME="telegram_bot.service"

# ==============================
# Functions
# ==============================

# Function to print messages
function echo_info() {
    echo -e "\\033[1;32m$1\\033[0m"
}

function echo_error() {
    echo -e "\\033[1;31m$1\\033[0m" >&2
}

# Check if the script is run as root
if [[ $EUID -ne 0 ]]; then
   echo_error "This script must be run as root. Use sudo."
   exit 1
fi

# Update and install necessary packages
echo_info "Updating system packages..."
apt-get update && apt-get upgrade -y

echo_info "Installing Git and Python3..."
apt-get install -y git python3 python3-venv

# Create a dedicated user for the bot
echo_info "Creating a dedicated user 'telegrambot'..."
if id "telegrambot" &>/dev/null; then
    echo_info "User 'telegrambot' already exists."
else
    useradd -m telegrambot
    echo_info "User 'telegrambot' created successfully."
fi

# Clone the repository
if [ ! -d "$INSTALL_DIR" ]; then
    echo_info "Cloning the repository into $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    chown -R telegrambot:telegrambot "$INSTALL_DIR"
else
    echo_info "Repository already exists at $INSTALL_DIR. Pulling latest changes..."
    cd "$INSTALL_DIR"
    sudo -u telegrambot git pull
fi

cd "$INSTALL_DIR"

# Set up Python virtual environment
echo_info "Setting up Python virtual environment..."
sudo -u telegrambot python3 -m venv venv
source venv/bin/activate

# Install dependencies
echo_info "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# ==============================
# Environment Variables Setup
# ==============================

# Prompt user for environment variables
echo_info "Please enter the following environment variables:"

read -p "Telegram Bot Token: " TELEGRAM_BOT_TOKEN
read -p "Webhook URL (e.g., https://yourdomain.com/YOUR_TELEGRAM_BOT_TOKEN): " WEBHOOK_URL
read -p "Apify API Token: " APIFY_API_TOKEN
read -p "Telegram API ID: " TELEGRAM_API_ID
read -p "Telegram API Hash: " TELEGRAM_API_HASH
read -p "Telegram String Session: " TELEGRAM_STRING_SESSION
read -p "Target Channel Username (e.g., @yourchannelusername): " TARGET_CHANNEL_USERNAME

read -p "Enter Admin Telegram User IDs (comma-separated, e.g., 123456789,987654321): " ADMINS_INPUT

# Convert comma-separated input to JSON array
IFS=',' read -r -a ADMINS_ARRAY <<< "$ADMINS_INPUT"
ADMINS_JSON="["
for i in "${!ADMINS_ARRAY[@]}"; do
    ADMINS_ARRAY[$i]=$(echo "${ADMINS_ARRAY[$i]}" | xargs)  # Trim whitespace
    ADMINS_JSON+="${ADMINS_ARRAY[$i]}"
    if [ $i -lt $((${#ADMINS_ARRAY[@]}-1)) ]; then
        ADMINS_JSON+=", "
    fi
done
ADMINS_JSON+="]"

# Export environment variables
export TELEGRAM_BOT_TOKEN
export WEBHOOK_URL
export APIFY_API_TOKEN
export TELEGRAM_API_ID
export TELEGRAM_API_HASH
export TELEGRAM_STRING_SESSION
export TARGET_CHANNEL_USERNAME
export ADMINS="$ADMINS_JSON"

# Create a .env file
echo_info "Creating .env file..."
cat <<EOL > .env
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
WEBHOOK_URL=$WEBHOOK_URL
APIFY_API_TOKEN=$APIFY_API_TOKEN
TELEGRAM_API_ID=$TELEGRAM_API_ID
TELEGRAM_API_HASH=$TELEGRAM_API_HASH
TELEGRAM_STRING_SESSION=$TELEGRAM_STRING_SESSION
TARGET_CHANNEL_USERNAME=$TARGET_CHANNEL_USERNAME
ADMINS=$ADMINS_JSON
EOL

chown telegrambot:telegrambot .env

# Install python-dotenv to load environment variables
pip install python-dotenv

# Modify bot.py to load environment variables from .env if not already done
if ! grep -q "from dotenv import load_dotenv" bot.py; then
    echo_info "Modifying bot.py to load environment variables from .env..."
    sed -i '1i from dotenv import load_dotenv' bot.py
    sed -i '2i load_dotenv()' bot.py
    chown telegrambot:telegrambot bot.py
fi

# ==============================
# Setup Systemd Service
# ==============================

echo_info "Setting up systemd service..."

# Create systemd service file
cat <<EOL > /etc/systemd/system/$SERVICE_NAME
[Unit]
Description=Telegram Bot Service
After=network.target

[Service]
User=telegrambot
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python3 bot.py
Restart=always
EnvironmentFile=$INSTALL_DIR/.env

[Install]
WantedBy=multi-user.target
EOL

# Reload systemd daemon
echo_info "Reloading systemd daemon..."
systemctl daemon-reload

# Enable and start the service
echo_info "Enabling and starting the Telegram Bot service..."
systemctl enable $SERVICE_NAME
systemctl start $SERVICE_NAME

# Check service status
systemctl status $SERVICE_NAME --no-pager

echo_info "Telegram Bot has been successfully set up and started."
