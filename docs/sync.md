# Syncing from the SD Submit API

`admin.py <deployment> fetch` loads published submissions from metadata-submitter (the SD Submit
API) through its `/sync` endpoints. For this to work, sd-search-api must be registered with
metadata-submitter as a sync client.

The deployment is the first argument of `admin.py`. A deployment supports `fetch` only if it reads
submissions from metadata-submitter, which `Bigpicture` deployment does. The examples below use 
Bigpicture.

The sync endpoints do not use a shared secret. sd-search-api signs a token for each request with
its own private key, and the token expires after 60 seconds. metadata-submitter holds only the
matching public key, so the private key never leaves sd-search-api.

## 1. Generate a signing key

The tokens are signed with ES256, so the key must be a P-256 elliptic curve key. Generate it on
the sd-search-api side and keep the private key secret, e.g.:

```bash
openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt -out sync-private.pem
```

Derive the public key from it, e.g.:

```bash
openssl ec -in sync-private.pem -pubout -out sync-public.pem
```

Both sides take the keys base64-encoded on a single line:

```bash
openssl base64 -A -in sync-private.pem
```

```bash
openssl base64 -A -in sync-public.pem
```

## 2. Configure sd-search-api

| Variable                | Value                                                                       |
|-------------------------|-----------------------------------------------------------------------------|
| `SD_SUBMIT_API_URL`     | The metadata-submitter base URL without `/sync`, e.g. `https://<host>/api`. |
| `SD_SUBMIT_PRIVATE_KEY` | The base64-encoded private key.                                             |
| `SD_SUBMIT_AUDIENCE`    | The same string as metadata-submitter's `SYNC_AUDIENCE`.                    |
| `SD_SUBMIT_ISSUER`      | Optional. Defaults to `sd-search-api`. Must match `iss` in step 3.          |

Set them wherever `admin.py <deployment> fetch` runs. Set them on the server too: it publishes the public
key at `GET /jwk`, and it publishes no key unless all of them are set.

## 3. Register with metadata-submitter

Send the metadata-submitter admin the base64-encoded **public** key and the issuer name. Never
send the private key. They add the client to their configuration and restart the service:

```
SYNC_CLIENTS=[{"iss": "sd-search-api", "public_keys": ["<base64-encoded public key>"]}]
SYNC_AUDIENCE=<agreed audience, e.g. https://<host>/api/sync>
```

The audience can be any string, as long as both sides use exactly the same one. The sync URL is
a natural choice. metadata-submitter does not mount the `/sync` endpoints unless `SYNC_CLIENTS`
is set.

## 4. Verify

Read the published submissions without writing anything:

```bash
uv run python scripts/admin.py Bigpicture fetch --dry-run
```

This still reads the incremental load marker, so it needs the Postgres settings described in
[Using admin.py](../README.md#using-adminpy).

If it fails, the error names the status metadata-submitter answered with:

- `401` — the issuer, audience or key does not match between the two sides.
- `404` — `SD_SUBMIT_API_URL` is wrong, or `SYNC_CLIENTS` is not set on metadata-submitter.

Then load and sync:

```bash
uv run python scripts/admin.py Bigpicture fetch --sync
```

## Rotating the key

metadata-submitter accepts a token signed with any of the client's public keys, because the
token names no key. So a key can be replaced without either side ever rejecting a token:

1. Generate a new key pair.
2. Have the new public key added to `public_keys`, beside the old one.
3. Switch `SD_SUBMIT_PRIVATE_KEY` to the new private key, on the server and wherever `fetch` runs.
4. Have the old public key removed from `public_keys`.
