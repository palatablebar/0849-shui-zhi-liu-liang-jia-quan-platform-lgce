# 字段定义

| 字段 | 类型 | 说明 |
|---|---|---|
| `sample_id` | text | 主键 |
| `station_id` | text | 业务字段 |
| `sample_at` | datetime | 业务字段 |
| `concentration` | decimal | 业务字段 |
| `flow_m3s` | decimal | 业务字段 |
| `detection_limit` | decimal_optional | 业务字段 |
| `qualifier` | text | 业务字段 |
| `unit` | text | 业务字段 |

空字符串、`-`、`NA`、`null` 和 `未提供` 按空值处理。金额字段在整理层转为整数分或整数最小单位；日期按 ISO 日期解析；带时间的字段按本地时间解析。标识字段保持为文本，前导零不会被去除。
