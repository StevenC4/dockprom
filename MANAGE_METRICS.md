## Get list of metrics, filtered by key prefix

```bash
curl -s http://172.18.0.2:9090/api/v1/metadata | jq '.data | with_entries(select(.key | startswith("animdl_automation")))'
```

## Delete metric
```bash
curl -X POST -g 'http://172.18.0.2:9090/api/v1/admin/tsdb/delete_series?match[]=metric_name
```
