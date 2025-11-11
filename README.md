# Token Launch Bot

A Telegram bot for scheduling and managing token launches across multiple blockchain networks.

## Quick Start

### 1. Set Up Environment Variables

This bot requires several environment variables to function. Create a `.env` file in the root directory:

```bash
cp .env.example .env
```

Then edit `.env` and fill in your credentials:

**Required Configuration:**
- `TELEGRAM_TOKEN` - Get this from [@BotFather](https://t.me/botfather) on Telegram
- `ALLOWED_USER_ID` - Your Telegram user ID (find it using [@userinfobot](https://t.me/userinfobot))
- `PRINTR_API_URL` - Your Printr API endpoint
- `PRINTR_BEARER_TOKEN` - Your Printr API bearer token

**Blockchain Configuration:**
For each blockchain you want to support, you'll need:
- Creator account address (CAIP-10 format)
- Private key for signing transactions
- RPC endpoint URL

See `.env.example` for the complete list of variables.

### 2. Start the Bot

The bot will start automatically. Once your environment variables are configured, click the "Restart" button in Replit to reload the workflow with your new configuration.

### 3. Using the Bot

1. Open Telegram and find your bot
2. Send `/start` to begin
3. Enter your user ID to authenticate
4. Configure wallet addresses for each blockchain
5. Upload JSON files containing token launch details
6. Use the menu to schedule single launches or batch schedules

## Features

- **Multi-Chain Support**: Deploy tokens on Ethereum, Arbitrum, Avalanche, Base, BNB Chain, Mantle, and Solana
- **Flexible Scheduling**: Schedule individual launches or batch multiple launches with custom intervals
- **Status Monitoring**: Check deployment status across all chains
- **Wallet Management**: Store and update wallet addresses for each blockchain
- **Transaction Signing**: Automatic signing and submission for both EVM and Solana chains

## JSON Format for Token Launches

```json
{
  "launches": [
    {
      "name": "MyToken",
      "symbol": "MTK",
      "description": "Token description here",
      "chains": ["ethereum", "solana"],
      "image": "base64_encoded_image_data_here"
    }
  ]
}
```

See `launch.json` for a complete example.

## Supported Chains

- **ethereum** - Ethereum Mainnet
- **arbitrum** - Arbitrum One
- **avalanche** - Avalanche C-Chain
- **base** - Base
- **bnb** - BNB Chain
- **mantle** - Mantle Network
- **solana** - Solana Mainnet

## Project Files

- `launch_bot.py` - Main bot application with Telegram handlers
- `printr_client.py` - Printr API client for token creation
- `requirements.txt` - Python dependencies
- `launch.json` - Example token configuration
- `.env.example` - Template for environment variables

## Security

- **Never commit** your `.env` file or expose private keys
- The `ALLOWED_USER_ID` setting restricts bot access to only your Telegram account
- Private keys are used only for signing transactions on their respective chains

## Logs

The bot logs all activity to:
- `bot.log` - Main bot operations and errors
- `printr_client.log` - API client operations

Check these files if you encounter issues.

## Troubleshooting

**Bot won't start:**
- Make sure all required environment variables are set in `.env`
- Check that your `TELEGRAM_TOKEN` is valid

**Authentication fails:**
- Verify your `ALLOWED_USER_ID` matches your Telegram user ID

**Transaction signing fails:**
- Ensure private keys are correctly formatted
- Verify RPC endpoints are accessible and working

## Development

The bot uses:
- Python 3.11
- pyTelegramBotAPI for Telegram integration
- APScheduler for job scheduling
- SQLite for local data storage
- Web3.py and Solana.py for blockchain interactions
