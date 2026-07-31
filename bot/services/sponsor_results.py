def all_configured_integrations_failed(
    *,
    tgrass_configured: bool,
    tgrass_result: object,
    botohub_configured: bool,
    botohub_result: object,
    piarflow_configured: bool = False,
    piarflow_result: object = None,
) -> bool:
    """Fail the wall only when none of the configured providers answered."""
    provider_statuses: list[bool] = []
    if tgrass_configured:
        provider_statuses.append(isinstance(tgrass_result, list))
    if botohub_configured:
        provider_statuses.append(isinstance(botohub_result, list))
    if piarflow_configured:
        provider_statuses.append(isinstance(piarflow_result, list))
    return bool(provider_statuses) and not any(provider_statuses)
