#!/usr/bin/env python3
"""
Setup verification script for Token Launch Bot.
Run this to check if all required environment variables are configured.
"""

import os
from dotenv import load_dotenv

load_dotenv()

REQUIRED_VARS = [
    "TELEGRAM_TOKEN",
    "ALLOWED_USER_ID",
    "PRINTR_API_URL",
    "PRINTR_BEARER_TOKEN",
]

CHAIN_VARS = [
    "CHAIN_ETHEREUM",
    "CHAIN_ARBITRUM",
    "CHAIN_AVALANCHE",
    "CHAIN_BASE",
    "CHAIN_BNB",
    "CHAIN_MANTLE",
    "CHAIN_SOLANA",
]

CREATOR_VARS = [
    "CREATOR_ETHEREUM",
    "CREATOR_ARBITRUM",
    "CREATOR_AVALANCHE",
    "CREATOR_BASE",
    "CREATOR_BNB",
    "CREATOR_MANTLE",
    "CREATOR_SOLANA",
]

PRIVATE_KEY_VARS = [
    "PRIVATE_KEY_ETHEREUM",
    "PRIVATE_KEY_ARBITRUM",
    "PRIVATE_KEY_AVALANCHE",
    "PRIVATE_KEY_BASE",
    "PRIVATE_KEY_BNB",
    "PRIVATE_KEY_MANTLE",
    "PRIVATE_KEY_SOLANA",
]

RPC_VARS = [
    "RPC_ETHEREUM",
    "RPC_ARBITRUM",
    "RPC_AVALANCHE",
    "RPC_BASE",
    "RPC_BNB",
    "RPC_MANTLE",
    "RPC_SOLANA",
]

def check_vars(var_list, category):
    """Check if variables in the list are set."""
    missing = []
    configured = []
    
    for var in var_list:
        value = os.getenv(var)
        if not value or value.startswith("your_") or value.startswith("0x"):
            missing.append(var)
        else:
            configured.append(var)
    
    return configured, missing

def main():
    print("=" * 60)
    print("Token Launch Bot - Setup Verification")
    print("=" * 60)
    print()
    
    all_configured = True
    
    # Check required core variables
    print("📋 Core Configuration:")
    configured, missing = check_vars(REQUIRED_VARS, "Core")
    for var in configured:
        print(f"  ✅ {var}")
    for var in missing:
        print(f"  ❌ {var} - NOT CONFIGURED")
        all_configured = False
    print()
    
    # Check chain mappings
    print("🔗 Chain Mappings:")
    configured, missing = check_vars(CHAIN_VARS, "Chain")
    for var in configured:
        print(f"  ✅ {var}")
    for var in missing:
        print(f"  ⚠️  {var} - NOT CONFIGURED (optional)")
    print()
    
    # Check creator accounts
    print("👤 Creator Accounts:")
    configured, missing = check_vars(CREATOR_VARS, "Creator")
    for var in configured:
        print(f"  ✅ {var}")
    for var in missing:
        print(f"  ⚠️  {var} - NOT CONFIGURED (optional)")
    print()
    
    # Check private keys
    print("🔐 Private Keys:")
    configured, missing = check_vars(PRIVATE_KEY_VARS, "Private Key")
    for var in configured:
        print(f"  ✅ {var}")
    for var in missing:
        print(f"  ⚠️  {var} - NOT CONFIGURED (optional)")
    print()
    
    # Check RPC endpoints
    print("🌐 RPC Endpoints:")
    configured, missing = check_vars(RPC_VARS, "RPC")
    for var in configured:
        print(f"  ✅ {var}")
    for var in missing:
        print(f"  ⚠️  {var} - NOT CONFIGURED (optional)")
    print()
    
    print("=" * 60)
    if all_configured:
        print("✅ All required variables are configured!")
        print("Your bot is ready to start.")
    else:
        print("❌ Some required variables are missing.")
        print("Please configure them in your .env file.")
        print()
        print("To fix this:")
        print("1. Copy .env.example to .env")
        print("2. Edit .env and fill in the missing values")
        print("3. Restart the bot")
    print("=" * 60)

if __name__ == "__main__":
    main()
