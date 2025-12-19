# NYC Distress Signal API

Real-time property distress intelligence for NYC. Aggregates data from multiple municipal sources to compute a **Distress Score** (0-100) for any NYC property.

## Data Sources

- **NYC 311 Complaints** - Illegal conversions, heat/water issues, noise complaints
- **NYC Department of Buildings (DOB)** - Open violations, Stop Work Orders, Vacate Orders
- **NYC HPD Violations** - Housing Preservation & Development Class A/B/C violations

## Quick Start

### Prerequisites

- Python 3.9+
- Playwright (for DOB scraping)

### Installation

```bash
# Clone the repository
git clone <your-repo-url>
cd distressed-nyc-signals

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Copy environment config
cp .env.example .env

# Start the server
uvicorn main:app --reload
```

### Docker

```bash
# Build and run with Docker Compose
docker-compose up --build

# Or build manually
docker build -t nyc-distress-api .
docker run -p 8000:8000 nyc-distress-api
```

## API Endpoints

### Analyze Property

```http
POST /v1/analyze
Content-Type: application/json

{
  "house_number": "123",
  "street": "Broadway",
  "borough": "Manhattan"
}
```

Returns distress score, signals, and summary.

### Agent Endpoint (LLM Optimized)

```http
POST /v1/agent
```

Minified response for AI agents to reduce token usage.

### Property Timeline

```http
POST /v1/timeline
```

Historical timeline of complaints and violations.

### Health Check

```http
GET /health
```

Returns API health status.

## Configuration

See `.env.example` for all available configuration options including:

- Debug mode
- API authentication settings
- NYC OpenData credentials
- Cache settings
- Rate limiting

## Authentication

The API supports tiered API key authentication. See `.env.example` for configuration.

When `REQUIRE_API_KEY=true`, all requests must include:

```
Authorization: Bearer <your-api-key>
```

## Rate Limiting

The API includes tiered rate limiting based on subscription level. Limits are applied per API key or per IP for unauthenticated requests.

## Architecture

```
app/
├── middleware/       # Auth, rate limiting, security, logging
├── clients/          # NYC OpenData API clients (311, HPD)
├── scrapers/         # DOB BIS web scraper with circuit breaker
├── services/         # Scoring algorithm, geocoder, cache
├── routes/           # API endpoints (v1, admin)
└── models.py         # Pydantic models
```

## Security

This API implements several security measures:

- Input validation and sanitization
- Rate limiting with IP spoofing protection
- Security headers (HSTS, CSP, X-Frame-Options, etc.)
- Constant-time authentication comparisons
- Request body size limits
- Response size limits

For production deployments:
- Set `REQUIRE_API_KEY=true`
- Configure `ADMIN_MASTER_KEY` with a strong random value
- Set `DEBUG=false`
- Configure `CORS_ORIGINS` to your specific domains
- Configure `TRUSTED_PROXIES` if behind a load balancer

## License

MIT
