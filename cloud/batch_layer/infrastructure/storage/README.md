# Scanner snapshot storage lifecycle

The snapshot builder (`snapshot_builder.py`) writes two things to
`s3://<datalake>/scanner-snapshots/`:

| Key | Purpose | Retention |
|-----|---------|-----------|
| `scanner-snapshots/latest/market_1d.parquet` | Stable key the vectorized scanner always reads. Overwritten every run. | Permanent |
| `scanner-snapshots/history/<date>/market_1d.parquet` | Point-in-time dated copy for reproducibility. | 14 days (lifecycle) |

Without a lifecycle rule the dated copies accumulate ~132 MB/day forever
(~48 GB/year). The dated copies live under a dedicated `history/` subprefix so
the expiry rule can target them WITHOUT ever touching the permanent `latest/`
object (a flat `scanner-snapshots/` expiry would risk deleting `latest/` during
a pipeline outage).

## Apply / update

```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket dev-condvest-datalake \
  --lifecycle-configuration file://scanner_snapshot_lifecycle.json \
  --region ca-west-1

# Verify
aws s3api get-bucket-lifecycle-configuration \
  --bucket dev-condvest-datalake --region ca-west-1
```

Rules:
- `expire-scanner-snapshot-history` — delete `history/` copies 14 days after creation.
- `abort-incomplete-mpu-scanner-snapshots` — clean up failed multipart uploads
  after 7 days (large parquet uploads are multipart). Does not expire completed objects.
