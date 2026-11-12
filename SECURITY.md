# Security policy

World Proxy Atlas accepts only public IP literals and uses a conservative proxy port allowlist. It rejects credentials, DNS hostnames, private/reserved/multicast addresses and malformed inputs. The source catalogue rejects reserved example/test domains, non-HTTPS URLs, embedded credentials and unsupported protocol mappings before network access. Source downloads are read completely in bounded chunks and limited by total bytes, line count, time and concurrency; redirects may not downgrade to HTTP. Proxy checks are separately bounded by time and concurrency. The validator sends bounded, low-volume HTTPS GET requests to Google's live `robots.txt` and Cloudflare Trace. It verifies TLS certificates and accepts a candidate only when one endpoint returns HTTP 200 with its expected marker.

Do not use public proxies for credentials, sessions, payment information, API keys or personal data. A proxy passing a connectivity check is not a security endorsement and does not establish anonymity or operator trust.

Report defects involving this repository's own code through GitHub Security Advisories or an appropriate maintainer contact route. Do not publish private information or unverified operator allegations.
