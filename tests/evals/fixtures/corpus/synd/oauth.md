# OAuth2

Details about OAuth2 integration.

## Client Credentials

To use the client credentials flow, you need a client ID and secret.

```python
def configure_oauth(client_id: str) -> Config:
    return Config(client_id=client_id)
```

## Authorization Code

The authorization code flow requires a redirect URI.
