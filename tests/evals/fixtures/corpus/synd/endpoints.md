# API Reference

The API reference provides details on all available endpoints.

## Users

Returns the users list. Use GET /api/users to retrieve all users.

## Billing

Returns billing info. Use GET /api/billing to retrieve billing details.

```python
import requests

def get_billing(account_id: str) -> dict:
    return requests.get(f"/api/billing/{account_id}")
```
