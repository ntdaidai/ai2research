# Publication validation

The independently prepared public checkout passed 199 isolated regression checks in 34.199 seconds on Python 3.14.4. No research experiments or paid reviews were started. The source scan found no private keys, local deployment addresses, personal home paths, private state databases or copied experiment artifacts in the publication file set.

The live read-only dashboard is deployed with a user-owned systemd service. It returned HTTP 200 after restart. The public `docs/` demo contains explicit synthetic records and does not read the live database. Deployment uses a static HTTP server on the research host; GitHub Pages is not required.

The server also passed backend-free deployment checks: exported snapshots for three projects, static PDF/PNG output requests, and HTTP 404 for the research API.
