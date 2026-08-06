def all_configured_integrations_failed(
    *,
    tgrass_configured: bool,
    tgrass_result: object,
    botohub_configured: bool,
    botohub_result: object,
    traffy_configured: bool = False,
    traffy_result: object = None,
    flyerhub_configured: bool = False,
    flyerhub_result: object = None,
) -> bool:
    """Fail the wall only when none of the configured providers answered."""
    provider_statuses: list[bool] = []
    if tgrass_configured:
        provider_statuses.append(isinstance(tgrass_result, list))
    if botohub_configured:
        provider_statuses.append(isinstance(botohub_result, list))
    if traffy_configured:
        provider_statuses.append(isinstance(traffy_result, list))
    if flyerhub_configured:
        provider_statuses.append(isinstance(flyerhub_result, list))
    return bool(provider_statuses) and not any(provider_statuses)
