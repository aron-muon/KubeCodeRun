# Performance Guide

This document provides performance benchmarks, tuning recommendations, and monitoring guidance for the Code Interpreter API.

## Performance Benchmarks

### Baseline Metrics (With Optimizations)

The following metrics represent typical performance with all optimizations enabled (pod pooling, HTTP sidecar):

| Metric                         | Value      | Notes                            |
| ------------------------------ | ---------- | -------------------------------- |
| **Python execution (simple)**  | 50-100ms   | With warm pod pool               |
| **Python execution (complex)** | 100-300ms  | Depends on code complexity       |
| **JavaScript execution**       | 50-150ms   | With pod pool                    |
| **Pod acquisition**            | ~50-100ms  | From pre-warmed pool             |
| **Cold start (Jobs)**          | 3-10s      | Languages with poolSize=0        |
| **State serialization**        | 1-25ms     | Depends on state size            |
| **File upload (1MB)**          | 50-100ms   | To MinIO                         |

### Performance Comparison

| Configuration            | Python Simple | Notes                         |
| ------------------------ | ------------- | ----------------------------- |
| Warm Pod Pool (default)  | 50-100ms      | ~85% P99 latency reduction    |
| Kubernetes Jobs          | 3-10s         | Cold start for each request   |

---

## Optimization Features

### 1. Pod Pool

Pre-warmed Kubernetes pods eliminate cold start latency:

```
Without Pool (Jobs):
Request → Create Job → Pod Scheduled → Start → Execute → Cleanup
         [~1-3s]       [~1-2s]         [~1s]   [~50ms]   [~1s]
         Total: ~3-10s

With Pool:
Request → Acquire from Pool → Execute via Sidecar → Destroy → (Background: Replenish)
         [~10-50ms]           [~50-100ms]           [~50ms]
         Total: ~100-200ms
```

**Configuration:**

```bash
POD_POOL_ENABLED=true
POD_POOL_WARMUP_ON_STARTUP=true
POD_POOL_PY=5                       # Python pool size
POD_POOL_JS=2                       # JavaScript pool size
# Languages with poolSize=0 use Kubernetes Jobs

# Pool optimization settings
POD_POOL_PARALLEL_BATCH=5           # Pods to start in parallel during warmup
POD_POOL_REPLENISH_INTERVAL=2       # Seconds between replenishment checks
POD_POOL_EXHAUSTION_TRIGGER=true    # Immediate replenish when pool exhausted
```

### 2. HTTP Sidecar Communication

Each execution pod has an HTTP sidecar that handles communication:

```
Request Flow:
API → HTTP POST to Sidecar → Sidecar executes code → Response
      [~5-10ms]              [~50-100ms]              [~5ms]
      Total: ~60-115ms
```

**Sidecar Endpoints:**

- `POST /execute` - Execute code with optional state
- `POST /files` - Upload files to shared volume
- `GET /files` - List/download generated files
- `GET /health` - Health check

**Pre-imported libraries (Python):**

- numpy, pandas, matplotlib, scipy
- sklearn, statsmodels
- json, csv, datetime, collections

### 3. Connection Pooling

Redis connections are pooled for efficiency:

```bash
REDIS_MAX_CONNECTIONS=20
REDIS_SOCKET_TIMEOUT=5
REDIS_SOCKET_CONNECT_TIMEOUT=5
```

---

## Configuration for Performance

### Pool Size Recommendations

| Usage Pattern          | Python Pool | JS Pool | Other Languages |
| ---------------------- | ----------- | ------- | --------------- |
| Light (< 10 req/min)   | 2           | 1       | 0 (use Jobs)    |
| Medium (10-50 req/min) | 5           | 2       | 0 (use Jobs)    |
| Heavy (> 50 req/min)   | 10          | 5       | 2               |

**Trade-offs:**

- Higher pool size = more cluster resources, faster responses
- Pool size of 0 = use Kubernetes Jobs (3-10s cold start)

### Memory Allocation

Each execution pod uses memory:

| Language          | Base Memory | With Code | Recommendation    |
| ----------------- | ----------- | --------- | ----------------- |
| Python            | ~150MB      | 200-500MB | 512Mi limit       |
| JavaScript        | ~50MB       | 100-200MB | 256Mi limit       |
| Go                | ~20MB       | 50-150MB  | 256Mi limit       |
| Java              | ~100MB      | 200-400MB | 512Mi limit       |

**Configuration:**

```bash
K8S_MEMORY_LIMIT=512Mi      # Default per pod
K8S_MEMORY_REQUEST=128Mi    # Request per pod
```

### State Persistence Tuning

For optimal state persistence performance:

```bash
# Faster state operations (smaller states)
STATE_MAX_SIZE_MB=10

# Less frequent archival (reduces MinIO operations)
STATE_ARCHIVE_CHECK_INTERVAL_SECONDS=600

# Longer Redis TTL (fewer archive restorations)
STATE_TTL_SECONDS=14400  # 4 hours
```

---

## Latency Breakdown

### Typical Python Request (Pod Pool)

```
Component                   Time
──────────────────────────────────
Request parsing             ~1ms
Authentication              ~1ms
Session lookup              ~2ms
State load (if exists)      ~3ms
Pod acquire from pool       ~10-50ms
HTTP sidecar communication  ~5ms
Code execution              ~50ms
State save                  ~3ms
Response building           ~2ms
──────────────────────────────────
Total                       ~80-120ms
```

### Request with File Operations

