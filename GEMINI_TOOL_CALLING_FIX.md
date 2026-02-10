# Fixing "Thought signature is not valid" Error

## Problem

When using `google/gemini-2.0-flash` with tool calling via OpenRouter, you may get:
```
OpenRouter API error (400): 'Thought signature is not valid'
```

This is a known issue with Gemini models and tool calling through OpenRouter.

## Solutions

### Option 1: Switch to a Different Model (Recommended)

Update your `.env` file:

```bash
# Instead of:
OR_MODEL=google/gemini-2.0-flash

# Use one of these (better tool calling support):
OR_MODEL=anthropic/claude-3.5-sonnet
# OR
OR_MODEL=openai/gpt-4o-mini
# OR
OR_MODEL=google/gemini-pro  # Older Gemini, but more stable
```

### Option 2: Use Gemini Pro Instead

If you want to stick with Gemini:

```bash
OR_MODEL=google/gemini-pro
```

Gemini Pro has better tool calling support than Gemini 2.0 Flash.

### Option 3: Check OpenRouter Model Status

Some models on OpenRouter have better tool calling support. Check:
- https://openrouter.ai/models
- Look for models with "Function Calling" support

## Why This Happens

Gemini 2.0 Flash uses a different internal format for tool calling ("Thought signatures") that may not be fully compatible with OpenRouter's API wrapper. The error occurs when the model tries to validate the tool call format.

## Recommended Models for Tool Calling

1. **anthropic/claude-3.5-sonnet** - Excellent tool calling, fast
2. **openai/gpt-4o-mini** - Good tool calling, very fast, cheap
3. **google/gemini-pro** - Good tool calling, stable
4. **anthropic/claude-3-haiku** - Fast, good tool calling

## Testing

After changing the model, test with:

```bash
curl -X POST http://localhost:8000/browse \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Find iPhone 15 on rozetka.ua"}'
```

If the error persists, the issue might be with the tool schema format itself.
