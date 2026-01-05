# API Key Management

The API supports multiple API keys with rate limiting, managed via CLI.

## CLI Commands

```bash
# Create a new API key with rate limits (all periods available)
python scripts/api_key_cli.py create --name "My App" \
  --per-second 10 --per-minute 100 --hourly 1000 --daily 10000 --monthly 100000

# Create an unlimited key
python scripts/api_key_cli.py create --name "Internal Service"

# List all keys
python scripts/api_key_cli.py list

# Show key details and usage
python scripts/api_key_cli.py show sk-abc12345

# Check current usage
python scripts/api_key_cli.py usage sk-abc12345

# Disable a key
python scripts/api_key_cli.py update sk-abc12345 --disable

# Revoke a key
python scripts/api_key_cli.py revoke sk-abc12345
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `MASTER_API_KEY` | Required for CLI operations | (set in .env) |
| `RATE_LIMIT_ENABLED` | Enable per-key rate limiting | `true` |

## Backward Compatibility

- The `API_KEY` environment variable continues to work unchanged
- Env var keys have no rate limits (unlimited)
- Redis-managed keys are additive - they work alongside the env var key

## Rate Limit Headers

When rate limits are exceeded (429 response), the response includes:

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Maximum requests allowed |
| `X-RateLimit-Remaining` | Remaining requests (0 when exceeded) |
| `X-RateLimit-Reset` | Reset timestamp (ISO format) |
| `X-RateLimit-Period` | Period that was exceeded (per_second/per_minute/hourly/daily/monthly) |

**Note:** Rate limit headers are only included in 429 (Too Many Requests) responses, not in successful responses.

## REST API Endpoints

All admin endpoints require the `MASTER_API_KEY` in the `x-api-key` header.

### List All API Keys

```bash
GET /admin/keys
Headers: x-api-key: <MASTER_API_KEY>
```

### Create API Key

```bash
POST /admin/keys
Headers: x-api-key: <MASTER_API_KEY>
Body: {
  "name": "My App",
  "rate_limits": {
    "per_second": 10,
    "per_minute": 100,
    "hourly": 1000,
    "daily": 10000,
    "monthly": 100000
  }
}
```

### Update API Key

```bash
PATCH /admin/keys/{key_hash}
Headers: x-api-key: <MASTER_API_KEY>
Body: {
  "enabled": false,
  "rate_limits": {"hourly": 500}
}
```

### Revoke API Key

```bash
DELETE /admin/keys/{key_hash}
Headers: x-api-key: <MASTER_API_KEY>
```

### Get Admin Statistics

```bash
GET /admin/stats?hours=24
Headers: x-api-key: <MASTER_API_KEY>
```

## Architecture

| File | Purpose |
|------|---------|
| `src/models/api_key.py` | ApiKeyRecord, RateLimits dataclasses |
| `src/services/api_key_manager.py` | CRUD and rate limiting |
| `src/services/auth.py` | Validation with manager integration |
| `src/api/admin.py` | REST API endpoints for key management |
| `scripts/api_key_cli.py` | CLI management tool |
