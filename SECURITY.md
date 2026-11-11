# Security policy

World Proxy Atlas accepts only public IP literals and uses a conservative proxy port allowlist. It rejects credentials, DNS hostnames, private/reserved/multicast addresses and malformed inputs. Source downloads and proxy checks are bounded by byte, line, time and concurrency limits. The validator sends one fixed, low-volume HTTPS request and checks the response marker.

Do not use public proxies for credentials, sessions, payment information, API keys or personal data. A proxy passing a connectivity check is not a security endorsement and does not establish anonymity or operator trust.

Report defects involving this repository's own code through GitHub Security Advisories or an appropriate maintainer contact route. Do not publish private information or unverified operator allegations.
