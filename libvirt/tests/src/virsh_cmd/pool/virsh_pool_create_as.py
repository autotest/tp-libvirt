import os
import logging

from virttest import libvirt_storage
from virttest import virsh


def run(test, params, env):
    '''
    Test the command virsh pool-create-as

    (1) Call virsh pool-create-as
    (2) Call virsh -c remote_uri pool-create-as
    (3) Call virsh pool-create-as with an unexpected option
    '''

    # Run test case
    if 'pool_name' not in params or 'pool_target' not in params:
        logging.error("Please give a 'name' and 'target'")

    pool_options = params.get('pool_options', '')

    pool_name = params.get('pool_name')
    pool_type = params.get('pool_type')
    pool_target = params.get('pool_target')

    if not os.path.isdir(pool_target):
        if os.path.isfile(pool_target):
            logging.error('<target> must be a directory')
        else:
            os.makedirs(pool_target)

    logging.info('Creating a %s type pool %s', pool_type, pool_name)
    status = virsh.pool_create_as(pool_name, pool_type, pool_target,
                                  extra=pool_options, uri=virsh.canonical_uri())

    # Check status_error
    status_error = params.get('status_error')
    if status_error == 'yes':
        if status:
            test.fail("%d not a expected command return value"
                      % status)
        else:
            logging.info("It's an expected error")
    elif status_error == 'no':
        result = virsh.pool_info(pool_name, uri=virsh.canonical_uri())
        if result.exit_status:
            test.fail('Failed to check pool information')
        else:
            logging.info('Pool %s is running', pool_name)
        if not status:
            test.fail('%d not a expected command return value'
                      % status)
        else:
            logging.info('Succeed to create pool %s', pool_name)
    # Clean up
    libvirt_pool = libvirt_storage.StoragePool()
    if libvirt_pool.pool_exists(pool_name):
        libvirt_pool.delete_pool(pool_name)