```
Component                   Time
──────────────────────────────────
Request parsing             ~1ms
Authentication              ~1ms
Session lookup              ~2ms
File upload to pod          ~10ms (1MB file)
Pod acquire from pool       ~10-50ms
Code execution              ~50ms
Output file detection       ~5ms
File download from pod      ~10ms
MinIO upload                ~20ms
Response building           ~2ms
──────────────────────────────────
Total                       ~115-155ms
```

---

## Scaling Guidelines

### Concurrent Requests

The API handles concurrent requests efficiently:

| Concurrency | Response Time (p50) | Response Time (p99) |
| ----------- | ------------------- | ------------------- |
| 1           | 35ms                | 50ms                |
| 5           | 40ms                | 80ms                |
| 10          | 50ms                | 150ms               |
| 20          | 100ms               | 300ms               |
| 50          | 200ms               | 500ms               |

**Bottlenecks at high concurrency:**

1. Pod pool exhaustion (wait for replenishment)
2. Redis connection pool saturation
3. Kubernetes API server throughput

### Horizontal Scaling

For high-throughput deployments:

1. **Multiple API replicas**: Use Kubernetes Deployment with HPA
2. **Shared Redis**: All replicas use same Redis for sessions/state
3. **Shared MinIO**: All replicas use same MinIO for files
4. **Node autoscaling**: Enable cluster autoscaler for execution pods

```
                    ┌─────────────────┐
                    │  K8s Ingress    │
                    └────────┬────────┘
             ┌───────────────┼───────────────┐
             ▼               ▼               ▼
      ┌──────────┐    ┌──────────┐    ┌──────────┐
      │  API     │    │  API     │    │  API     │
      │  Pod 1   │    │  Pod 2   │    │  Pod 3   │
      └────┬─────┘    └────┬─────┘    └────┬─────┘
           │               │               │
           └───────────────┼───────────────┘
                    ┌──────┴──────┐
                    │   Redis     │
                    │   MinIO     │
                    └─────────────┘
```

### Resource Planning

| Daily Requests | API Replicas | Pod Pool Size | Redis Memory | MinIO Storage |
| -------------- | ------------ | ------------- | ------------ | ------------- |
| 1,000          | 1            | 5 Python      | 256MB        | 1GB           |
| 10,000         | 2            | 10 Python     | 512MB        | 5GB           |
| 100,000        | 5            | 15 Python     | 2GB          | 20GB          |
| 1,000,000      | 20           | 20 Python     | 8GB          | 100GB         |

---

## Monitoring

### Key Metrics

Monitor these metrics for performance insights:

| Metric                | Source               | Alert Threshold |
| --------------------- | -------------------- | --------------- |
| Request latency (p99) | `/metrics/api`       | > 500ms         |
| Execution time (p99)  | `/metrics/execution` | > 200ms         |
| Pool utilization      | `/metrics`           | > 80%           |
| Pool wait time        | `/metrics`           | > 100ms         |
| Redis latency         | Redis SLOWLOG        | > 10ms          |
| State size (avg)      | Logs                 | > 5MB           |

### Monitoring Endpoints

```bash
# Overall system metrics
curl https://localhost/metrics -H "x-api-key: $API_KEY"

# Execution-specific metrics
curl https://localhost/metrics/execution -H "x-api-key: $API_KEY"

# API request metrics
curl https://localhost/metrics/api -H "x-api-key: $API_KEY"

# Health with detailed timings
curl https://localhost/health/detailed -H "x-api-key: $API_KEY"
```

### Performance Alerts

Recommended alert conditions:

```yaml
# High latency
- condition: request_latency_p99 > 500ms
  duration: 5m
  severity: warning

# Pool exhaustion
- condition: pool_wait_time_avg > 100ms
  duration: 2m
  severity: critical

# State size growing
- condition: state_size_avg > 10MB
  duration: 1h
  severity: warning
```

---

## Troubleshooting

### High Latency

1. **Check pool utilization**:

   ```bash
   curl https://localhost/metrics | jq '.pool'
   ```

   If pool is frequently exhausted, increase `POD_POOL_PY`.

2. **Check Redis latency**:

   ```bash
   redis-cli --latency
   ```

   If > 10ms, consider Redis tuning or dedicated instance.

3. **Check pod health**:
   ```bash
   kubectl -n librecodeinterpreter get pods -l app.kubernetes.io/managed-by=librecodeinterpreter
   ```
   If pods are unhealthy, check logs with `kubectl logs`.

### Pool Exhaustion

1. **Increase pool size**:

   ```bash
   POD_POOL_PY=10
   POD_POOL_JS=5
   ```

2. **Check for slow executions**:
   Long-running code blocks pods. Consider timeout reduction:

   ```bash
   MAX_EXECUTION_TIME=15
   ```

3. **Check pod cleanup**:
   Pods should be destroyed immediately. Check for orphaned pods:
   ```bash
   kubectl -n librecodeinterpreter get pods -l app.kubernetes.io/managed-by=librecodeinterpreter
   ```

### Memory Issues

1. **Check pod memory**:

   ```bash
   kubectl top pods -n librecodeinterpreter
   ```

2. **Reduce state size limit**:

   ```bash
   STATE_MAX_SIZE_MB=25
   ```

3. **Check for memory leaks in user code**:
   Review execution patterns for memory-intensive operations.

---

## Performance Testing

Run performance tests with the included script:

```bash
# Activate virtual environment
source .venv/bin/activate

# Install dependencies
pip install aiohttp

# Run performance tests
python scripts/perf_test.py
```

The script tests:

- Simple Python execution
- Complex Python execution
- Concurrent requests
- State persistence overhead
- File operations

---

## Related Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture
- [CONFIGURATION.md](CONFIGURATION.md) - All configuration options
- [STATE_PERSISTENCE.md](STATE_PERSISTENCE.md) - State persistence guide
