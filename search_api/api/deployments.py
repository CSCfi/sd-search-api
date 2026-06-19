"""Deployment registry mapping a DEPLOYMENT_TYPE to its Domain."""

from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.api.domain import Domain
from search_api.exceptions import SystemException

# Register new deployments here.
DOMAINS: dict[str, Domain] = {
    "Bigpicture": BP_DOMAIN,
}


def get_domain(deployment_type: str) -> Domain:
    """Return the domain registered for a deployment type."""
    try:
        return DOMAINS[deployment_type]
    except KeyError:
        raise SystemException(
            f"Unknown deployment type {deployment_type!r}. "
            f"Registered deployments: {', '.join(sorted(DOMAINS))}."
        )
