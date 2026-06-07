# Kimchi server

Run [kimchi.dev](https://kimchi.dev/) models (kimi-k2.6, minimax-m2.7, etc.) inside Claude Code.

## How it works

Claude Code speaks Anthropic's API format. kimchi.dev speaks OpenAI's. This kimchi.py sits in between: it receives Anthropic-format requests from Claude Code, flattens any structured content blocks (including Claude Code's internal `input_text` context injections) into plain strings, translates the request to OpenAI format, forwards it to kimchi.dev, and converts the response (including SSE streams) back to Anthropic format.

## Setup

1. Add your key to `.env`:

   ```
   KIMCHI_API_KEY=your_key_here
   ```

2. Start the proxy:

   ```bash
   uv run proxy.py
   ```

3. Point Claude Code at it:

   ```bash
   export ANTHROPIC_BASE_URL=http://localhost:4000
   export ANTHROPIC_API_KEY=anything
   ```

4. Use a model:

   ```bash
   claude --model kimi-k2.6
   ```

   Or set it as default:

   ```bash
   claude config set model kimi-k2.6
   ```
