# Token Launch Bot

## Overview
This is a Telegram bot for scheduling and managing token launches across multiple blockchain networks including Ethereum, Arbitrum, Avalanche, Base, BNB, Mantle, and Solana. The bot integrates with the Printr API to create and deploy tokens across chains.

## Project Structure
- `launch_bot.py` - Main Telegram bot with scheduling logic
- `printr_client.py` - API client for Printr token creation service
- `requirements.txt` - Python dependencies
- `launch.json` - Example token launch configuration
- `launches.db` - SQLite database (created on first run)

## Features
- Upload token launch configurations via JSON files
- Schedule single token launches or batch schedules
- Monitor deployment status across multiple chains
- Wallet management for different blockchain networks
- Transaction signing and submission for both EVM and Solana chains

## Setup Instructions

### 1. Configure Environment Variables
Copy `.env.example` to `.env` and fill in the required values:

```bash
cp .env.example .env
```

Required environment variables:
- `TELEGRAM_TOKEN` - Your Telegram Bot token from @BotFather
- `ALLOWED_USER_ID` - Your Telegram user ID for authentication
- `PRINTR_API_URL` - Printr API endpoint
- `PRINTR_BEARER_TOKEN` - Printr API authentication token
- Chain-specific private keys and RPC endpoints for each supported blockchain

### 2. Start the Bot
The bot runs automatically via the configured workflow. To run manually:
```bash
python launch_bot.py
```

### 3. Interact with the Bot
1. Start a conversation with your bot on Telegram
2. Send `/start` command
3. Authenticate with your user ID
4. Configure wallet addresses for each chain
5. Upload JSON files with token launch details
6. Schedule launches using the interactive menu

## Supported Chains
- Ethereum (eip155:1)
- Arbitrum (eip155:42161)
- Avalanche (eip155:43114)
- Base (eip155:8453)
- BNB Chain (eip155:56)
- Mantle (eip155:5000)
- Solana (solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp)

## Database Schema
The bot uses SQLite with two tables:
- `launches` - Stores launch configurations and status
- `wallets` - Stores wallet addresses per chain per user

## Security Notes
- Never commit `.env` file or expose private keys
- Keep your Telegram bot token secure
- Limit access using `ALLOWED_USER_ID`
- Private keys are used for transaction signing

## Recent Changes
- 2025-11-11: Initial setup in Replit environment
- Installed Python 3.11 and all dependencies
- Created .env.example template for configuration
- Updated .gitignore for Python best practices
