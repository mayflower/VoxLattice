# Model preparation

`manifest.lock.json` pins the official FastEnhancer-B VCTK-Demand release by
GitHub asset ID, byte size, archive hash, member names, and member hashes.

Run `make model`. The archive is stored under ignored `models/cache/`; verified
members are atomically prepared under ignored `models/prepared/`. The server
never downloads at runtime and verifies the checkpoint again before loading.
