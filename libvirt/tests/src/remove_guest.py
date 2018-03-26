from virttest import error_context


@error_context.context_aware
def run(test, params, env):
    """
    everything is done by client.virt module
    """
    pass
