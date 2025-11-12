set -euo pipefail

API_BASE=${API_BASE:-"http://127.0.0.1:8000"}
API_KEY=${API_KEY:-"dev-key"}
PROM=${PROMETHEUS_URL:-"http://127.0.0.1:9090"}
TMP_DB="/tmp/nl2sql_dbs/smoke_demo.sqlite"

echo "🧪 Running NL2SQL smoke metrics validation..."
echo "API_BASE=$API_BASE"
echo "PROMETHEUS_URL=$PROM"
echo "TMP_DB=$TMP_DB"

# --- 1. Make sure the DB exists ---
if [ ! -f "$TMP_DB" ]; then
  echo "⚙️  Creating demo database via smoke_run.py..."
  python scripts/smoke_run.py || {
    echo "❌ smoke_run.py failed to create demo DB."
    exit 1
  }
else
  echo "✅ Found existing DB at $TMP_DB"
fi

# --- 2. Upload DB and capture db_id ---
echo "⬆️  Uploading demo DB..."
DB_ID=$(curl -s -X POST "$API_BASE/api/v1/nl2sql/upload_db" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@${TMP_DB}" | jq -r '.db_id')

if [ "$DB_ID" = "null" ] || [ -z "$DB_ID" ]; then
  echo "❌ Failed to upload DB or get db_id."
  exit 1
fi
echo "✅ Uploaded DB_ID: $DB_ID"

# --- 3. Run a few API smoke queries ---
echo "🚀 Sending test queries..."
curl -s -X POST "$API_BASE/api/v1/nl2sql" \
  -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d "{\"db_id\":\"$DB_ID\",\"query\":\"How many artists are there?\"}" | jq .

curl -s -X POST "$API_BASE/api/v1/nl2sql" \
  -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d "{\"db_id\":\"$DB_ID\",\"query\":\"Which customer spent the most?\"}" | jq .

# --- 4. Collect metrics snapshot from Prometheus ---
echo "📊 Checking Prometheus metrics..."
curl -s "$PROM/api/v1/query?query=nl2sql:pipeline_success_ratio" | jq .

curl -s "$PROM/api/v1/query?query=nl2sql:stage_p95_ms" | jq .

echo "✅ Smoke metrics check completed."
