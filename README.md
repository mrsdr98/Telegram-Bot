# Comprehensive Telegram Bot

A robust Telegram bot built with Python that allows admins to manage and automate various tasks, including uploading CSV files of phone numbers, checking Telegram registration status via Apify, adding users to a target channel using Telethon, managing blocked users, and exporting data.

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Commands](#commands)
- [Security Considerations](#security-considerations)
- [Troubleshooting](#troubleshooting)
- [Developer Guide](#developer-guide)
- [License](#license)

## Features

1. **Predefined Configurations**:
    - **Admin IDs**: Define Telegram user IDs with administrative privileges.
    - **Bot Token**: Securely obtained from [BotFather](https://t.me/BotFather).
    - **Webhook URL**: HTTPS endpoint for receiving Telegram updates.

2. **Dynamic Configuration via Bot Interface**:
    - **Settings Button**: Set or update Telegram API ID, API Hash, String Session, Apify API Token, and Target Channel Username.

3. **Functionalities**:
    - **Upload CSV**: Upload a CSV file containing phone numbers to check their Telegram registration status using Apify.
    - **Add to Channel**: Add verified users to a specified Telegram channel using Telethon.
    - **Manage Blocked Users**: Block or unblock specific Telegram user IDs.
    - **Export Data**: Export processed data as CSV or JSON files.

4. **Webhook Integration**: Utilizes `python-telegram-bot`'s built-in webhook support for efficient handling of Telegram updates.

5. **Self-Contained**: All configurations are managed dynamically through the bot interface, with persistence handled via `config.json`.

## Prerequisites

- **Python 3.8+**: Ensure Python is installed. [Download Python](https://www.python.org/downloads/)
- **Telegram Bot Token**: Obtain from [BotFather](https://t.me/BotFather).
- **Telegram API Credentials**:
    - **API ID and API Hash**: Register your application on [my.telegram.org](https://my.telegram.org/apps).
    - **String Session**: Generate using Telethon.
- **Apify API Token**: Sign up on [Apify](https://apify.com/) and obtain your API token.
- **Domain with HTTPS**: Required for setting up Telegram webhooks.
- **Git**: Installed on your local machine. [Download Git](https://git-scm.com/downloads)
- **Server Access**: A server (e.g., VPS) with SSH access to deploy the bot.

## Installation

### **1. Clone the Repository**

```bash
git clone https://github.com/mrsdr98/telegram-bot.git
cd telegram-bot

