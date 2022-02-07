"""
decorators to wrap up function behaviors
"""

import logging

LOG = logging.getLogger('avocado.' + __name__)


def polariondecorator(polarion_id):
    """
    one decorator to output polarion case id when execute specific test case
    method
    usage:
        @polariondecorator("RHEL7-110402")
        def check_multifunction_is_on(vm_name, test):

    :param polarion_id: polarion case id
    """
    def decorator(fun):
        def wrapper(*args, **kwargs):
            if polarion_id:
                LOG.debug("Execute*********************polarion case id:%s", polarion_id)
            return fun(*args, **kwargs)
        return wrapper
    return decorator
