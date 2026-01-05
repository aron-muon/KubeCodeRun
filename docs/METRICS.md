# Detailed Usage Metrics

Track per-execution, per-language, and per-API-key metrics.

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `DETAILED_METRICS_ENABLED` | Enable detailed metrics | `true` |
| `METRICS_BUFFER_SIZE` | In-memory buffer size | `10000` |
| `METRICS_ARCHIVE_ENABLED` | Archive to MinIO | `true` |
| `METRICS_ARCHIVE_RETENTION_DAYS` | Archive retention | `90` days |
| `SQLITE_METRICS_ENABLED` | Enable SQLite-based metrics storage | `true` |
| `SQLITE_METRICS_DB_PATH` | Path to SQLite metrics database | `data/metrics.db` |
| `METRICS_EXECUTION_RETENTION_DAYS` | Retain individual execution records | `90` days |
| `METRICS_DAILY_RETENTION_DAYS` | Retain daily aggregate records | `365` days |
| `METRICS_AGGREGATION_INTERVAL_MINUTES` | Aggregation interval | `60` minutes |

## API Endpoints

### Public Metrics Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /metrics` | System metrics, execution and API statistics |
| `GET /metrics/execution` | Code execution metrics and statistics |
| `GET /metrics/api` | API request metrics and statistics |
| `GET /metrics/detailed` | Summary with language breakdown |
| `GET /metrics/by-language` | Per-language execution stats |
| `GET /metrics/by-api-key/{hash}` | Per-API-key usage (first 16 chars of key hash) |
| `GET /metrics/pool` | Container pool hit rates |

### Admin Dashboard Endpoints

All admin endpoints require the `MASTER_API_KEY` in the `x-api-key` header.

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/admin/metrics/summary` | Summary statistics for selected period |
| `GET /api/v1/admin/metrics/languages` | Language usage data for charts |
| `GET /api/v1/admin/metrics/time-series` | Time-series data for line charts |
| `GET /api/v1/admin/metrics/heatmap` | Hourly activity heatmap data |
| `GET /api/v1/admin/metrics/api-keys` | List of API keys for filtering |
| `GET /api/v1/admin/metrics/top-languages` | Top languages by execution count |

## Tracked Metrics

**Per-execution:**
- Language, execution time, memory usage, status, files generated, container source

**Per-language:**
- Execution count, error rates, average execution times

**Per-API-key:**
- Request counts, resource consumption

**Pool:**
- Hit rate, cold starts, exhaustion events

## Architecture

| File | Purpose |
|------|---------|
| `src/models/metrics.py` | DetailedExecutionMetrics, LanguageMetrics, ApiKeyUsageMetrics |
| `src/services/metrics.py` | In-memory metrics collector with Redis persistence |
| `src/services/detailed_metrics.py` | Detailed metrics collection service |
| `src/services/sqlite_metrics.py` | SQLite-based persistent metrics storage |
| `src/services/orchestrator.py` | Records metrics after execution |
| `src/middleware/metrics.py` | API request metrics middleware |
| `src/api/dashboard_metrics.py` | Admin dashboard metrics endpoints |

## Storage Backends

Metrics are stored in multiple backends:
- **Redis**: Short-term storage for in-memory buffers and real-time metrics
- **SQLite**: Long-term persistent storage for historical analytics
- **MinIO**: Archive storage for metric exports (when enabled)
