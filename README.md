# Dumb Bot

A Discord bot that answers pings badly, powered by free LLM providers with automatic failover.

## Setup
1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill in your Discord token plus any provider keys you have. Providers without a key are skipped.
3. In the Discord developer portal, enable **Message Content Intent**.
4. `python bot.py`

## Notes
- Memory: last 21 messages per server (pinger + bot only), stored in `memory.db`.
- Limits and unlimited user IDs are configured in `.env`.
- Provider list sourced from [Free-LLM](https://github.com/nejib1/Free-LLM). URLs and models change often, so verify them.
- Never commit your real `.env`. Each person should use their own keys.
- Some providers are unverified third parties and may log prompts. Don't send them anything private.
- 
